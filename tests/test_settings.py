from copy import deepcopy
import hashlib
import json

import pytest

from datalab.metrics import compute_metric, format_value
from datalab.settings import load_preferences, load_project, resolve_settings


def write_json(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def project():
    return {"schema_version": "1.0", "name": "渠道比较",
            "data": {"grain": ["month", "channel"], "entity_key": ["month", "channel"], "date": "month", "sheet": "明细"},
            "analysis": {"timezone": "Asia/Shanghai", "frequency": "M", "group": "channel"},
            "metrics": {"conversion": {"label": "转化率", "aggregation": "ratio", "column": "orders",
                "denominator": "visits", "unit": "percent", "filters": [{"column": "eligible", "op": "eq", "value": True}]}},
            "quality": {"required_columns": ["orders", "visits"], "unique_key": ["month", "channel"],
                        "max_missing_fraction": {"orders": 0.05, "visits": 0}, "min_rows": 1}}


def test_explicit_resolution_precedence_and_provenance(tmp_path):
    project_path = write_json(tmp_path, "项目 配置.json", project())
    preference_path = write_json(tmp_path, "偏好.json", {"schema_version": "1.0", "report": {"title": "每月渠道"},
                                                        "display": {"precision": 3}})
    resolved = resolve_settings(project_path, preference_path, "conversion", {"group": "region", "title": "本次报告", "date": None})
    assert resolved["analysis"] == {"metric": "orders", "agg": "ratio", "weight": None, "denominator": "visits",
            "group": "region", "date": "month", "sheet": "明细", "key": ["month", "channel"],
            "timezone": "Asia/Shanghai", "freq": "M", "title": "本次报告"}
    assert resolved["metric_definition"]["id"] == "conversion"
    assert resolved["metric_definition"]["filters"][0]["value"] is True
    assert resolved["quality"]["max_missing_fraction"] == {"orders": 0.05, "visits": 0}
    provenance = resolved["provenance"]
    assert provenance["files"]["project"]["sha256"] == hashlib.sha256(project_path.read_bytes()).hexdigest()
    assert provenance["field_sources"]["analysis.group"] == "overrides.group"
    assert provenance["field_sources"]["analysis.date"] == "project.data.date"
    assert provenance["field_sources"]["analysis.agg"] == "project.metrics.conversion.aggregation"
    assert provenance["data_grain"] == ["month", "channel"]
    assert format_value(0.12345, resolved["metric_definition"], resolved["preferences"]) == "12.345%"


@pytest.mark.parametrize("override", [{"metric": "other"}, {"agg": "ratio"}, {"denominator": "other"},
                                      {"weight": "w"}, {"filters": []}])
def test_registered_metric_rejects_business_override_even_if_equal(tmp_path, override):
    path = write_json(tmp_path, "project.json", project())
    with pytest.raises(ValueError, match="business overrides"):
        resolve_settings(path, metric_id="conversion", overrides=override)


def test_preferences_cannot_change_metric_semantics(tmp_path):
    import pandas as pd

    p = project()
    p["metrics"]["conversion"]["precision"] = 1
    path = write_json(tmp_path, "project.json", p)
    prefs = write_json(tmp_path, "prefs.json", {"schema_version": "1.0", "display": {"precision": 5},
                                              "dashboard": {"interval": 9, "max_rows": 100}})
    result = resolve_settings(path, prefs, "conversion")
    df = pd.DataFrame({"orders": [2, 9], "visits": [10, 10], "eligible": [True, False]})
    assert compute_metric(df, result["metric_definition"])["value"] == 0.2
    assert format_value(0.2, result["metric_definition"], result["preferences"]) == "20.0%"
    bad = write_json(tmp_path, "bad.json", {"schema_version": "1.0", "analysis": {"aggregation": "sum"}})
    with pytest.raises(ValueError, match="Unknown"):
        load_preferences(bad)


def test_defaults_are_isolated_and_no_home_discovery(tmp_path, monkeypatch):
    write_json(tmp_path, "preferences.json", {"schema_version": "1.0", "display": {"precision": 9}})
    monkeypatch.setenv("HOME", str(tmp_path))
    result = resolve_settings()
    assert result["metric_definition"] is None
    assert result["provenance"]["files"] == {}
    assert result["preferences"]["display"]["precision"] == 2
    assert result["preferences"]["charts"]["color"] == "#286B85"
    assert result["preferences"]["dashboard"]["max_rows"] == 50000
    result["quality"]["required_columns"].append("bad")
    result["preferences"]["display"]["precision"] = 9
    assert resolve_settings()["quality"]["required_columns"] == []
    assert resolve_settings()["preferences"]["display"]["precision"] == 2


def test_legacy_count_mapping_and_profile_are_explicit(tmp_path):
    assert resolve_settings(overrides={"agg": "count"})["analysis"]["agg"] == "count_rows"
    assert resolve_settings(overrides={"agg": "count", "metric": "id"})["analysis"]["agg"] == "count_nonmissing"
    assert resolve_settings(overrides={"agg": "count_distinct", "metric": "id"})["metric_definition"]["aggregation"] == "count_distinct"
    path = write_json(tmp_path, "project.json", project())
    assert resolve_settings(path)["metric_definition"] is None  # Do not guess which metric to select.
    with pytest.raises(ValueError, match="not found"):
        resolve_settings(path, metric_id="missing")
    with pytest.raises(ValueError):
        resolve_settings(overrides={"agg": "mean"})
    with pytest.raises(ValueError):
        resolve_settings(overrides={"filters": []})
    with pytest.raises(ValueError, match="Unknown"):
        resolve_settings(overrides={"source_path": "auto-discover.csv"})


@pytest.mark.parametrize("change", [
    {"unknown": True}, {"data": {"auto_find": "*.csv"}}, {"analysis": {"frequency": "hourly"}},
    {"quality": {"max_missing_fraction": 0.5}}, {"quality": {"max_missing_fraction": {"x": 1.01}}},
    {"quality": {"min_rows": True}}, {"quality": {"unique_key": ["x", "x"]}},
    {"metrics": {"rate": {"id": "other", "column": "x"}}},
])
def test_invalid_project_fields_rejected(tmp_path, change):
    value = {**project(), **change}
    with pytest.raises(ValueError):
        load_project(write_json(tmp_path, "bad.json", value))


@pytest.mark.parametrize("section", [
    {"charts": {"color": "red"}}, {"display": {"precision": -1}},
    {"report": {"page_size": "A3"}}, {"dashboard": {"interval": 0}},
    {"dashboard": {"max_rows": 1.5}}, {"display": {"missing_label": 0}},
])
def test_invalid_preference_values_rejected(tmp_path, section):
    with pytest.raises(ValueError):
        load_preferences(write_json(tmp_path, "bad.json", {"schema_version": "1.0", **section}))


def test_nonfinite_and_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":"1.0","display":{"precision":2,"precision":3}}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_preferences(path)
    path.write_text('{"schema_version":"1.0","dashboard":{"interval":NaN}}')
    with pytest.raises(ValueError, match="Non-finite"):
        load_preferences(path)


def test_public_loaders_do_not_mutate_inputs_and_fill_only_appearance_defaults(tmp_path):
    value = project()
    before = deepcopy(value)
    loaded = load_project(write_json(tmp_path, "project.json", value))
    assert value == before
    assert loaded["metrics"]["conversion"]["id"] == "conversion"
    prefs = load_preferences(write_json(tmp_path, "prefs.json", {"schema_version": "1.0"}))
    assert prefs["report"]["page_size"] == "A4"
    assert "metrics" not in prefs
