#!/usr/bin/env python3
"""Copy the complete DataLab skill into an explicitly selected skill directory."""
import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/data-lab/scripts"))
    from datalab.common import configure_utf8_stdio
    from datalab.safety import output_session, safe_write_bytes
    configure_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", required=True, help="Existing/new parent directory used by your host for skills")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = Path(args.dest).expanduser().resolve() / "data-lab"
    if target.exists():
        parser.error(f"Already exists: {target}. Review/move the previous version before updating.")
    source = root / "skills/data-lab"
    files = []
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in {"__pycache__", ".venv", ".DS_Store"} for part in relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            parser.error(f"Skill source contains a symlink or directory junction: {relative}")
        if path.is_file():
            files.append(path)
    with output_session(target, inputs=files, raw_inputs=files, directory=True):
        for path in files:
            safe_write_bytes(target / path.relative_to(source), path.read_bytes())
    print(f"Copied skill: {target}")
    print("Reload your host's skill discovery. Python dependencies are not installed by this copy.")


if __name__ == "__main__":
    main()
