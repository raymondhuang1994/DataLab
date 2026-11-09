"""Export a DataLab result bundle as an editable, portable Word report.

Only local raster charts inside the result bundle are embedded. Input strings
are treated as text, never as HTML or OOXML. Rendering is a separate QA step.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from typing import Any
from .common import guard_output_path


def _text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "—"
        # Formatting belongs to the analysis layer. Do not silently round two
        # different computed values into the same displayed report number.
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    result = str(value)
    # XML 1.0 disallows most ASCII control characters.
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", result):
        raise ValueError("报告文本包含 XML 不支持的控制字符，请先清理输入。")
    return result


def _heading_text(value: Any) -> str:
    text = _text(value)
    # Descriptive report headings have no decorative punctuation. Body text is
    # never normalized this way, so source expressions and caveats are preserved.
    return " ".join("".join(" " if unicodedata.category(c).startswith("P") else c
                            for c in text).split()) or "数据分析报告"


def _list(bundle: dict, key: str) -> list:
    value = bundle.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} 必须是数组。")
    return value


def _validate_bundle(bundle: Any, base: Path) -> tuple[dict, list[Path]]:
    if not isinstance(bundle, dict):
        raise ValueError("result.json 必须是对象。")
    if str(bundle.get("schema_version", "")) not in ("1.0", "1.1"):
        raise ValueError("Word 导出要求 schema_version 为 1.0 或 1.1。")
    if not isinstance(bundle.get("title"), str) or not bundle["title"].strip():
        raise ValueError("result.json 需要非空 title。")
    for key in ("summary", "findings", "tables", "charts", "limitations", "methodology"):
        _list(bundle, key)
    for table in bundle.get("tables", []):
        if not isinstance(table, dict):
            raise ValueError("tables 中每项必须是对象。")
        columns, rows = table.get("columns"), table.get("rows")
        if not isinstance(columns, list) or not columns or len(columns) > 12:
            raise ValueError("每张 Word 表格需要 1–12 个字段；更多字段请先选择或拆分。")
        if not isinstance(rows, list) or any(not isinstance(r, list) or len(r) != len(columns) for r in rows):
            raise ValueError("表格每行的值数量必须与 columns 一致。")
    for item in bundle.get("findings", []):
        if not isinstance(item, dict) or not isinstance(item.get("text", ""), str):
            raise ValueError("findings 需要含 text 的对象。")
    for key in ("source", "profile", "validation"):
        if key in bundle and not isinstance(bundle[key], dict):
            raise ValueError(f"{key} 必须是对象。")
    checks = bundle.get("validation", {}).get("checks", [])
    if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
        raise ValueError("validation.checks 必须是对象数组。")
    chart_paths = []
    for chart in bundle.get("charts", []):
        if not isinstance(chart, dict) or not isinstance(chart.get("path"), str):
            raise ValueError("charts 需要含 path 的对象。")
        path = (base / chart["path"]).resolve()
        if not path.is_relative_to(base):
            raise ValueError("图表路径必须位于结果包目录内。")
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ValueError("Word 图表仅接受 PNG/JPEG；请先将 SVG/HTML 导出为图片。")
        if not path.is_file():
            raise FileNotFoundError(f"找不到图表：{chart['path']}")
        chart_paths.append(path)
    return bundle, chart_paths


def _fonts() -> tuple[str, str]:
    cjk = os.environ.get("DATALAB_CJK_FONT")
    if not cjk:
        cjk = "Microsoft YaHei" if sys.platform == "win32" else "PingFang SC" if sys.platform == "darwin" else "Noto Sans CJK SC"
    return "Arial", cjk


def _apply_font(run_or_style: Any, latin: str, cjk: str, size: float | None = None) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    run_or_style.font.name = latin
    if size is not None:
        run_or_style.font.size = Pt(size)
    run_or_style.font.color.rgb = RGBColor(0, 0, 0)
    element = run_or_style._element
    rpr = element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attr in ("ascii", "hAnsi", "cs"):
        fonts.set(qn(f"w:{attr}"), latin)
    fonts.set(qn("w:eastAsia"), cjk)
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(qn(f"w:{attr}"), None)


def _style_document(document: Any, latin: str, cjk: str) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(20), Mm(20)
    section.left_margin, section.right_margin = Mm(22), Mm(22)
    section.footer_distance = Mm(10)
    styles = document.styles
    for name, size in (("Normal", 11), ("Title", 23), ("Subtitle", 11), ("Heading 1", 15),
                       ("Heading 2", 12), ("Caption", 9), ("List Bullet", 11)):
        style = styles[name]
        _apply_font(style, latin, cjk, size)
        style.font.italic = False
        style.font.bold = name.startswith("Heading")
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.line_spacing = 1.18
        if name.startswith("Heading"):
            style.paragraph_format.space_before = Pt(14)
            style.paragraph_format.keep_with_next = True
        if name == "Title":
            style.paragraph_format.space_after = Pt(10)
            style.paragraph_format.keep_with_next = True
        ppr = style._element.find(qn("w:pPr"))
        if ppr is not None:
            for border in ppr.findall(qn("w:pBdr")):
                ppr.remove(border)
    # Fixed page field only; no external links or macro fields.
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("DataLab  ·  ")
    _apply_font(run, latin, cjk, 9)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    document.core_properties.author = "DataLab"
    document.core_properties.last_modified_by = "DataLab"


def _add_table(document: Any, columns: list, rows: list[list], width_mm: float,
               latin: str, cjk: str) -> None:
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt
    table = document.add_table(rows=1, cols=len(columns))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    # Estimate display width from a bounded sample; descriptive columns get more
    # width than compact numerical fields. Values themselves are never truncated.
    def units(value: Any) -> int:
        return max(1, sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in _text(value)))
    weights = []
    for i, col in enumerate(columns):
        lengths = sorted(units(row[i]) for row in rows[:200])
        typical = lengths[int((len(lengths) - 1) * .85)] if lengths else 1
        weights.append(min(36, max(7, units(col), typical)))
    total = sum(weights)
    widths = [width_mm * weight / total for weight in weights]
    for column, width in zip(table.columns, widths):
        column.width = Mm(width)
    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{side}")
        for key, val in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
            border.set(qn(f"w:{key}"), val)
        borders.append(border)
    properties.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for side, size in (("top", "85"), ("bottom", "85"), ("left", "100"), ("right", "100")):
        margin = OxmlElement(f"w:{side}")
        margin.set(qn("w:w"), size)
        margin.set(qn("w:type"), "dxa")
        margins.append(margin)
    properties.append(margins)
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    records = [columns] + rows
    for index, values in enumerate(records):
        cells = table.rows[0].cells if index == 0 else table.add_row().cells
        for ci, (cell, value) in enumerate(zip(cells, values)):
            cell.width = Mm(widths[ci])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.12
            p.paragraph_format.keep_together = False
            p.paragraph_format.keep_with_next = index == 0
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if index and isinstance(value, (int, float)) and not isinstance(value, bool) else WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(_text(value))
            _apply_font(run, latin, cjk, 9 if len(columns) > 6 else 10)
            run.bold = index == 0
            if index == 0:
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "E7EFF5")
                cell._tc.get_or_add_tcPr().append(shade)
    spacer = document.add_paragraph()
    spacer.paragraph_format.space_after = Pt(3)
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.line_spacing = .4


def export_word(bundle_path: str | Path, output_path: str | Path) -> dict:
    """Create a DOCX from a schema 1.0 bundle; never claim visual QA happened.

    Raises ValueError for malformed/unsafe inputs and FileNotFoundError for absent
    charts. The destination is atomically replaced only after successful save.
    """
    from docx import Document
    from docx.enum.section import WD_SECTION_START, WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Mm, Pt
    from PIL import Image
    source_path = Path(bundle_path).expanduser().resolve()
    raw = json.loads(source_path.read_text(encoding="utf-8-sig"))
    output = guard_output_path(source_path, raw, output_path)
    if output.suffix.lower() != ".docx":
        raise ValueError("Word 输出文件必须使用 .docx 扩展名。")
    bundle, chart_paths = _validate_bundle(raw, source_path.parent)
    # Verify image decodability before creating any output.
    dimensions = []
    for chart_path in chart_paths:
        with Image.open(chart_path) as image:
            dimensions.append(image.size)
            image.verify()
    document = Document()
    latin, cjk = _fonts()
    preferences = bundle.get("preferences", {})
    report_preferences = preferences.get("report", {})
    cjk = report_preferences.get("cjk_font") or cjk
    page_size = report_preferences.get("page_size", "A4")
    if page_size not in ("A4", "Letter"):
        raise ValueError("Word page_size 只支持 A4 或 Letter。")
    page_width, page_height = (210, 297) if page_size == "A4" else (215.9, 279.4)
    portrait_content, landscape_content = page_width - 44, page_height - 44
    _style_document(document, latin, cjk)
    document.sections[0].page_width, document.sections[0].page_height = Mm(page_width), Mm(page_height)
    title = _heading_text(bundle["title"])
    document.core_properties.title = title
    document.add_paragraph(title, style="Title")
    source = bundle.get("source", {})
    meta = []
    if bundle.get("created_at"):
        meta.append("生成时间 " + _text(bundle["created_at"]))
    if source.get("path"):
        meta.append("数据来源 " + _text(source["path"]))
    if source.get("rows") is not None:
        count = source.get("columns")
        column_count = len(count) if isinstance(count, list) else count
        meta.append(f"数据规模 {_text(source['rows'])} 行" + (f" × {_text(column_count)} 列" if column_count is not None else ""))
    for line in meta:
        document.add_paragraph(line, style="Subtitle")
    summary = bundle.get("summary", [])
    if summary:
        document.add_heading("主要结论", level=1)
        for statement in summary:
            document.add_paragraph(_text(statement))
    else:
        document.add_paragraph("本报告汇集本次分析的结果、计算方法与数据限制，供核对指标和后续决策使用。")
    findings = bundle.get("findings", [])
    if findings:
        document.add_heading("分析发现", level=1)
        for finding in findings:
            if finding.get("title"):
                document.add_heading(_heading_text(finding["title"]), level=2)
            document.add_paragraph(_text(finding.get("text", "")))
            if finding.get("evidence"):
                document.add_paragraph("依据 " + _text(finding["evidence"]), style="Caption")
    if bundle.get("charts"):
        document.add_heading("图表", level=1)
        for chart, path, (width, height) in zip(bundle["charts"], chart_paths, dimensions):
            if chart.get("title"):
                document.add_heading(_heading_text(chart["title"]), level=2)
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.keep_with_next = bool(chart.get("caption"))
            # Bound both dimensions while preserving the original aspect ratio.
            width_inches = min(portrait_content / 25.4, (180 / 25.4) * width / height)
            inline = paragraph.add_run().add_picture(str(path), width=Inches(width_inches))
            inline._inline.docPr.set("descr", _text(chart.get("caption") or chart.get("title") or path.stem))
            if chart.get("caption"):
                document.add_paragraph(_text(chart["caption"]), style="Caption")
    if bundle.get("tables"):
        document.add_heading("数据表", level=1)
        landscape = False
        for item in bundle["tables"]:
            use_landscape = len(item["columns"]) > 6
            if use_landscape != landscape:
                section = document.add_section(WD_SECTION_START.NEW_PAGE)
                section.orientation = WD_ORIENT.LANDSCAPE if use_landscape else WD_ORIENT.PORTRAIT
                section.page_width, section.page_height = (Mm(page_height), Mm(page_width)) if use_landscape else (Mm(page_width), Mm(page_height))
                landscape = use_landscape
            document.add_heading(_heading_text(item.get("title", "数据表")), level=2)
            from .metrics import format_value
            formats = item.get("formats", {})
            display_rows = [[format_value(value, formats[column], preferences) if column in formats else value for column, value in zip(item["columns"], row)] for row in item["rows"]]
            _add_table(document, item["columns"], display_rows, landscape_content if landscape else portrait_content, latin, cjk)
            if item.get("source_file"):
                document.add_paragraph("明细来源 " + _text(item["source_file"]), style="Caption")
        if landscape:
            section = document.add_section(WD_SECTION_START.NEW_PAGE)
            section.orientation = WD_ORIENT.PORTRAIT
            section.page_width, section.page_height = Mm(page_width), Mm(page_height)
    for name, key in (("分析方法", "methodology"), ("限制与注意事项", "limitations")):
        if bundle.get(key):
            document.add_heading(name, level=1)
            for statement in bundle[key]:
                document.add_paragraph(_text(statement))
    validation = bundle.get("validation", {})
    if validation:
        document.add_heading("结果核对", level=1)
        document.add_paragraph("自动检查仅覆盖已列明的项目；业务方法、自由叙述与最终版式需要另行审阅。")
        if validation.get("status"):
            status_names = {"pass": "通过", "passed": "通过", "warn": "需关注", "warning": "需关注", "needs_review": "需要复核", "fail": "未通过", "failed": "未通过"}
            status = validation["status"]
            document.add_paragraph("核对状态 " + status_names.get(str(status), _text(status)))
        for check in validation.get("checks", []):
            name = _text(check.get("name", "检查"))
            status = _text(check.get("status", "未提供"))
            detail = _text(check.get("detail", ""))
            document.add_paragraph(f"{name}  {status}" + (f"\n{detail}" if detail else ""))
    if source.get("sha256"):
        document.add_heading("来源记录", level=1)
        document.add_paragraph("SHA256 " + _text(source["sha256"]), style="Caption")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".datalab-word-", suffix=".docx", dir=output.parent)
    os.close(fd)
    try:
        document.save(temporary)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"output": str(output), "format": "docx", "tables": len(bundle.get("tables", [])),
            "charts": len(chart_paths), "fonts": {"latin": latin, "cjk": cjk},
            "visual_qa": "not_run", "warnings": ["已生成可编辑正文和表格；图表以图片嵌入。交付前请在目标环境渲染并检查分页与字体。"]}


def add_word_parser(subparsers: Any) -> Any:
    parser = subparsers.add_parser("word", help="将 result.json 结果包导出为 Word 报告")
    parser.add_argument("--bundle", required=True, help="result.json 路径")
    parser.add_argument("--out", required=True, help="输出 .docx 路径")
    parser.set_defaults(func=lambda args: export_word(args.bundle, args.out))
    return parser
