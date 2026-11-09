"""Shared metric definitions, explicit filtering, computation and display.

Filters are data, never expressions. Formatting never changes the stored value.
"""
from __future__ import annotations

import math
import re
from numbers import Real, Integral


AGGREGATIONS = frozenset({"sum", "mean", "median", "ratio", "weighted_mean",
                          "count_rows", "count_nonmissing", "count_distinct"})
METRIC_KEYS = frozenset({"id", "label", "aggregation", "column", "denominator",
                         "weight", "filters", "unit", "currency", "precision"})
FILTER_OPS = frozenset({"eq", "ne", "in", "not_in", "is_null", "not_null", "gt", "ge", "lt", "le"})


def _object(value, allowed, context):
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {context} keys: {sorted(unknown, key=str)}")


def _name(value, context, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a nonempty string.")
    return value


def _scalar(value):
    return isinstance(value, (str, bool, int, float)) and not (
        isinstance(value, float) and not math.isfinite(value)
    )


def normalize_filters(filters):
    """Validate AND-connected predicates; null comparisons are explicit."""
    if filters is None:
        return []
    if not isinstance(filters, list):
        raise ValueError("filters must be a list of {column, op, value} objects.")
    result = []
    for item in filters:
        _object(item, {"column", "op", "value"}, "filter")
        column = _name(item.get("column"), "filter.column")
        op = item.get("op")
        if not isinstance(op, str) or op not in FILTER_OPS:
            raise ValueError(f"Unsupported filter op: {op!r}")
        predicate = {"column": column, "op": op}
        if op in {"is_null", "not_null"}:
            if "value" in item:
                raise ValueError(f"{op} does not accept a value.")
        else:
            value = item.get("value")
            if op in {"in", "not_in"}:
                if not isinstance(value, list) or not all(_scalar(v) for v in value):
                    raise ValueError(f"{op} requires a list of finite, non-null scalar values.")
                value = list(value)
            elif not _scalar(value):
                raise ValueError(f"{op} requires a finite, non-null scalar value; use is_null for nulls.")
            predicate["value"] = value
        result.append(predicate)
    return result


def _format_fields(spec):
    unit = spec.get("unit", "number")
    if unit not in ("number", "percent", "currency"):
        raise ValueError("unit must be number, percent or currency.")
    currency = spec.get("currency")
    if unit == "currency":
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("currency unit requires an uppercase three-letter currency code.")
    elif currency is not None:
        raise ValueError("currency is only valid with unit='currency'.")
    precision = spec.get("precision")
    if precision is not None and (type(precision) is not int or not 0 <= precision <= 12):
        raise ValueError("precision must be an integer from 0 to 12.")
    return unit, currency, precision


def normalize_metric(spec):
    """Return a fresh, complete definition; reject ambiguous combinations."""
    _object(spec, METRIC_KEYS, "metric")
    aggregation = spec.get("aggregation", "sum")
    if not isinstance(aggregation, str) or aggregation not in AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation!r}")
    column = _name(spec.get("column"), "column", optional=True)
    weight = _name(spec.get("weight"), "weight", optional=True)
    denominator = _name(spec.get("denominator"), "denominator", optional=True)
    if aggregation == "count_rows":
        if column is not None:
            raise ValueError("count_rows cannot specify column; use count_nonmissing or count_distinct.")
    elif column is None:
        raise ValueError(f"{aggregation} requires column.")
    if aggregation == "ratio" and denominator is None:
        raise ValueError("ratio requires denominator.")
    if aggregation != "ratio" and denominator is not None:
        raise ValueError("denominator is only valid for ratio.")
    if aggregation == "weighted_mean" and weight is None:
        raise ValueError("weighted_mean requires weight.")
    if aggregation != "weighted_mean" and weight is not None:
        raise ValueError("weight is only valid for weighted_mean.")
    unit, currency, precision = _format_fields(spec)
    if aggregation.startswith("count_") and unit != "number":
        raise ValueError("Count metrics use unit='number'.")
    identifier = _name(spec.get("id"), "id", optional=True)
    label = _name(spec.get("label"), "label", optional=True) or column or "记录数"
    return {"id": identifier, "label": label, "aggregation": aggregation,
            "column": column, "denominator": denominator, "weight": weight,
            "filters": normalize_filters(spec.get("filters")), "unit": unit,
            "currency": currency, "precision": precision}


def legacy_metric(column=None, aggregation="sum", weight=None, denominator=None):
    """Translate the old 'count' spelling without conflating count semantics."""
    if aggregation == "count":
        aggregation = "count_nonmissing" if column is not None else "count_rows"
    return normalize_metric({"column": column, "aggregation": aggregation,
                             "weight": weight, "denominator": denominator})


def _check_columns(df, columns):
    if not df.columns.is_unique:
        raise ValueError("Metric input has duplicate column names.")
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"Metric fields not found: {missing}")


def filter_frame(df, filters):
    """Return an independent frame; comparisons exclude nulls, including ne."""
    import pandas as pd

    predicates = normalize_filters(filters)
    _check_columns(df, [item["column"] for item in predicates])
    mask = pd.Series(True, index=df.index)
    for item in predicates:
        values = df[item["column"]]
        op = item["op"]
        try:
            if op == "is_null":
                selected = values.isna()
            elif op == "not_null":
                selected = values.notna()
            elif op in {"in", "not_in"}:
                selected = values.isin(item["value"])
                if op == "not_in":
                    selected = ~selected
                selected = values.notna() & selected
            else:
                selected = values.notna() & getattr(values, op)(item["value"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Cannot apply {op} to field {item['column']!r}; check value types.") from exc
        mask &= selected.fillna(False)
    return df.loc[mask].copy()


def _numeric(series):
    import pandas as pd

    try:
        values = pd.to_numeric(series, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Field {series.name!r} contains invalid numeric text; no implicit percent/currency conversion.") from exc
    return values.replace([float("inf"), float("-inf")], float("nan"))


def _sum(values):
    import pandas as pd

    if pd.api.types.is_integer_dtype(values.dtype):
        return sum(int(value) for value in values)
    try:
        return math.fsum(float(value) for value in values)
    except OverflowError as exc:
        raise ValueError("Metric aggregation exceeded the finite numeric range.") from exc


def compute_metric(df, spec):
    """Compute on jointly valid rows and disclose filter/validity denominators."""
    definition = normalize_metric(spec)
    involved = [definition[key] for key in ("column", "weight", "denominator") if definition[key] is not None]
    _check_columns(df, involved)
    eligible = filter_frame(df, definition["filters"])
    result = {"value": None, "input_rows": len(df), "eligible_rows": len(eligible),
              "valid_rows": 0, "excluded_rows": 0, "undefined_reason": None}
    aggregation = definition["aggregation"]
    if aggregation == "count_rows":
        result.update(value=len(eligible), valid_rows=len(eligible))
        return result
    values = eligible[definition["column"]]
    if aggregation in {"count_nonmissing", "count_distinct"}:
        valid = values.dropna()
        try:
            value = len(valid) if aggregation == "count_nonmissing" else valid.nunique(dropna=True)
        except TypeError as exc:
            raise ValueError("count_distinct requires scalar, hashable values.") from exc
        result.update(value=int(value), valid_rows=len(valid), excluded_rows=len(eligible) - len(valid))
        return result
    values = _numeric(values)
    valid = values.notna()
    other = None
    if aggregation in {"ratio", "weighted_mean"}:
        related = definition["denominator"] if aggregation == "ratio" else definition["weight"]
        other = _numeric(eligible[related])
        if (other.dropna() < 0).any():
            raise ValueError("Denominators and weights cannot be negative, including rows with a missing numerator.")
        valid &= other.notna()
    count = int(valid.sum())
    result.update(valid_rows=count, excluded_rows=len(eligible) - count)
    if not count:
        result["undefined_reason"] = "no_valid_rows"
        return result
    values = values[valid]
    if aggregation == "sum":
        value = _sum(values)
    elif aggregation == "mean":
        value = _sum(values) / count
    elif aggregation == "median":
        value = float(values.median())
    else:
        other = other[valid]
        divisor = _sum(other)
        result["denominator_sum" if aggregation == "ratio" else "weight_sum"] = divisor
        if divisor == 0:
            result["undefined_reason"] = "zero_denominator" if aggregation == "ratio" else "zero_weight_sum"
            return result
        numerator = _sum(values) if aggregation == "ratio" else math.fsum(float(x) * float(w) for x, w in zip(values, other))
        result["numerator_sum" if aggregation == "ratio" else "weighted_sum"] = numerator
        value = numerator / divisor
    if isinstance(value, Real) and not math.isfinite(value):
        raise ValueError("Metric aggregation produced a non-finite value.")
    result["value"] = value
    return result


def format_value(value, spec, preferences=None):
    """Format a canonical definition or a {unit, precision, currency} subset."""
    _object(spec, METRIC_KEYS, "metric format")
    if set(spec) - {"unit", "currency", "precision"}:
        spec = normalize_metric(spec)
    unit, currency, precision = _format_fields(spec)
    if preferences is not None and not isinstance(preferences, dict):
        raise ValueError("preferences must be an object.")
    display = (preferences or {}).get("display", {})
    if not isinstance(display, dict):
        raise ValueError("preferences.display must be an object.")
    missing = display.get("missing_label", "—")
    if not isinstance(missing, str):
        raise ValueError("missing_label must be text.")
    if precision is None:
        precision = display.get("precision", 2)
        if type(precision) is not int or not 0 <= precision <= 12:
            raise ValueError("display.precision must be an integer from 0 to 12.")
    if value is None:
        return missing
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Metric display value must be a real number or None.")
    if isinstance(value, Integral):
        shown = int(value) * (100 if unit == "percent" else 1)
        text = f"{shown:,}" + ("." + "0" * precision if precision else "")
        return text + "%" if unit == "percent" else f"{currency} {text}" if unit == "currency" else text
    if not math.isfinite(value):
        return missing
    shown = value * 100 if unit == "percent" else value
    if not math.isfinite(shown):
        raise ValueError("Formatted value exceeded the finite numeric range.")
    text = f"{shown:,.{precision}f}"
    return text + "%" if unit == "percent" else f"{currency} {text}" if unit == "currency" else text
