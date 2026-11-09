#!/usr/bin/env python3
"""Explicit, cross-platform venv setup. Never installs into the system environment."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv


def main():
    from datalab.common import configure_utf8_stdio
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Prepare DataLab's isolated Python environment")
    parser.add_argument("--venv", required=True, help="An explicit writable environment directory")
    parser.add_argument("--parquet", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("DataLab requires Python 3.11 or newer; select that interpreter to run bootstrap.")
    destination = Path(args.venv).expanduser().resolve()
    if destination.exists() and not (destination / "pyvenv.cfg").is_file():
        parser.error("Destination exists and is not a Python virtual environment; choose a new path.")
    if not destination.exists():
        venv.EnvBuilder(with_pip=True).create(destination)
    interpreter = destination / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(interpreter), "-m", "pip", "install", "-r", str(Path(__file__).with_name("requirements.txt"))], check=True)
    extras = (["pyarrow>=16"] if args.parquet else []) + (["plotly>=5.22,<7"] if args.interactive else [])
    if extras:
        subprocess.run([str(interpreter), "-m", "pip", "install", *extras], check=True)
    subprocess.run([str(interpreter), str(Path(__file__).with_name("run.py")), "doctor"], check=True)
    print(f"Python executable: {interpreter}")


if __name__ == "__main__":
    main()
