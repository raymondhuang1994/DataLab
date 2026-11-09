import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "data-lab" / "scripts"))

from datalab.word_report import export_word
from docx import Document
from lxml import etree
from PIL import Image


class WordReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="datalab-word-test-")
        self.directory = Path(self.temp.name)
        Image.new("RGB", (800, 200), "white").save(self.directory / "chart.png")
        self.bundle = {
            "schema_version": "1.0", "title": "月度经营分析", "created_at": "2026-11-08",
            "source": {"path": "中文 数据.csv", "sha256": "a" * 64, "rows": 95, "columns": 3},
            "summary": ["销售额按同一口径汇总。"],
            "findings": [{"title": "地区差异", "text": "A < B & C；原始文本保留。", "evidence": "table.csv"}],
            "tables": [{"title": "地区明细", "columns": ["地区", "销售额", "说明"],
                        "rows": [[f"地区 {i}", i * 100, "统计范围按完整月份计算"] for i in range(95)]}],
            "charts": [{"title": "月度趋势", "path": "chart.png", "caption": "单位为元"}],
            "methodology": ["汇总记录，未推断因果关系。"], "limitations": ["没有上一年同期数据。"],
            "validation": {"status": "passed", "checks": [{"name": "行数检查", "status": "pass", "detail": "95 行"}]},
        }

    def tearDown(self):
        self.temp.cleanup()

    def export(self):
        path = self.directory / "result.json"
        path.write_text(json.dumps(self.bundle, ensure_ascii=False), encoding="utf-8")
        output = self.directory / "报告.docx"
        return output, export_word(path, output)

    def test_editable_text_full_table_repeated_header_and_aspect(self):
        output, result = self.export()
        document = Document(output)
        self.assertEqual(len(document.tables[0].rows), 96)
        self.assertEqual(document.tables[0].cell(95, 0).text, "地区 94")
        paragraphs = "\n".join(p.text for p in document.paragraphs)
        self.assertIn("A < B & C", paragraphs)
        self.assertIn("没有上一年同期数据", paragraphs)
        self.assertEqual(result["visual_qa"], "not_run")
        with ZipFile(output) as archive:
            tree = etree.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}
            self.assertEqual(len(tree.xpath("//w:tblHeader", namespaces=ns)), 1)
            self.assertFalse(tree.xpath("//w:trHeight[@w:hRule='exact']", namespaces=ns))
            extent = tree.xpath("//wp:extent", namespaces=ns)[0]
            self.assertAlmostEqual(int(extent.get("cx")) / int(extent.get("cy")), 4.0, places=4)
            footer = archive.read("word/footer1.xml").decode()
            self.assertIn("PAGE", footer)
            self.assertTrue(tree.xpath("//w:rFonts[@w:eastAsia]", namespaces=ns))

    def test_wide_table_gets_landscape_without_dropping_columns(self):
        self.bundle["tables"] = [{"title": "宽表", "columns": [f"字段{i}" for i in range(9)], "rows": [list(range(9))]}]
        output, _ = self.export()
        document = Document(output)
        self.assertEqual(len(document.tables[0].columns), 9)
        self.assertTrue(any(s.page_width > s.page_height for s in document.sections))

    def test_missing_chart_fails_before_output(self):
        self.bundle["charts"][0]["path"] = "missing.png"
        with self.assertRaises(FileNotFoundError):
            self.export()
        self.assertFalse((self.directory / "报告.docx").exists())

    def test_outside_chart_and_inconsistent_rows_rejected(self):
        self.bundle["charts"][0]["path"] = "../private.png"
        with self.assertRaisesRegex(ValueError, "目录内"):
            self.export()
        self.bundle["charts"] = []
        self.bundle["tables"][0]["rows"].append(["too few"])
        with self.assertRaisesRegex(ValueError, "columns"):
            self.export()


if __name__ == "__main__":
    unittest.main()
