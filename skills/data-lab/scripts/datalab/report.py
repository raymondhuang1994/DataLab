"""Render a portable, offline HTML narrative from an inspectable result bundle."""
from __future__ import annotations
import base64
import html
import json
from pathlib import Path
from .common import guard_output_path, safe_bundle_path


def _text(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.6g}"
    return str(value)


def render_html(bundle_path, output_path):
    bundle_path = Path(bundle_path).resolve()
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    if data.get("schema_version") not in ("1.0", "1.1"):
        raise ValueError("不支持的成果 schema_version。")
    output = guard_output_path(bundle_path, data, output_path)
    esc = lambda value: html.escape(_text(value), quote=True)
    sections = []
    def bullet(title, values):
        if values:
            sections.append(f"<section><h2>{esc(title)}</h2><ul>" + "".join(f"<li>{esc(v)}</li>" for v in values) + "</ul></section>")
    bullet("分析摘要", data.get("summary", []))
    for finding in data.get("findings", []):
        sections.append(f"<section><h2>{esc(finding['title'])}</h2><p>{esc(finding.get('text',''))}</p></section>")
    for chart in data.get("charts", []):
        path = safe_bundle_path(bundle_path.parent, chart["path"])
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ValueError("HTML 报告仅嵌入 PNG/JPEG 图，避免可执行 SVG 内容。")
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        sections.append(f"<figure><img alt=\"{esc(chart['title'])}\" src=\"data:{mime};base64,{encoded}\"><figcaption>{esc(chart.get('caption',''))}</figcaption></figure>")
    for table in data.get("tables", []):
        from .metrics import format_value
        header = "".join(f"<th scope=\"col\">{esc(c)}</th>" for c in table["columns"])
        formats = table.get("formats", {})
        rows = "".join("<tr>" + "".join(f"<td>{esc(format_value(v, formats[c], data.get('preferences')) if c in formats else v)}</td>" for c, v in zip(table["columns"], row)) + "</tr>" for row in table["rows"][:100])
        note = f"<p class=\"note\">展示前 100 行，共 {len(table['rows'])} 行；完整结果见成果包数据表。</p>" if len(table["rows"]) > 100 else ""
        sections.append(f"<section><h2>{esc(table['title'])}</h2>{note}<div class=\"table-wrap\"><table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table></div></section>")
    bullet("方法与口径", data.get("methodology", []))
    bullet("限制与待确认事项", data.get("limitations", []))
    checks = data.get("validation", {}).get("checks", [])
    validation = data.get("validation", {})
    bullet("审阅状态", ["自动检查结果仅覆盖明确列出的项目；业务方法、自由叙述和版式仍需审阅。"])
    if validation.get("coverage"):
        bullet("检查覆盖范围", [f"{key}：{value}" for key, value in validation["coverage"].items()])
    bullet("自动检查记录", [f"{c['name']} · {c['status']}：{c.get('detail','')}" for c in checks])
    source = data.get("source", {})
    source_description = f"数据源：{Path(source.get('path', '未提供')).name} · 记录数：{source.get('rows', '—')} · SHA-256：{source.get('sha256','未提供')}"
    document = """<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>""" + esc(data["title"]) + """</title>
<style>
:root{color-scheme:light;--ink:#18333d;--muted:#576b73;--accent:#286b85;--line:#dce5e7}
*{box-sizing:border-box}body{margin:0;background:#f4f7f8;color:var(--ink);font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif}
main{max-width:1080px;margin:32px auto;padding:48px 56px;background:#fff}header{padding-bottom:24px;border-bottom:3px solid var(--accent)}.eyebrow{letter-spacing:.16em;color:var(--accent);font-size:13px}h1{font-size:32px;line-height:1.35;margin:14px 0}h2{font-size:20px;line-height:1.4;margin:0 0 12px}section,figure{margin:32px 0}.note,figcaption,footer{color:var(--muted);font-size:13px}p{margin:8px 0}ul{padding-left:22px}li{margin:8px 0}img{display:block;width:100%;height:auto}figure{margin-left:0;margin-right:0}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:10px 12px;text-align:left;overflow-wrap:anywhere}th{background:#edf3f5}footer{margin-top:32px;padding-top:20px;border-top:1px solid var(--line);overflow-wrap:anywhere}
@media(max-width:640px){main{margin:0;padding:24px 18px}h1{font-size:26px}}@media print{body{background:#fff}main{max-width:none;margin:0;padding:0}h2{break-after:avoid}figure,tr{break-inside:avoid}thead{display:table-header-group}}
</style></head><body><main><header><div class="eyebrow">DATALAB · ANALYSIS REPORT</div><h1>""" + esc(data["title"]) + "</h1><p class=\"note\">生成时间：" + esc(data.get("created_at", "")) + "</p></header>" + "".join(sections) + "<footer>" + esc(source_description) + "<p>本文件是一次分析的数据快照。自动检查不替代对业务口径、样本及方法的审阅。</p></footer></main></body></html>"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return {"output_path": str(output), "format": "html", "self_contained": True}
