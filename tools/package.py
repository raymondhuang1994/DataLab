#!/usr/bin/env python3
"""Create reproducible source/plugin and standalone-skill ZIPs without local artifacts."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/data-lab/scripts"))
from datalab.safety import output_session, safe_write_bytes, safe_write_text

EXCLUDED = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist", "work", "demo-output", "benchmark-output"}


def package(source, destination, prefix):
    source, destination = Path(source), Path(destination)
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("Package output must be outside the source tree.")
    files = package_files(source)
    with output_session(destination, inputs=files):
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                relative = path.relative_to(source)
                info = zipfile.ZipInfo((Path(prefix) / relative).as_posix(), date_time=(2026, 11, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
        safe_write_bytes(destination, payload.getvalue())
    return {"file": destination.name, "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "files": len(files), "bytes": destination.stat().st_size}


def package_files(source):
    """Never follow a package link into unrelated/private source material."""
    files = []
    for path in [source, *source.rglob("*")]:
        relative = path.relative_to(source)
        if any(x in EXCLUDED or x.endswith(".egg-info") for x in relative.parts):
            continue
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Package source contains a symlink or directory junction: {relative}")
        if path.is_file() and path.suffix not in {".pyc", ".pyo"} and path.name != ".DS_Store":
            files.append(path)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.out).resolve()
    if out.is_relative_to(root):
        parser.error("Choose an output directory outside the source tree.")
    subprocess.run([sys.executable, str(root / "tools/check_package.py")], check=True)
    version = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"]
    with output_session(out, inputs=package_files(root), directory=True):
        results = [package(root, out / f"DataLab-{version}.zip", "DataLab"),
                   package(root / "skills/data-lab", out / f"data-lab-skill-{version}.zip", "data-lab")]
        safe_write_text(out / "SHA256SUMS.txt", "".join(f"{r['sha256']}  {r['file']}\n" for r in results), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
