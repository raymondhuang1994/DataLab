#!/usr/bin/env python3
"""Copy the complete DataLab skill into an explicitly selected skill directory."""
import argparse
from pathlib import Path
import shutil
import sys


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/data-lab/scripts"))
    from datalab.common import configure_utf8_stdio
    configure_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", required=True, help="Existing/new parent directory used by your host for skills")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = Path(args.dest).expanduser().resolve() / "data-lab"
    if target.exists():
        parser.error(f"Already exists: {target}. Review/move the previous version before updating.")
    shutil.copytree(root / "skills/data-lab", target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".DS_Store"))
    print(f"Copied skill: {target}")
    print("Reload your host's skill discovery. Python dependencies are not installed by this copy.")


if __name__ == "__main__":
    main()
