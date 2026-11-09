#!/usr/bin/env python3
"""Create reproducible source/plugin and standalone-skill ZIPs without local artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

EXCLUDED = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist", "demo-output", "benchmark-output"}


def package(source, destination, prefix):
    files = [p for p in source.rglob("*") if p.is_file() and not any(x in EXCLUDED or x.endswith(".egg-info") for x in p.relative_to(source).parts) and p.suffix not in {".pyc", ".pyo"} and p.name != ".DS_Store"]
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            relative = path.relative_to(source)
            info = zipfile.ZipInfo((Path(prefix) / relative).as_posix(), date_time=(2026, 11, 8, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return {"file": destination.name, "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "files": len(files), "bytes": destination.stat().st_size}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.out).resolve()
    if out.is_relative_to(root):
        parser.error("Choose an output directory outside the source tree.")
    subprocess.run([sys.executable, str(root / "tools/check_package.py")], check=True)
    out.mkdir(parents=True, exist_ok=True)
    version = json.loads((root / "plugin.json").read_text())["version"]
    results = [package(root, out / f"DataLab-{version}.zip", "DataLab"),
               package(root / "skills/data-lab", out / f"data-lab-skill-{version}.zip", "data-lab")]
    (out / "SHA256SUMS.txt").write_text("".join(f"{r['sha256']}  {r['file']}\n" for r in results), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
