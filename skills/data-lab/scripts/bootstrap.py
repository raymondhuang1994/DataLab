#!/usr/bin/env python3
"""Explicit, cross-platform venv setup. Never installs into the system environment."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True


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
    destination = Path(args.venv).expanduser().absolute()
    if os.path.lexists(destination):
        parser.error("Environment path already exists. Use that environment directly if ready, or choose a new path; bootstrap never updates an existing environment.")
    from datalab.safety import cache_directory, output_session
    requirements = Path(__file__).with_name("requirements.txt")
    with output_session(destination, inputs=[requirements], directory=True) as session:
        session.record_external_writer("venv, ensurepip and pip installation", destination)
        environment = dict(os.environ)
        temporary = str(cache_directory("temporary"))
        environment.update(TMP=temporary, TEMP=temporary, TMPDIR=temporary,
                           PYTHONDONTWRITEBYTECODE="1",
                           MPLCONFIGDIR=str(cache_directory("matplotlib")))
        for variable in ("PYTHONHOME", "PYTHONPATH"):
            environment.pop(variable, None)
        # A separate interpreter keeps venv/ensurepip's environment changes out
        # of the calling process, and all install targets are new task files.
        subprocess.run([sys.executable, "-m", "venv", str(destination)],
                       check=True, env=environment, timeout=180)
        interpreter = destination / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        pip = [str(interpreter), "-m", "pip", "--isolated", "--require-virtualenv", "install",
               "--no-cache-dir", "--disable-pip-version-check"]
        subprocess.run([*pip, "-r", str(requirements)], check=True, env=environment, timeout=900)
        extras = (["pyarrow>=16"] if args.parquet else []) + (["plotly>=5.22,<7"] if args.interactive else [])
        if extras:
            subprocess.run([*pip, *extras], check=True, env=environment, timeout=900)
        subprocess.run([str(interpreter), str(Path(__file__).with_name("run.py")), "doctor"],
                       check=True, env=environment, timeout=60)
    print(f"Python executable: {interpreter}")


if __name__ == "__main__":
    main()
