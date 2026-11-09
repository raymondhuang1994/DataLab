import csv
import json
import math
from pathlib import Path
import pandas as pd
import pytest
from datalab.io import load_frame
from datalab.analysis import analyze, metric_value
from datalab.validation import validate_bundle
from datalab.report import render_html


def write_csv(tmp_path, rows, filename="中文 数据.csv"):
    tmp_path = tmp_path / "inputs"
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / filename
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows(rows)
    return path


def test_ids_and_legitimate_na_preserved(tmp_path):
    path = write_csv(tmp_path, [["customer_id", "region", "revenue"], ["001", "NA", 10], ["002", "", 20]])
    df = load_frame(path)
    assert df["customer_id"].tolist() == ["001", "002"]
    assert df["region"].iloc[0] == "NA"
    assert df["region"].isna().sum() == 1
    assert df["revenue"].sum() == 30


def test_duplicate_headers_rejected(tmp_path):
    path = write_csv(tmp_path, [["a", " a "], [1, 2]])
    with pytest.raises(ValueError, match="重复"):
        load_frame(path)


def test_multisheet_requires_choice(tmp_path):
    path = tmp_path / "两张表.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="甲", index=False)
        pd.DataFrame({"x": [2]}).to_excel(writer, sheet_name="乙", index=False)
    with pytest.raises(ValueError, match="多个"):
        load_frame(path)
    assert load_frame(path, sheet="乙")["x"].iloc[0] == 2


def test_weighted_mean_and_ratio_use_paired_valid_records():
    df = pd.DataFrame({"x": [50., 200., float("nan")], "n": [100., 10., 999.]})
    assert metric_value(df, "x", "weighted_mean", weight="n") == pytest.approx(7000/110)
    assert metric_value(df, "x", "ratio", denominator="n") == pytest.approx(250/110)
    assert metric_value(pd.DataFrame({"x": [1], "n": [0]}), "x", "ratio", denominator="n") is None


def test_groups_missing_dates_and_full_reconciliation(tmp_path):
    path = write_csv(tmp_path, [["日期", "地区", "收入"], ["2025-01-01", "华东", 10], ["2025-03-01", "", 20], ["bad", "华南", 5]])
    out = tmp_path / "结果"
    result = analyze(path, out, metric="收入", group="地区", date="日期", charts=False)
    assert result["metric"]["value"] == 35
    by_group = next(t for t in result["tables"] if t["id"] == "by_group")
    assert sum(row[1] for row in by_group["rows"]) == 35
    trend = next(t for t in result["tables"] if t["id"] == "by_period")
    assert trend["rows"] == [["2025-01", 10, 1], ["2025-02", None, 0], ["2025-03", 20, 1]]
    assert any("1 条" in s and "日期" in s for s in result["limitations"])
    assert validate_bundle(out / "result.json")["checks_status"] == "passed"
    # Alter a delivered table without changing JSON: QA must fail.
    file = out / "tables/by_group.csv"
    file.write_text(file.read_text(encoding="utf-8-sig").replace("20.0", "999.0").replace(",20,", ",999,"), encoding="utf-8-sig")
    assert validate_bundle(out / "result.json")["status"] == "failed"


def test_output_cannot_replace_previous_analysis(tmp_path):
    path = write_csv(tmp_path, [["x"], [1]])
    analyze(path, tmp_path / "out", charts=False)
    with pytest.raises(ValueError, match="已有"):
        analyze(path, tmp_path / "out", charts=False)


def test_html_escapes_untrusted_labels(tmp_path):
    path = write_csv(tmp_path, [["分类", "值"], ["<script>alert(1)</script>", 1]])
    out = tmp_path / "out"
    analyze(path, out, metric="值", group="分类", charts=False)
    document = (out / "report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;" in document


def test_changed_source_is_not_verified(tmp_path):
    path = write_csv(tmp_path, [["x"], [1]])
    out = tmp_path / "out"
    analyze(path, out, metric="x", charts=False)
    path.write_text("x\n2\n", encoding="utf-8")
    assert validate_bundle(out / "result.json")["status"] == "failed"


def test_json_and_parquet_load_equivalent(tmp_path):
    df = pd.DataFrame({"x": [1, 2], "类别": ["甲", "乙"]})
    a = tmp_path / "a.json"; b = tmp_path / "b.parquet"
    a.write_text(df.to_json(orient="records", force_ascii=False), encoding="utf-8")
    df.to_parquet(b, index=False)
    pd.testing.assert_frame_equal(load_frame(a), load_frame(b))


def test_count_accepts_text_identifiers(tmp_path):
    path = write_csv(tmp_path, [["customer_id", "group"], ["a", "x"], ["b", "x"], ["", "y"]])
    result = analyze(path, tmp_path / "out", metric="customer_id", agg="count", group="group", charts=False)
    assert result["metric"]["value"] == 2
    assert validate_bundle(tmp_path / "out/result.json")["checks_status"] == "passed"


def test_output_cannot_contain_source(tmp_path):
    out = tmp_path / "out"
    (out / "tables").mkdir(parents=True)
    path = write_csv(out / "tables", [["x"], [7]], "profile.csv")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="已有|原始数据目录"):
        analyze(path, out, metric="x", charts=False)
    assert path.read_bytes() == original


def test_internal_column_names_do_not_corrupt_metrics(tmp_path):
    path = write_csv(tmp_path, [["__datalab_group", "__datalab_period", "分类", "日期"], [3, 4, "甲", "2025-01-01"]])
    result = analyze(path, tmp_path / "out", metric="__datalab_group", group="分类", date="日期", charts=False)
    assert result["metric"]["value"] == 3
    assert next(t for t in result["tables"] if t["id"] == "by_group")["rows"][0][1] == 3


def test_mixed_timezone_requires_explicit_normalization():
    from datalab.analysis import parse_dates
    with pytest.raises(ValueError, match="混合"):
        parse_dates(pd.Series(["2025-01-01 00:00:00", "2025-01-01T00:00:00Z"]), "Asia/Shanghai")
    result = parse_dates(pd.Series(["2025-01-01T23:00:00-05:00", "2025-01-01T23:00:00+01:00"]), "UTC")
    assert result.dt.day.tolist() == [2, 1]
