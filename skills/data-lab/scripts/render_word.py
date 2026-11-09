#!/usr/bin/env python3
"""Render a DOCX locally for visual inspection; rendering is not review.

Uses only the Python standard library for PDF conversion. Optional page images
require Poppler's pdftoppm or PyMuPDF. No Microsoft Word automation is required.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def find_soffice(override: str | None = None) -> str:
    """Find an executable without changing PATH or the machine configuration."""
    if override:
        candidate = Path(override).expanduser()
        found = str(candidate.resolve()) if candidate.is_file() else shutil.which(override)
        if found:
            return found
        raise ValueError(f"LibreOffice executable not found: {override}")
    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "LibreOffice/program/soffice.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ValueError("LibreOffice was not found. Install LibreOffice or pass --soffice PATH.")


def run_command(arguments: list[str], timeout: int) -> None:
    """Run an argument list, never a shell command."""
    result = subprocess.run(
        arguments, capture_output=True, text=True, errors="replace", timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(f"{Path(arguments[0]).name} exited {result.returncode}: {detail}")


def render_word(input_path: Path, output_dir: Path, soffice: str,
                make_png: bool = False, dpi: int = 150, timeout: int = 180) -> dict:
    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not input_path.is_file() or input_path.suffix.lower() != ".docx":
        raise ValueError("--input must be an existing .docx file.")
    if output_dir.exists():
        raise ValueError("--out must be a new directory; existing output is never overwritten.")
    if not 72 <= dpi <= 300:
        raise ValueError("--dpi must be between 72 and 300.")
    if timeout < 1:
        raise ValueError("--timeout must be positive.")
    output_dir.mkdir(parents=True, exist_ok=False)
    pdf_path = output_dir / f"{input_path.stem}.pdf"
    # A unique profile isolates the conversion from an already open LibreOffice.
    # This helper intentionally inherits, and never rewrites, the font environment.
    with tempfile.TemporaryDirectory(prefix="datalab-lo-") as profile:
        run_command([
            soffice, f"-env:UserInstallation={Path(profile).resolve().as_uri()}",
            "--headless", "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(output_dir), str(input_path),
        ], timeout)
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError("LibreOffice did not produce a PDF. Check the input and installed fonts.")
    renderer = None
    if make_png:
        poppler = shutil.which("pdftoppm")
        if poppler:
            renderer = "pdftoppm"
            run_command([poppler, "-png", "-r", str(dpi), str(pdf_path),
                         str(output_dir / "page")], timeout)
        elif importlib.util.find_spec("pymupdf"):
            renderer = "PyMuPDF"
            import pymupdf

            with pymupdf.open(pdf_path) as document:
                for index, page in enumerate(document):
                    page.get_pixmap(dpi=dpi).save(output_dir / f"page-{index + 1}.png")
        else:
            raise RuntimeError(
                f"PDF retained at {pdf_path}. PNG rendering requires pdftoppm (Poppler) "
                "or PyMuPDF. Install one, or inspect the PDF directly."
            )
    pages = sorted(output_dir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    if make_png and not pages:
        raise RuntimeError(f"No page images were produced. PDF retained at {pdf_path}.")
    result = {
        "input": str(input_path), "pdf": str(pdf_path),
        "pages": [str(page) for page in pages], "png_renderer": renderer,
        "visual_qa": "not_reviewed",
        "next_step": "Inspect every rendered page for fonts, clipping, tables, charts and page numbers.",
    }
    (output_dir / "render.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return result


def main() -> int:
    from datalab.common import configure_utf8_stdio
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input DOCX")
    parser.add_argument("--out", required=True, type=Path, help="New output directory")
    parser.add_argument("--soffice", help="Explicit LibreOffice executable path")
    parser.add_argument("--png", action="store_true", help="Also render each PDF page as PNG")
    parser.add_argument("--dpi", type=int, default=150, help="PNG resolution, 72–300 (default: 150)")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per conversion process")
    args = parser.parse_args()
    try:
        result = render_word(args.input, args.out, find_soffice(args.soffice),
                             args.png, args.dpi, args.timeout)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Word rendering failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
