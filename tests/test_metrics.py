from copy import deepcopy
import math

import pandas as pd
import pytest

from datalab.metrics import compute_metric, filter_frame, format_value, legacy_metric, normalize_metric


def test_ratio_uses_paired_rows_and_discloses_population():
    df = pd.DataFrame({"orders": [50., 20., None, 90.], "visits": [1000., 100., 999., 100.],
                       "channel": ["search", "search", "search", "social"]})
    spec = {"aggregation": "ratio", "column": "orders", "denominator": "visits",
            "filters": [{"column": "channel", "op": "eq", "value": "search"}], "unit": "percent"}
    result = compute_metric(df, spec)
    assert result["value"] == pytest.approx(70/1100)
    assert (result["input_rows"], result["eligible_rows"], result["valid_rows"], result["excluded_rows"]) == (4, 3, 2, 1)
    assert result["numerator_sum"] == 70
    assert result["denominator_sum"] == 1100
    assert format_value(result["value"], spec) == "6.36%"


def test_weights_are_paired_zero_weights_not_missing():
    df = pd.DataFrame({"x": [10., 20., None, 50.], "w": [2., 0., 100., None]})
    result = compute_metric(df, {"aggregation": "weighted_mean", "column": "x", "weight": "w"})
    assert result["value"] == 10
    assert result["valid_rows"] == 2
    assert result["weight_sum"] == 2
    assert result["excluded_rows"] == 2


@pytest.mark.parametrize("aggregation,field", [("ratio", "denominator"), ("weighted_mean", "weight")])
def test_negative_related_values_rejected_even_with_missing_numerator(aggregation, field):
    df = pd.DataFrame({"x": [None, 2.], "n": [-1., 2.], "include": [False, True]})
    spec = {"column": "x", "aggregation": aggregation, field: "n"}
    with pytest.raises(ValueError, match="negative"):
        compute_metric(df, spec)
    assert compute_metric(df, {**spec, "filters": [{"column": "include", "op": "eq", "value": True}]})["value"] is not None


def test_count_semantics_and_valid_rows_are_distinct():
    df = pd.DataFrame({"x": ["a", "a", None, ""]})
    assert compute_metric(df, legacy_metric(aggregation="count"))["value"] == 4
    assert compute_metric(df, legacy_metric(column="x", aggregation="count"))["value"] == 3
    distinct = compute_metric(df, {"aggregation": "count_distinct", "column": "x"})
    assert distinct["value"] == 2
    assert distinct["valid_rows"] == 3
    assert distinct["excluded_rows"] == 1


@pytest.mark.parametrize("aggregation", ["sum", "mean", "median", "ratio", "weighted_mean"])
def test_empty_and_all_missing_numeric_metrics_remain_undefined(aggregation):
    spec = {"aggregation": aggregation, "column": "x"}
    if aggregation == "ratio":
        spec["denominator"] = "n"
    if aggregation == "weighted_mean":
        spec["weight"] = "n"
    df = pd.DataFrame({"x": [None, float("inf")], "n": [1., 1.]})
    assert compute_metric(df, spec)["value"] is None
    assert compute_metric(df.iloc[:0], spec)["value"] is None
    assert compute_metric(df, spec)["valid_rows"] == 0


def test_zero_denominator_differs_from_real_zero_and_count_zero():
    zero = pd.DataFrame({"x": [0], "n": [0]})
    result = compute_metric(zero, {"column": "x", "aggregation": "ratio", "denominator": "n"})
    assert result["value"] is None and result["valid_rows"] == 1
    assert result["undefined_reason"] == "zero_denominator"
    assert compute_metric(zero, {"column": "x", "aggregation": "sum"})["value"] == 0
    assert compute_metric(zero.iloc[:0], {"aggregation": "count_rows"})["value"] == 0


def test_all_filter_operators_and_null_rules():
    df = pd.DataFrame({"x": [1., 2., 3., None]})
    expected = {"eq": [2.], "ne": [1., 3.], "gt": [3.], "ge": [2., 3.],
                "lt": [1.], "le": [1., 2.]}
    for op, values in expected.items():
        assert filter_frame(df, [{"column": "x", "op": op, "value": 2.}])["x"].tolist() == values
    assert filter_frame(df, [{"column": "x", "op": "in", "value": [1., 3.]}])["x"].tolist() == [1., 3.]
    assert filter_frame(df, [{"column": "x", "op": "not_in", "value": [1., 3.]}])["x"].tolist() == [2.]
    assert len(filter_frame(df, [{"column": "x", "op": "is_null"}])) == 1
    assert len(filter_frame(df, [{"column": "x", "op": "not_null"}])) == 3
    both = filter_frame(df, [{"column": "x", "op": "ge", "value": 2}, {"column": "x", "op": "lt", "value": 3}])
    assert both["x"].tolist() == [2.]


def test_filters_are_literal_and_do_not_mutate_source():
    text = "__import__('os').system('echo unsafe')"
    df = pd.DataFrame({"x": [text, "other"]})
    selected = filter_frame(df, [{"column": "x", "op": "eq", "value": text}])
    assert selected["x"].tolist() == [text]
    selected.loc[0, "x"] = "changed"
    assert df.loc[0, "x"] == text
    for predicate in [{"column": "x", "op": "eval", "value": text},
                      {"column": "x", "op": "eq", "value": None},
                      {"column": "x", "op": "is_null", "value": None},
                      {"column": "x", "op": "eq", "value": "other", "expression": text}]:
        with pytest.raises(ValueError):
            filter_frame(df, [predicate])


@pytest.mark.parametrize("spec", [
    {"column": "x", "unknown": 1}, {"aggregation": "count"},
    {"aggregation": "count_rows", "column": "x"}, {"aggregation": "sum"},
    {"column": "x", "weight": "n"}, {"column": "x", "denominator": "n"},
    {"column": "x", "aggregation": "ratio"}, {"column": "x", "aggregation": "weighted_mean"},
    {"column": "x", "unit": "currency"}, {"column": "x", "currency": "USD"},
    {"column": "x", "precision": True}, {"column": "x", "precision": -1},
])
def test_invalid_metric_combinations_are_rejected(spec):
    with pytest.raises(ValueError):
        normalize_metric(spec)


def test_invalid_numeric_text_and_unknown_column_fail_explicitly():
    with pytest.raises(ValueError, match="numeric text"):
        compute_metric(pd.DataFrame({"x": ["5%"]}), {"column": "x"})
    with pytest.raises(ValueError, match="not found"):
        compute_metric(pd.DataFrame({"x": [1]}), {"column": "missing"})
    assert compute_metric(pd.DataFrame({"x": ["1.5", "2.5"]}), {"column": "x"})["value"] == 4
    assert compute_metric(pd.DataFrame({"x": [2**62, 2**62]}), {"column": "x"})["value"] == 2**63


def test_display_is_nonmutating_and_accepts_format_only_specs():
    spec = {"unit": "percent", "precision": 2}
    before = deepcopy(spec)
    preferences = {"display": {"precision": 0, "missing_label": "未定义"}}
    value = 0.08307692307692308
    assert format_value(value, spec, preferences) == "8.31%"
    assert value == 0.08307692307692308 and spec == before
    assert format_value(None, spec, preferences) == "未定义"
    assert format_value(math.nan, {}) == "—"
    assert format_value(1234.5, {"unit": "currency", "currency": "CNY", "precision": 2}) == "CNY 1,234.50"
    assert format_value(1234.5, {}, {"display": {"precision": 1}}) == "1,234.5"
    with pytest.raises(ValueError):
        format_value(1, {"unit": "number", "expression": "value*100"})
