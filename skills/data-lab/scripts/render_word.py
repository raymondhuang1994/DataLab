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
from datalab.safety import cache_directory, current_session, output_session, safe_write_bytes, safe_write_text


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
    requested_input = input_path.expanduser().absolute()
    requested_output = output_dir.expanduser().absolute()
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
    with output_session(requested_output, raw_inputs=[requested_input], directory=True):
        return _render_word(input_path, output_dir, soffice, make_png, dpi, timeout)


def _render_word(input_path: Path, output_dir: Path, soffice: str,
                 make_png: bool, dpi: int, timeout: int) -> dict:
    """Give external renderers a task-owned copy, never the original DOCX."""
    session = current_session()
    pdf_path = output_dir / f"{input_path.stem}.pdf"
    source_copy = cache_directory("word-source") / "source.docx"
    rendered = cache_directory("word-render")
    profile = cache_directory("libreoffice")
    safe_write_bytes(source_copy, input_path.read_bytes())
    session.record_external_writer("LibreOffice conversion of task-owned DOCX copy", session.root)
    # A task-owned profile isolates conversion from an already open LibreOffice.
    # Font settings are inherited, while original files are never passed to it.
    run_command([
        soffice, f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--headless", "--convert-to", "pdf:writer_pdf_Export",
        "--outdir", str(rendered), str(source_copy),
    ], timeout)
    session.adopt_tree(rendered)
    session.adopt_tree(profile)
    rendered_pdf = rendered / "source.pdf"
    if not rendered_pdf.is_file() or rendered_pdf.stat().st_size == 0:
        raise RuntimeError("LibreOffice did not produce a PDF. Check the input and installed fonts.")
    safe_write_bytes(pdf_path, rendered_pdf.read_bytes())
    renderer = None
    if make_png:
        poppler = shutil.which("pdftoppm")
        if poppler:
            renderer = "pdftoppm"
            session.record_external_writer("pdftoppm page rendering", rendered)
            run_command([poppler, "-png", "-r", str(dpi), str(pdf_path),
                         str(rendered / "page")], timeout)
            session.adopt_tree(rendered)
            for page in rendered.glob("page-*.png"):
                safe_write_bytes(output_dir / page.name, page.read_bytes())
        elif importlib.util.find_spec("pymupdf"):
            renderer = "PyMuPDF"
            import pymupdf

            with pymupdf.open(pdf_path) as document:
                for index, page in enumerate(document):
                    safe_write_bytes(output_dir / f"page-{index + 1}.png", page.get_pixmap(dpi=dpi).tobytes("png"))
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
        "protection": {"mode": "software_guards", "renderer_input": "task_owned_copy",
                       "os_isolation_verified": False},
        "next_step": "Inspect every rendered page for fonts, clipping, tables, charts and page numbers.",
    }
    safe_write_text(output_dir / "render.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
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
