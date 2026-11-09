# Word 报告

当用户需要可编辑的 Word 报告时，将已核对的分析结果写入 `result.json`，再运行：

```text
python scripts/run.py word --bundle "结果目录/result.json" --out "结果目录/分析报告.docx"
```

在 `skills/data-lab` 目录运行上面的命令；其他位置使用入口脚本的完整路径。`word_report.export_word(bundle_path, output_path)` 也可直接调用。

## 输入与内容

采用结果包 schema 1.0 或 1.1。`title` 必填；可用内容包括 `summary`、`findings`、`charts`、`tables`、`methodology`、`limitations`、`validation` 和 `source`。结果须来自已经运行的分析，不把未执行的查询、假设或模型推测写成已证实事实。不要用 Word 导出步骤自行添加未经计算的结论。

- 正文和表格为原生 Word 对象，可编辑。图表以 PNG/JPEG 嵌入，保留宽高比；这不等于原生可编辑的 Excel 图表。
- `charts[].path` 为相对于 `result.json` 所在目录的路径，只能指向该目录内的本地图片。缺图、损坏图和目录外路径会明确报错。
- 表格必须具有 `columns` 和等长的 `rows`。所有行完整保留，跨页重复表头；7–12 列使用横向页面。超过 12 列先按任务选择字段或明确拆表，不悄悄省略数据。
- 纯文本输入按文字插入，不执行 HTML、Markdown 或 OOXML。标题中的标点会转换为空格，正文和数据值不作该转换。
- 缺失值显示为破折号。计算表保持完整数值。schema 1.1 可用 `tables[].formats` 指定列的单位和精度，Word 与 HTML 使用相同格式器；未配置格式的列按原值显示。

## Windows 和 macOS

生成 DOCX 需要 `python-docx` 与 Pillow，不需要本机安装 Word。默认 A4；项目偏好 `report.page_size` 可选 A4/Letter，`report.cjk_font` 可指定中文字体；正文 Arial，中文优先用 Windows 的 Microsoft YaHei、macOS 的 PingFang SC，其他环境用 Noto Sans CJK SC。可通过 `DATALAB_CJK_FONT` 指定目标环境已有的中文字体。文档不内嵌字体，字体替换和分页可能随打开环境变化。

部分隔离式 LibreOffice 渲染器不会读取系统字体或用户字体。若生成的页面出现方框或中文空白，应让该渲染进程通过独立的 Fontconfig 配置读取已安装中文字体，再重新渲染。不要因此把中文正文改成图片，也不要在未确认字体许可时把字体文件打包分发。

## 交付验证

导出函数只报告 `visual_qa: not_run`，生成成功不能代替版式检查。交付前用当前宿主可用的 Word/LibreOffice 渲染器输出 PDF 或页面图片，逐页检查中文、图表宽高比、表头、页码、跨页表格及截断。

随附的跨平台渲染助手可将 DOCX 转成 PDF：

```text
python scripts/render_word.py --input "结果目录/分析报告.docx" --out "结果目录/word-preview"
```

需要逐页 PNG 时添加 `--png`，并安装 Poppler（提供 `pdftoppm`）或 PyMuPDF（`python -m pip install PyMuPDF`）。默认 150 DPI，可用 `--dpi` 指定 72–300 DPI。转换必需 LibreOffice；脚本会检查 PATH、Windows Program Files 和 macOS Applications，可用 `--soffice "可执行文件完整路径"` 指定位置。它使用独立临时用户配置、参数列表调用，不修改系统字体配置。

`--out` 必须是尚不存在的新目录，防止覆盖已有结果。PDF 始终保留；缺少 PNG 渲染器会明确报错并提示检查已有 PDF。`render.json` 中的 `visual_qa: not_reviewed` 只表示已经转换，不能作为版式验收通过。必须实际打开并检查所有页面，修复后重新生成并再次检查。

具备 Documents 技能时也可用其 `render_docx.py`；安装 Word 的环境可直接导出 PDF 后检查。没有渲染能力时明确标为“已生成，尚未完成版式验收”，不要声称已检查。

正式报告保留数据口径、方法和限制；适合将主要图表和汇总表放入 Word，大量原始明细留在成果包 CSV/Excel 中，并在报告标注对应文件。没有用户要求时，不扩展成 PPT。
