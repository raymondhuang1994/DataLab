import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from datalab.cli import main
from datalab.analysis import analyze
from datalab.word_report import export_word


def project_files(tmp_path):
    tmp_path = tmp_path / "inputs"
    tmp_path.mkdir()
    source = tmp_path / "渠道 数据.csv"
    source.write_text("month,channel,visits,orders,status\n2025-01,search,1000,50,valid\n2025-02,search,100,20,valid\n2025-02,social,900,900,cancelled\n", encoding="utf-8")
    project = tmp_path / "项目.json"
    project.write_text(json.dumps({"schema_version":"1.0", "name":"渠道比较", "data":{"date":"month", "grain":"channel-month"}, "analysis":{"group":"channel", "timezone":"Asia/Shanghai"}, "metrics":{"conversion":{"label":"整体转化率", "aggregation":"ratio", "column":"orders", "denominator":"visits", "filters":[{"column":"status", "op":"eq", "value":"valid"}], "unit":"percent"}}, "quality":{"required_columns":["orders","visits"], "min_rows":2}}), encoding="utf-8")
    preferences = tmp_path / "偏好.json"
    preferences.write_text(json.dumps({"schema_version":"1.0", "report":{"title":"我的渠道报告", "page_size":"Letter", "cjk_font":"Noto Sans CJK SC"}, "display":{"precision":1}, "charts":{"color":"#224466"}}), encoding="utf-8")
    return source, project, preferences


def test_cli_project_metric_and_preferences_reach_all_report_outputs(tmp_path):
    source, project, preferences = project_files(tmp_path)
    out = tmp_path / "输出 空格"
    assert main(["analyze", "--input",str(source),"--project",str(project),"--preferences",str(preferences),"--metric-id","conversion","--out",str(out),"--no-charts"]) == 0
    data = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert data["metric"]["value"] == pytest.approx(70/1100)
    assert data["title"] == "我的渠道报告"
    assert data["computation"]["eligible_rows"] == 2
    assert "6.4%" in " ".join(data["summary"])
    assert "6.4%" in (out / "report.html").read_text(encoding="utf-8")
    assert data["validation"]["checks_status"] == "passed"
    assert data["validation"]["status"] == "needs_review"
    docx = out / "report.docx"
    export_word(out / "result.json", docx)
    from docx import Document
    doc = Document(docx)
    assert doc.sections[0].page_width.inches == pytest.approx(8.5, abs=.01)
    assert any("6.4%" in cell.text for table in doc.tables for row in table.rows for cell in row.cells)
    replay = tmp_path / "replay"
    assert main(["rerun", "--config",str(out / "config.json"),"--out",str(replay)]) == 0
    repeated = json.loads((replay / "result.json").read_text(encoding="utf-8"))
    assert repeated["metric"] == data["metric"]
    assert repeated["preferences"] == data["preferences"]


def test_project_quality_failure_does_not_create_report(tmp_path):
    source, project, preferences = project_files(tmp_path)
    definition = json.loads(project.read_text(encoding="utf-8"))
    definition["quality"]["unique_key"] = ["channel"]
    project.write_text(json.dumps(definition), encoding="utf-8")
    out = tmp_path / "must_not_exist"
    assert main(["analyze", "--input",str(source),"--project",str(project),"--metric-id","conversion","--out",str(out),"--no-charts"]) == 2
    assert not (out / "result.json").exists()
    assert json.loads(next((out / "audit").glob("*.json")).read_text(encoding="utf-8"))["status"] == "failed"


def test_registered_metric_cannot_be_silently_overridden(tmp_path):
    source, project, preferences = project_files(tmp_path)
    out = tmp_path / "wrong"
    assert main(["analyze", "--input",str(source),"--project",str(project),"--metric-id","conversion","--agg","mean","--out",str(out)]) == 2
    assert not (out / "result.json").exists()
    assert json.loads(next((out / "audit").glob("*.json")).read_text(encoding="utf-8"))["status"] == "failed"
