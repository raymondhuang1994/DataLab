"""Scoped, non-destructive writes for bundled tools, not an OS sandbox.

Existing inputs and output files are never adopted as writable. Only files
created by the current in-memory session may be updated. Arbitrary third-party
code still needs host-enforced read-only input permissions.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import uuid
from .secure_io import locked_directory, locked_file


_ACTIVE = ContextVar("datalab_write_session", default=None)


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino)


def _link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _absolute(path):
    return Path(os.path.abspath(Path(path).expanduser()))


def _snapshot(path):
    if not path.exists():
        return {"exists": False, "sha256": None}
    if not path.is_file():
        raise ValueError(f"输入必须是普通文件: {path}")
    before = path.stat()
    digest = _digest(path)
    after = path.stat()
    if (_identity(before), before.st_size, before.st_mtime_ns) != (_identity(after), after.st_size, after.st_mtime_ns):
        raise ValueError(f"读取期间输入发生变化，请固定快照后重试: {path}")
    return {"exists": True, "sha256": digest, "bytes": after.st_size,
            "mode": stat.S_IMODE(after.st_mode), "mtime_ns": after.st_mtime_ns,
            "identity": list(_identity(after))}


class WriteSession:
    def __init__(self, output, *, inputs=(), raw_inputs=(), directory=False):
        self.requested = _absolute(output)
        if _link(self.requested):
            raise ValueError(f"输出不能覆盖或使用链接/目录联接: {self.requested}")
        self.output = self.requested.resolve()
        self.root = self.output if directory else self.output.parent
        self.directory = directory
        self.run_id = uuid.uuid4().hex
        self.inputs = {}
        self.raw_directories = set()
        self.raw_directory_identities = set()
        self.owned = {}
        self.new_directories = {}
        self.root_identity = None
        self.audit_path = None
        self.environment = {}
        self.external_writers = []
        for path in inputs:
            self.register_input(path)
        for path in raw_inputs:
            self.register_input(path, raw=True)

    def register_input(self, path, *, raw=False):
        if path is None:
            return
        requested = _absolute(path)
        resolved = requested.resolve()
        if resolved in self.owned:
            return  # Generated intermediate, not a pre-existing input.
        if requested not in self.inputs:
            self.inputs[requested] = {"path": str(requested), "resolved_path": str(resolved),
                                      "before": _snapshot(requested), "role": "input"}
        record = self.inputs[requested]
        if raw:
            record["role"] = "raw_data"
            if record["before"]["exists"]:
                self.raw_directories.add(resolved.parent)
                self.raw_directories.add(requested.parent.resolve())
                self.raw_directory_identities.add(_identity(resolved.parent.stat()))
                self.raw_directory_identities.add(_identity(requested.parent.stat()))
        if self.root_identity is not None:
            self._check_scope(self.root)

    def _check_scope(self, path):
        for folder in self.raw_directories:
            if path == folder or path.is_relative_to(folder):
                raise ValueError(f"输出不能写入原始数据目录及其子目录；请选择独立成果位置: {folder}")
        for record in self.inputs.values():
            protected = Path(record["resolved_path"])
            if path == protected:
                raise ValueError(f"输出不能覆盖输入文件: {protected}")
            if path.exists() and protected.exists() and path.is_file() and protected.is_file():
                if path.samefile(protected):
                    raise ValueError(f"输出不能覆盖输入文件的链接: {protected}")

    def _verify_root(self):
        if self.root_identity is not None:
            if _link(self.root) or not self.root.is_dir() or _identity(self.root.stat()) != self.root_identity:
                raise ValueError("成果目录在运行期间被替换或变成链接，已拒绝继续写入。")

    def path(self, destination):
        self._verify_root()
        candidate = _absolute(destination)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"写入必须位于本次成果目录内: {self.root}")
        self._check_scope(resolved)
        # Inspect lexical components below the resolved root as well as targets.
        if candidate.is_relative_to(self.root):
            part = self.root
            for component in candidate.relative_to(self.root).parts:
                part = part / component
                if _link(part):
                    raise ValueError(f"输出不能使用链接/目录联接: {part}")
        if _link(candidate):
            raise ValueError(f"输出不能覆盖链接: {candidate}")
        return resolved

    def ensure_directory(self, destination):
        path = self.path(destination)
        with locked_directory(path, expected=self._expected_directories(),
                              forbidden=self.raw_directory_identities, create=True,
                              created=self.new_directories):
            pass
        self.path(path)
        return path

    def _expected_directories(self):
        expected = dict(self.new_directories)
        if self.root_identity is not None:
            expected[self.root] = self.root_identity
        return expected

    def start(self):
        self._check_scope(self.root)
        self._check_scope(self.output)
        if self.output.exists() and (self.directory or not self.output.is_dir()):
            raise ValueError(f"输出已有文件或目录，不能覆盖；请选择全新路径: {self.output}")
        if not self.directory and self.output.exists():
            raise ValueError(f"输出已有内容，不能覆盖: {self.output}")
        with locked_directory(self.root, forbidden=self.raw_directory_identities,
                              create=True, exclusive=self.directory,
                              created=self.new_directories) as (_, info):
            self.root_identity = _identity(info)
        return self

    def write_bytes(self, destination, value):
        path = self.path(destination)
        self.ensure_directory(path.parent)
        prior = self.owned.get(path)
        try:
            with locked_file(path, create=prior is None, expected=self._expected_directories(),
                             forbidden=self.raw_directory_identities) as stream:
                info = os.fstat(stream.fileno())
                if prior is not None and (_identity(info) != prior["identity"] or info.st_nlink != 1):
                    raise ValueError(f"写入目标已被替换，拒绝更新: {path}")
                if prior is not None and hashlib.sha256(stream.read()).hexdigest() != prior["sha256"]:
                    raise ValueError(f"写入目标已被修改，拒绝更新: {path}")
                stream.seek(0)
                stream.write(value)
                stream.truncate()
                stream.flush()
                info = os.fstat(stream.fileno())
        except FileExistsError as exc:
            raise ValueError(f"输出已有文件，不能覆盖: {path}") from exc
        self.owned[path] = {"identity": _identity(info), "sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
        return path

    def adopt_generated(self, destination):
        """Record an external renderer's output only inside our new directory."""
        path = self.path(destination)
        if not any(path.is_relative_to(folder) and folder.is_dir() and _identity(folder.stat()) == identity
                   for folder, identity in self.new_directories.items() if folder.is_relative_to(self.root)):
            raise ValueError("不能接管任务开始前存在目录中的外部产物。")
        if _link(path) or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError(f"外部产物必须是无链接的普通文件: {path}")
        if path not in self.owned:
            info = path.stat()
            self.owned[path] = {"identity": _identity(info), "sha256": _digest(path), "bytes": info.st_size}
        else:
            prior = self.owned[path]
            if _identity(path.stat()) != prior["identity"] or _digest(path) != prior["sha256"]:
                raise ValueError(f"外部程序修改了已记录的产物，不能接管: {path}")
        return path

    def adopt_tree(self, directory):
        folder = self.path(directory)
        for path in folder.rglob("*"):
            if _link(path):
                raise ValueError(f"外部产物目录包含链接: {path}")
            if path.is_file():
                self.adopt_generated(path)

    def record_external_writer(self, name, scope):
        scope = self.path(scope)
        self.external_writers.append({"name": name, "intended_scope": str(scope),
                                      "os_enforced": False})

    def finish(self, error=None):
        changed = []
        records = []
        for requested, original in self.inputs.items():
            record = dict(original)
            try:
                after = _snapshot(requested)
                same = (after == original["before"] and str(requested.resolve()) == original["resolved_path"])
                record.update(after=after, status=("unchanged" if after["exists"] else "unavailable") if same else "changed")
            except (ValueError, OSError) as exc:
                record.update(after=None, status="changed", error=str(exc))
            if record["status"] == "changed":
                changed.append(record["path"])
            records.append(record)
        outputs = []
        changed_outputs = []
        for path, expected in sorted(self.owned.items()):
            record = {"path": str(path), "sha256": expected["sha256"], "bytes": expected["bytes"]}
            try:
                self.path(path)
                after = _snapshot(path)
                same = (after["exists"] and tuple(after["identity"]) == expected["identity"]
                        and after["sha256"] == expected["sha256"] and path.stat().st_nlink == 1)
                record["status"] = "unchanged" if same else "changed"
                record["final_sha256"] = after["sha256"]
            except (ValueError, OSError):
                record.update(status="changed", final_sha256=None)
            if record["status"] == "changed":
                changed_outputs.append(str(path))
            outputs.append(record)
        audit = {"schema_version": "1.0", "run_id": self.run_id,
                 "created_at": datetime.now(timezone.utc).isoformat(),
                 "protection": "software_guards", "os_read_only_enforced": False,
                 "limitations": ["Guards apply to bundled writes, not arbitrary scripts or external processes.",
                                 "Hashes detect changes but do not identify who changed a file.",
                                 "Host read-only input permissions are required for enforced isolation."],
                 "status": "failed" if error or changed or changed_outputs else "completed",
                 "error": str(error) if error else None, "inputs": records,
                 "external_writers": self.external_writers,
                 "outputs": outputs}
        self.audit_path = self.root / "audit" / f"file-safety-{self.run_id}.json"
        self.write_bytes(self.audit_path, (json.dumps(audit, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if changed:
            raise ValueError("运行期间输入发生变化；已记录失败，不会恢复或写回原件: " + ", ".join(changed))
        if changed_outputs:
            raise ValueError("本次产物在运行期间被外部修改；已记录失败，不会覆盖: " + ", ".join(changed_outputs))


def current_session():
    return _ACTIVE.get()


def register_input(path, raw=False):
    session = current_session()
    if session is not None:
        session.register_input(path, raw=raw)


@contextmanager
def output_session(output, *, inputs=(), raw_inputs=(), directory=False):
    active = current_session()
    if active is not None:
        for path in inputs:
            active.register_input(path)
        for path in raw_inputs:
            active.register_input(path, raw=True)
        path = active.path(output)
        if directory:
            active.ensure_directory(path)
        yield active
        return
    session = WriteSession(output, inputs=inputs, raw_inputs=raw_inputs, directory=directory).start()
    token = _ACTIVE.set(session)
    error = None
    try:
        yield session
    except BaseException as exc:
        error = exc
        raise
    finally:
        try:
            session.finish(error)
        finally:
            _ACTIVE.reset(token)


def safe_write_bytes(path, value):
    session = current_session()
    if session is not None:
        return session.write_bytes(path, bytes(value))
    with output_session(path) as session:
        return session.write_bytes(path, bytes(value))


def safe_write_text(path, text, encoding="utf-8"):
    return safe_write_bytes(path, text.encode(encoding))


@contextmanager
def safe_open(path, mode="w", encoding="utf-8", newline=None):
    if mode not in {"w", "wb", "x", "xb"}:
        raise ValueError("安全写入仅支持新建/本次任务拥有文件的 w/wb/x/xb 模式。")
    binary = "b" in mode
    buffer = io.BytesIO() if binary else io.StringIO(newline=newline)
    try:
        yield buffer
        value = buffer.getvalue()
        safe_write_bytes(path, value if binary else value.encode(encoding))
    finally:
        buffer.close()


def cache_directory(name):
    session = current_session()
    if session is None:
        raise ValueError("缓存需要显式任务工作区；请通过 DataLab 命令或 output_session 执行。")
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("缓存名称必须是单个目录名。")
    return session.ensure_directory(session.root / "work" / "cache" / session.run_id / name)


def bundle_inputs(bundle_path, data):
    bundle_path = Path(bundle_path).expanduser().resolve()
    paths = {bundle_path}
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key in {"path", "source_file", "svg_path"} or key.endswith("_path") or key.endswith("_file")) and isinstance(item, str) and item and "://" not in item:
                    path = Path(item).expanduser()
                    paths.add(path.resolve() if path.is_absolute() else (bundle_path.parent / path).resolve())
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(data)
    return sorted(paths)
