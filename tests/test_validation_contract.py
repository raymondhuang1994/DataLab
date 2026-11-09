"""Adversarial validation tests: matching files must not certify wrong math."""
import csv
import hashlib
import json
from pathlib import Path

import pytest
from datalab.validation import validate_bundle


def case(tmp_path, rows=None, definition=None, value=None, legacy=False):
    rows = rows or [["id", "x", "n", "g", "date", "state"],
                    ["a", 10, 100, "甲", "2025-01-01", "ok"],
                    ["a", 30, 50, "甲", "2025-03-01", "ok"],
                    ["b", "", 20, "乙", "bad", "drop"],
                    ["c", 0, 0, "乙", "2025-03-02", ""]]
    source = tmp_path / "原始.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    data = {"schema_version": "1.0" if legacy else "1.1", "title": "独立复算验证", "source": {
        "path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "rows": len(rows)-1, "columns": rows[0]}, "summary": ["一份实际分析。"], "tables": [], "charts": []}
    if definition is not None:
        spec = {"id": None, "label": "指标", "aggregation": "sum", "column": "x",
                "weight": None, "denominator": None, "filters": [], "unit": "number", "precision": 2, "currency": None, **definition}
        if not legacy:
            data["metric_definition"] = spec
        data["metric"] = {**spec, "value": value, "group": None, "date": None, "frequency": "M", "timezone": "UTC"}
    return tmp_path / "result.json", data


def validate(path, data, **options):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return validate_bundle(path, **options)


def add_table(path, data, identifier, columns, rows):
    filename = identifier + ".csv"
    with (path.parent / filename).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream); writer.writerow(columns); writer.writerows(rows)
    data["tables"].append({"id": identifier, "title": identifier, "columns": columns, "rows": rows, "source_file": filename})


@pytest.mark.parametrize("payload", [{"schema_version": "1.0"}, [], None,
    {"schema_version": {"unexpected": True}, "title": 7, "source": []}])
def test_empty_or_malformed_bundle_returns_failure(tmp_path, payload):
    path = tmp_path / "bad.json"
    result = validate(path, payload)
    assert result["status"] == result["checks_status"] == "failed"


def test_invalid_json_returns_failure(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")
    assert validate_bundle(path)["status"] == "failed"


@pytest.mark.parametrize("definition,value", [
    ({"aggregation": "sum"}, 40),
    ({"aggregation": "mean"}, 40/3),
    ({"aggregation": "median"}, 10),
    ({"aggregation": "ratio", "denominator": "n"}, 40/150),
    ({"aggregation": "weighted_mean", "weight": "n"}, 2500/150),
    ({"aggregation": "count_rows", "column": None}, 4),
    ({"aggregation": "count_nonmissing", "column": "x"}, 3),
    ({"aggregation": "count_distinct", "column": "id"}, 3),
])
def test_all_metric_types_are_independently_recomputed(tmp_path, definition, value):
    path, data = case(tmp_path, definition=definition, value=value)
    result = validate(path, data)
    assert result["checks_status"] == "passed", result
    assert result["status"] == "needs_review"
    assert result["coverage"]["calculations"]["checked"] == ["independent_total"]
    assert result["coverage"]["narrative"]["status"] == "not_checked"
    assert result["coverage"]["visual"]["status"] == "not_checked"
    data["metric"]["value"] = 99
    assert validate(path, data)["status"] == "failed"


@pytest.mark.parametrize("op,value,expected", [
    ("eq", "ok", 2), ("ne", "ok", 1), ("in", ["ok"], 2), ("not_in", ["ok"], 1),
    ("is_null", None, 1), ("not_null", None, 3),
])
def test_nulls_and_set_filtering_match_declared_population(tmp_path, op, value, expected):
    rule = {"column": "state", "op": op}
    if op not in {"is_null", "not_null"}:
        rule["value"] = value
    path, data = case(tmp_path, definition={"aggregation": "count_rows", "column": None, "filters": [rule]}, value=expected)
    assert validate(path, data)["checks_status"] == "passed"


@pytest.mark.parametrize("op,expected", [("gt", 1), ("ge", 2), ("lt", 1), ("le", 2)])
def test_ordered_filters(tmp_path, op, expected):
    path, data = case(tmp_path, definition={"aggregation": "count_rows", "column": None,
                    "filters": [{"column": "x", "op": op, "value": 10}]}, value=expected)
    assert validate(path, data)["checks_status"] == "passed"


def test_legacy_count_and_zero_denominator(tmp_path):
    path, data = case(tmp_path, definition={"aggregation": "count", "column": "id"}, value=4, legacy=True)
    assert validate(path, data)["checks_status"] == "passed"
    path, data = case(tmp_path, definition={"aggregation": "ratio", "denominator": "n",
        "filters": [{"column": "id", "op": "eq", "value": "c"}]}, value=None)
    assert validate(path, data)["checks_status"] == "passed"
    data["metric"]["value"] = 0
    assert validate(path, data)["status"] == "failed"


def test_group_and_period_recompute_even_when_json_and_csv_agree(tmp_path):
    path, data = case(tmp_path, definition={"aggregation": "ratio", "denominator": "n"}, value=40/150)
    data["metric"].update({"group": "g", "date": "date"})
    add_table(path, data, "by_group", ["g", "指标值", "记录数"], [["乙", None, 2], ["甲", 40/150, 2]])
    add_table(path, data, "by_period", ["期间", "指标值", "记录数"], [["2025-01", .1, 1], ["2025-02", None, 0], ["2025-03", .6, 2]])
    assert validate(path, data)["checks_status"] == "passed"
    # Tamper with both representations: mere file agreement cannot catch this.
    data["tables"] = []
    add_table(path, data, "by_group", ["g", "指标值", "记录数"], [["乙", None, 2], ["甲", .99, 2]])
    add_table(path, data, "by_period", ["期间", "指标值", "记录数"], [["2025-01", .1, 1], ["2025-02", 0, 0], ["2025-03", .6, 2]])
    result = validate(path, data)
    assert result["status"] == "failed"
    assert {c["name"] for c in result["checks"] if c["status"] == "failed"} == {"independent_by_group", "independent_by_period"}


def claim(value=40, evidence=None):
    return {"id": "total", "kind": "observation", "value": value, "format": {"unit": "number", "precision": 2},
            "template": "合计为{value}。", "text": "合计为40.00。", "evidence": evidence or {"metric": "value"}}


def test_claim_text_value_and_evidence_consistency(tmp_path):
    path, data = case(tmp_path, definition={}, value=40)
    data["claims"] = [claim()]
    data["summary"] = ["合计为40.00。"]
    result = validate(path, data)
    assert result["checks_status"] == "passed", result
    assert result["coverage"]["claims"]["checked"] == 1
    data["summary"] = ["合计为99.00，证明因果效果。"]
    assert validate(path, data)["status"] == "failed"


@pytest.mark.parametrize("mutation", [
    {"value": 99}, {"text": "合计为99.00。"}, {"template": "{value.__class__}"},
    {"template": "{value:.2f}"}, {"evidence": {"metric": "absent"}},
    {"evidence": {"table_id": "missing", "row": 0, "column": "指标值"}},
    {"kind": "causal"},
])
def test_invalid_or_unsupported_claims_fail(tmp_path, mutation):
    path, data = case(tmp_path, definition={}, value=40)
    data["claims"] = [{**claim(), **mutation}]
    data["summary"] = ["合计为40.00。"]
    assert validate(path, data)["status"] == "failed"


def test_table_claim_and_percent_format(tmp_path):
    path, data = case(tmp_path, definition={"aggregation": "ratio", "denominator": "n", "unit": "percent"}, value=40/150)
    add_table(path, data, "custom", ["分组", "比率"], [["甲", .1]])
    data["claims"] = [{"id": "row", "kind": "observation", "value": .1, "format": {"unit": "percent", "precision": 1},
        "template": "甲组为{value}。", "text": "甲组为10.0%。", "evidence": {"table_id": "custom", "row": 0, "column": "比率"}}]
    data["findings"] = [{"title": "比较", "text": "甲组为10.0%。"}]
    result = validate(path, data)
    assert result["checks_status"] == "passed", result
    assert "表格计算：custom" in result["coverage"]["calculations"]["not_checked"]
    # A valid reference confirms correspondence, not the custom table calculation.
    assert result["coverage"]["calculations"]["status"] == "partial"


def test_source_disabled_and_source_unavailable_are_explicit(tmp_path):
    path, data = case(tmp_path, definition={}, value=40)
    result = validate(path, data, verify_source=False)
    assert result["checks_status"] == "passed"
    assert result["status"] == "needs_review"
    assert result["coverage"]["calculations"]["status"] == "not_checked"
    (tmp_path / "原始.csv").unlink()
    result = validate(path, data)
    assert result["checks_status"] == "warning"
    assert result["coverage"]["source"]["status"] == "not_checked"


def test_quality_is_independent_and_applies_before_metric_filter(tmp_path):
    path, data = case(tmp_path, definition={"filters": [{"column": "state", "op": "eq", "value": "ok"}]}, value=40)
    data["quality_rules"] = {"min_rows": 4, "required_columns": ["id", "x"], "unique_key": ["id"], "max_missing_fraction": {"x": .1}}
    data["quality"] = {"status": "passed", "checks": []}  # Never trust this producer badge.
    result = validate(path, data)
    failures = {c["name"] for c in result["checks"] if c["status"] == "failed"}
    assert failures == {"quality:unique_key", "quality:max_missing_fraction:x"}
    assert next(c for c in result["checks"] if c["name"] == "quality:min_rows")["status"] == "passed"


def test_profile_only_has_no_fake_calculation_pass(tmp_path):
    path, data = case(tmp_path)
    data["metric"] = {"column": None, "aggregation": "sum", "value": None}
    data["metric_definition"] = None
    result = validate(path, data)
    assert result["checks_status"] == "passed"
    assert result["coverage"]["calculations"]["status"] == "not_checked"


def test_disabled_unique_key_and_uncertified_computation_metadata(tmp_path):
    path, data = case(tmp_path, definition={}, value=40)
    data["quality_rules"] = {"unique_key": []}
    data["computation"] = {"valid_rows": 999}
    result = validate(path, data)
    assert result["checks_status"] == "passed"
    assert result["coverage"]["calculations"]["status"] == "partial"
    assert any("computation" in note for note in result["coverage"]["calculations"]["not_checked"])


def test_boolean_value_is_not_a_valid_numerical_metric(tmp_path):
    path, data = case(tmp_path, definition={"aggregation": "count_rows", "column": None}, value=True)
    assert validate(path, data)["status"] == "failed"


def test_negative_denominator_cannot_hide_behind_missing_numerator(tmp_path):
    path, data = case(tmp_path, rows=[["x", "n"], [10, 100], ["", -50]],
                      definition={"aggregation": "ratio", "denominator": "n"}, value=.1)
    assert validate(path, data)["status"] == "failed"


@pytest.mark.parametrize("legacy", [True, False])
def test_missing_standard_group_tables_have_versioned_coverage(tmp_path, legacy):
    path, data = case(tmp_path, definition={}, value=40, legacy=legacy)
    data["metric"].update({"group": "g", "date": "date"})
    add_table(path, data, "custom_summary", ["说明", "数值"], [["仅供呈现", 40]])
    result = validate(path, data)
    if legacy:
        assert result["status"] == "needs_review"
        assert result["checks_status"] == "warning"
        assert result["coverage"]["calculations"]["status"] == "partial"
        assert any("by_group" in note for note in result["coverage"]["calculations"]["not_checked"])
        assert any("by_period" in note for note in result["coverage"]["calculations"]["not_checked"])
    else:
        assert result["status"] == "failed"
