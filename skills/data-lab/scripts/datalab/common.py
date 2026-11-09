"""Portable JSON and provenance utilities."""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_safe(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (ValueError, TypeError):
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def safe_bundle_path(base, relative):
    base = Path(base).resolve()
    candidate = (base / relative).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError(f"成果文件必须位于成果目录内: {relative}")
    return candidate


def guard_output_path(input_path, data, output_path):
    """Reject destinations aliasing an input or its declared file resources.

    Relative resource references are resolved beside the input document. This
    also works for a plain source file by passing an empty metadata dictionary.
    """
    input_path = Path(input_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    protected = {input_path}

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key in {"path", "source_file", "svg_path"}
                        or key.endswith("_path") or key.endswith("_file")):
                    if isinstance(item, str) and item and "://" not in item:
                        resource = Path(item).expanduser()
                        if not resource.is_absolute():
                            resource = input_path.parent / resource
                        protected.add(resource.resolve())
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(data)
    for resource in protected:
        identical = output == resource
        if not identical and output.exists() and resource.exists():
            # resolve() catches symlinks; samefile() also catches hard links.
            try:
                identical = output.samefile(resource)
            except OSError:
                pass
        if identical:
            raise ValueError(f"输出不能覆盖输入文件、源数据或成果资源文件: {resource}")
    return output
