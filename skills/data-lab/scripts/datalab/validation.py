"""Scoped validation with independent arithmetic and explicit unchecked coverage."""
from __future__ import annotations
import csv
import json
import math
import re
import statistics
import string
from decimal import Decimal, InvalidOperation
from numbers import Integral, Real
from datetime import date, datetime
from pathlib import Path
from .common import file_sha256, safe_bundle_path, utc_now


def _same(a, b):
    if a is None:
        return b in ("", "nan", "NaN", None)
    if isinstance(a, Integral) and not isinstance(a, bool):
        try:
            return not isinstance(b, bool) and Decimal(str(b)) == Decimal(int(a))
        except (InvalidOperation, ValueError, TypeError):
            return False
    if isinstance(a, (float, int)) and not isinstance(a, bool):
        try:
            return math.isfinite(float(a)) and math.isfinite(float(b)) and math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
        except (ValueError, TypeError, OverflowError):
            return False
    return str(a) == str(b)


def _number(value):
    """A missing/nonfinite observation is omitted; nonnumeric text is an error."""
    import pandas as pd
    if pd.isna(value):
        return None
    if isinstance(value, Integral):
        return int(value)
    result = float(value)
    return result if math.isfinite(result) else None


def _independent_group_labels(series):
    import pandas as pd
    kinds, values = set(), []
    for value in series:
        if pd.isna(value):
            values.append("(缺失)")
            continue
        if isinstance(value, bool) or type(value).__name__ == "bool_":
            kinds.add("boolean"); values.append("true" if value else "false")
        elif isinstance(value, Real) and math.isfinite(value):
            kinds.add("number"); values.append(str(int(value)) if value == int(value) else str(value))
        elif isinstance(value, str):
            kinds.add("text"); values.append(value)
        elif isinstance(value, (date, datetime)):
            kinds.add("datetime"); values.append(value.isoformat())
        else:
            raise ValueError("分组包含不支持或非有限的类别。")
    if len(kinds) > 1:
        raise ValueError("分组字段混合类别类型，必须先明确统一类型。")
    labels = pd.Series(values, index=series.index, dtype=object)
    if series.isna().any() and (series.notna() & labels.eq("(缺失)")).any():
        raise ValueError("缺失类别与实际标签冲突。")
    return labels


def _filtered(frame, filters):
    """Independent filter implementation; never calls metrics.apply_filters."""
    if not isinstance(filters, list):
        raise ValueError("filters 必须为列表。")
    work = frame
    for rule in filters:
        if not isinstance(rule, dict) or not set(rule) <= {"column", "op", "value"}:
            raise ValueError("过滤规则需要 column/op/value。")
        column, op = rule.get("column"), rule.get("op")
        if not isinstance(column, str) or column not in work:
            raise ValueError(f"过滤字段不存在：{column}")
        values, operand = work[column], rule.get("value")
        present = values.notna()
        if op in {"is_null", "not_null"}:
            if "value" in rule:
                raise ValueError("空值过滤操作不得同时声明value。")
            selected = values.isna() if op == "is_null" else present
        elif op in {"in", "not_in"}:
            if not isinstance(operand, list) or not all(_filter_scalar(v) for v in operand):
                raise ValueError("in/not_in 的 value 必须为列表。")
            selected = values.isin(operand)
            selected = present & (selected if op == "in" else ~selected)
        else:
            operations = {"eq": values.eq, "ne": values.ne, "gt": values.gt, "ge": values.ge, "lt": values.lt, "le": values.le}
            if op not in operations or "value" not in rule or not _filter_scalar(operand):
                raise ValueError(f"不支持或不完整的过滤规则：{rule}")
            selected = present & operations[op](operand)
        work = work.loc[selected.fillna(False)]
    return work


def _filter_scalar(value):
    return isinstance(value, (str, bool, int, float)) and not (isinstance(value, float) and not math.isfinite(value))


def _definition_valid(spec):
    if not set(spec) <= {"id", "label", "aggregation", "column", "denominator", "weight", "filters", "unit", "currency", "precision"}:
        return False
    aggregation = spec.get("aggregation")
    if aggregation not in ("sum", "mean", "median", "ratio", "weighted_mean", "count_rows", "count_nonmissing", "count_distinct"):
        return False
    if not isinstance(spec.get("label"), str) or not spec["label"].strip():
        return False
    for key in ("id", "column", "weight", "denominator"):
        if spec.get(key) is not None and (not isinstance(spec[key], str) or not spec[key].strip()):
            return False
    if (aggregation == "count_rows") != (spec.get("column") is None):
        return False
    if (aggregation == "ratio") != (spec.get("denominator") is not None):
        return False
    if (aggregation == "weighted_mean") != (spec.get("weight") is not None):
        return False
    filters = spec.get("filters", [])
    if not isinstance(filters, list):
        return False
    for rule in filters:
        if not isinstance(rule, dict) or not set(rule) <= {"column", "op", "value"} or not isinstance(rule.get("column"), str) or not rule["column"]:
            return False
        op = rule.get("op")
        if op in ("is_null", "not_null"):
            if "value" in rule:
                return False
        elif op in ("in", "not_in"):
            if not isinstance(rule.get("value"), list) or not all(_filter_scalar(v) for v in rule["value"]):
                return False
        elif op in ("eq", "ne", "gt", "ge", "lt", "le"):
            if not _filter_scalar(rule.get("value")):
                return False
        else:
            return False
    unit = spec.get("unit", "number")
    if unit not in ("number", "percent", "currency") or (aggregation.startswith("count_") and unit != "number"):
        return False
    if unit == "currency":
        if not isinstance(spec.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", spec["currency"]):
            return False
    elif spec.get("currency") is not None:
        return False
    precision = spec.get("precision")
    return precision is None or (type(precision) is int and 0 <= precision <= 12)


def _calculate(frame, spec):
    """Recompute using Python scalars, fsum and median, independent of producer."""
    aggregation = spec.get("aggregation")
    column = spec.get("column")
    if aggregation == "count":  # Version 1.0 compatibility.
        aggregation = "count_nonmissing" if column else "count_rows"
    if aggregation == "count_rows":
        return len(frame)
    if not isinstance(column, str) or column not in frame:
        raise ValueError(f"指标字段不存在：{column}")
    observed = frame[column].dropna().tolist()
    if aggregation == "count_nonmissing":
        return len(observed)
    if aggregation == "count_distinct":
        return len(set(observed))
    if aggregation not in {"sum", "mean", "median", "ratio", "weighted_mean"}:
        raise ValueError(f"独立复算不支持此聚合：{aggregation}")
    if aggregation in {"ratio", "weighted_mean"}:
        related = spec.get("denominator" if aggregation == "ratio" else "weight")
        if not isinstance(related, str) or related not in frame:
            raise ValueError("比例/加权指标缺少有效分母/权重字段。")
        pairs = [(_number(a), _number(b)) for a, b in zip(frame[column], frame[related])]
        if any(b is not None and b < 0 for a, b in pairs):
            raise ValueError("分母/权重不得为负数，包括分子缺失的记录。")
        valid = [(a, b) for a, b in pairs if a is not None and b is not None]
        divisor = math.fsum(b for a, b in valid)
        if divisor == 0:
            return None
        numerator = math.fsum(a if aggregation == "ratio" else a*b for a, b in valid)
        return numerator/divisor
    values = [_number(value) for value in observed]
    values = [value for value in values if value is not None]
    if not values:
        return None
    if aggregation == "sum":
        return sum(values) if all(isinstance(value, int) for value in values) else math.fsum(values)
    if aggregation == "mean":
        return math.fsum(values)/len(values)
    return statistics.median(values)


def _periods(series, timezone, frequency):
    """Independently parse dates; do not reuse analysis.parse_dates."""
    import pandas as pd
    if pd.api.types.is_numeric_dtype(series):
        raise ValueError("数值日期单位不明确。")
    dates = series.map(lambda value: pd.to_datetime(value, errors="coerce"))
    valid = dates.dropna()
    aware = [value.tzinfo is not None for value in valid]
    if any(aware) and not all(aware):
        raise ValueError("日期混合带时区和不带时区的值。")
    if any(aware):
        dates = pd.to_datetime(dates, utc=True).dt.tz_convert(timezone)
    else:
        dates = pd.to_datetime(dates).dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
    return dates.dt.tz_localize(None).dt.to_period(frequency)


def _valid_source(source):
    return (isinstance(source, dict) and isinstance(source.get("path"), str) and bool(source["path"].strip())
            and isinstance(source.get("sha256"), str) and re.fullmatch(r"[0-9a-fA-F]{64}", source["sha256"]) is not None
            and isinstance(source.get("rows"), int) and not isinstance(source["rows"], bool) and source["rows"] >= 0
            and isinstance(source.get("columns"), list) and bool(source["columns"])
            and all(isinstance(c, str) and c for c in source["columns"])
            and len(set(source["columns"])) == len(source["columns"]))


def _claim_number(value):
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))


def validate_bundle(bundle_path, verify_source=True):
    """Malformed external bundles become failed results, never a ready badge."""
    try:
        return _validate_bundle(bundle_path, verify_source)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, ImportError) as exc:
        return {"status": "failed", "checks_status": "failed", "created_at": utc_now(),
                "checks": [{"name": "bundle_contract", "status": "failed", "detail": str(exc)}],
                "coverage": {key: {"status": "not_checked", "detail": "成果结构或输入无效，验证未完成。"}
                             for key in ("source", "calculations", "claims", "narrative", "visual")},
                "scope": "成果结构或输入无效；不代表任何业务或视觉审阅已完成。"}


def _validate_bundle(bundle_path, verify_source=True):
    path = Path(bundle_path).expanduser().resolve()
    checks = []
    coverage = {
        "source": {"status": "not_checked", "detail": "尚未读取源数据。"},
        "calculations": {"status": "not_checked", "checked": [], "not_checked": []},
        "claims": {"status": "not_checked", "checked": 0, "detail": "没有结构化声明。"},
        "narrative": {"status": "not_checked", "detail": "自由叙述、业务解释、因果语言和建议仍需独立审阅；结构化声明通过也不证明这些内容正确。"},
        "visual": {"status": "not_checked", "detail": "仅检查图像文件/数据表引用，不检查图轴、像素内容、交互或Word版式。"},
    }
    def check(name, ok, detail):
        checks.append({"name": name, "status": "passed" if ok else "failed", "detail": detail})
    def warning(name, detail):
        checks.append({"name": name, "status": "warning", "detail": detail})
    def finish():
        state = "failed" if any(c["status"] == "failed" for c in checks) else "warning" if any(c["status"] == "warning" for c in checks) else "passed"
        return {"status": "failed" if state == "failed" else "needs_review", "checks_status": state,
                "created_at": utc_now(), "checks": checks, "coverage": coverage,
                "scope": "已执行检查的结果，不是整份分析已获批准；自由叙述与视觉始终单独标记未检查。"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        check("bundle_read", False, str(exc))
        return finish()
    if not isinstance(data, dict):
        check("schema", False, "成果必须为JSON对象。")
        return finish()
    check("schema", data.get("schema_version") in {"1.0", "1.1"}, "支持成果版本1.0/1.1。")
    check("title", isinstance(data.get("title"), str) and bool(data["title"].strip()), "需要非空标题。")
    source = data.get("source")
    source_valid = _valid_source(source)
    check("source_metadata", source_valid, "需要源路径、SHA-256、非负行数和唯一字段名列表。")
    tables, charts = data.get("tables", []), data.get("charts", [])
    summary, findings = data.get("summary", []), data.get("findings", [])
    schema_lists = all(isinstance(value, list) for value in (tables, charts, summary, findings))
    check("content_lists", schema_lists, "tables/charts/summary/findings应为列表。")
    if not schema_lists:
        return finish()
    check("actual_content", bool(tables or charts or any(isinstance(v, str) and v.strip() for v in summary)
                                  or any(isinstance(v, dict) and v.get("text") for v in findings)
                                  or (isinstance(data.get("profile"), dict) and data["profile"].get("columns"))), "成果需要实际表格、图形、分析文字或数据画像。")
    check("summary_shape", all(isinstance(v, str) for v in summary), "摘要每项为文字。")
    check("findings_shape", all(isinstance(v, dict) and isinstance(v.get("text", ""), str) for v in findings), "发现应为含文字的对象。")
    table_map = {}
    for index, table in enumerate(tables):
        identifier = table.get("id") if isinstance(table, dict) else None
        label = identifier if isinstance(identifier, str) else str(index)
        valid = (isinstance(table, dict) and isinstance(identifier, str) and bool(identifier)
                 and isinstance(table.get("columns"), list) and bool(table["columns"])
                 and all(isinstance(c, str) and c for c in table["columns"])
                 and len(set(table["columns"])) == len(table["columns"])
                 and isinstance(table.get("rows"), list)
                 and all(isinstance(row, list) and len(row) == len(table["columns"]) for row in table["rows"]))
        check("table_shape:" + label, valid, "表格需要唯一字段名，且每行宽度与表头一致。")
        if not valid:
            continue
        check("table_id_unique:" + identifier, identifier not in table_map, "表格标识唯一。")
        table_map[identifier] = table
        if table.get("source_file"):
            try:
                with safe_bundle_path(path.parent, table["source_file"]).open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.reader(stream, strict=True))
                ok = bool(rows) and rows[0] == table["columns"] and len(rows)-1 == len(table["rows"])
                ok = ok and all(len(actual) == len(expected) == len(table["columns"])
                                and all(_same(a, b) for a, b in zip(expected, actual))
                                for expected, actual in zip(table["rows"], rows[1:]))
            except (OSError, ValueError, TypeError, csv.Error):
                ok = False
            check("table_file:" + identifier, ok, "JSON结果表与CSV行数、列数和值一致。")
    for index, chart in enumerate(charts):
        if not isinstance(chart, dict) or not isinstance(chart.get("path"), str):
            check("chart_shape:" + str(index), False, "图表需要路径。")
            continue
        try:
            exists = safe_bundle_path(path.parent, chart["path"]).is_file()
        except (OSError, ValueError, TypeError):
            exists = False
        check("chart_file:" + chart["path"], exists, "图像存在且位于成果目录内。")
        if chart.get("table_id") is not None:
            check("chart_source:" + chart["path"], isinstance(chart.get("table_id"), str) and chart["table_id"] in table_map,
                  "图表关联结果表存在；不验证像素或图轴。")
        elif isinstance(chart.get("source_file"), str):
            try:
                valid_chart_source = safe_bundle_path(path.parent, chart["source_file"]).is_file()
            except (OSError, ValueError, TypeError):
                valid_chart_source = False
            check("chart_source_file:" + chart["path"], valid_chart_source,
                  "图表关联的独立数据文件存在；本项不复算文件或验证图形。")
        else:
            check("chart_source:" + chart["path"], False, "图表需要结果表或独立数据文件引用。")
    frame = None
    if verify_source and source_valid:
        source_path = Path(source["path"]).expanduser()
        if not source_path.is_absolute():
            source_path = path.parent / source_path
        if not source_path.is_file():
            warning("source_available", "原始数据不可访问，未独立复算。")
            coverage["source"] = {"status": "not_checked", "detail": "源文件不存在或不可访问。"}
        else:
            try:
                expected_hash = source["sha256"].lower()
                stable = file_sha256(source_path) == expected_hash
                check("source_hash", stable, "源文件与分析时SHA-256一致。")
                if stable:
                    from .io import load_frame
                    frame = load_frame(source_path, sheet=source.get("sheet"))
                    after = file_sha256(source_path) == expected_hash
                    check("source_stable_during_validation", after, "验证读取前后源文件未改变。")
                    check("source_rows", len(frame) == source["rows"], "源行数与记录一致。")
                    check("source_columns", list(frame.columns) == source["columns"], "源字段与记录一致。")
                    if not after:
                        frame = None
                source_checks = [c for c in checks if c["name"] in {"source_hash", "source_stable_during_validation", "source_rows", "source_columns"}]
                coverage["source"] = {"status": "passed" if stable and frame is not None and all(c["status"] == "passed" for c in source_checks) else "failed", "detail": "指纹、读取稳定性及源数据结构已检查。"}
            except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
                check("source_read", False, str(exc))
                coverage["source"]["status"] = "failed"
    elif not verify_source:
        coverage["source"] = {"status": "not_checked", "detail": "调用者关闭源数据验证。"}
    metric = data.get("metric")
    if metric is not None and not isinstance(metric, dict):
        check("metric_shape", False, "metric必须为对象。")
        metric = None
    elif isinstance(metric, dict):
        check("metric_value_type", "value" in metric and _claim_number(metric["value"]), "指标值必须为有限数值或null，不接受布尔值/字符串。")
    definition = data.get("metric_definition")
    if definition is not None and not isinstance(definition, dict):
        check("metric_definition", False, "metric_definition必须为对象。")
        definition = None
    elif isinstance(definition, dict):
        check("metric_definition", _definition_valid(definition), "指标定义、过滤语法及展示单位必须符合规范。")
        check("metric_result", isinstance(metric, dict) and "value" in metric and _claim_number(metric["value"]), "声明指标定义时必须提供有限数值或null的计算结果。")
    spec = definition if isinstance(definition, dict) else metric
    calc_names = []
    if isinstance(spec, dict) and isinstance(metric, dict) and (spec.get("column") or spec.get("aggregation") in {"count", "count_rows"}):
        if frame is not None:
            try:
                work = _filtered(frame, spec.get("filters", []))
                actual = _calculate(work, spec)
                check("independent_total", "value" in metric and _same(actual, metric.get("value")), "独立复算完整过滤后的总体指标。")
                calc_names.append("independent_total")
                import pandas as pd
                group = metric.get("group")
                if group:
                    if group not in work:
                        raise ValueError(f"分组字段不存在：{group}")
                    if data.get("schema_version") == "1.1":
                        _independent_group_labels(frame[group])
                        labels = _independent_group_labels(work[group])
                    else:
                        labels = work[group].map(lambda value: "(缺失)" if pd.isna(value) else str(value))
                    if work[group].isna().any() and "(缺失)" in work[group].dropna().astype(str).tolist():
                        raise ValueError("缺失分组标签与字面类别冲突。")
                    expected = [[label, _calculate(work.loc[labels == label], spec), int((labels == label).sum())]
                                for label in sorted(labels.unique())]
                    table = table_map.get("by_group")
                    if table is None and data.get("schema_version") == "1.0":
                        warning("legacy_group_not_checked", "旧版成果未保留标准by_group表；自定义分组呈现未独立复算。")
                        coverage["calculations"]["not_checked"].append("旧版自定义分组表：缺少标准by_group计算证据。")
                    else:
                        ok = bool(table) and table["columns"] == [group, "指标值", "记录数"] and _rows_same(expected, table["rows"])
                        check("independent_by_group", ok, "按源数据独立复算全部分组与每组记录数；1.1需保留标准计算表。")
                        calc_names.append("independent_by_group")
                date = metric.get("date")
                if date:
                    if date not in work:
                        raise ValueError(f"日期字段不存在：{date}")
                    frequency = metric.get("frequency", "M")
                    periods = _periods(work[date], metric.get("timezone", "UTC"), frequency)
                    present = periods.dropna()
                    expected = []
                    if not present.empty:
                        for period in pd.period_range(present.min(), present.max(), freq=frequency):
                            selected = periods == period
                            expected.append([str(period), _calculate(work.loc[selected], spec) if selected.any() else None, int(selected.sum())])
                    table = table_map.get("by_period")
                    if expected and table is None and data.get("schema_version") == "1.0":
                        warning("legacy_period_not_checked", "旧版成果未保留标准by_period表；自定义趋势呈现未独立复算。")
                        coverage["calculations"]["not_checked"].append("旧版自定义趋势表：缺少标准by_period计算证据。")
                    else:
                        ok = (bool(table) and table["columns"] == ["期间", "指标值", "记录数"] and _rows_same(expected, table["rows"])) if expected else table is None or table["rows"] == []
                        check("independent_by_period", ok, "按时区/周期独立复算趋势，缺失周期不作零值；1.1需保留标准计算表。")
                        calc_names.append("independent_by_period")
            except (OSError, ValueError, TypeError, KeyError, ImportError, OverflowError) as exc:
                check("independent_calculations", False, str(exc))
                calc_names.append("independent_calculations")
        else:
            coverage["calculations"]["not_checked"].append("源数据未验证，指标/分组/趋势未独立复算。")
    else:
        coverage["calculations"]["not_checked"].append("未提供可独立复算的指标定义；模型指标等其他计算不在本验证器覆盖范围。")
    covered_tables = {identifier for identifier in ("by_group", "by_period") if "independent_" + identifier in calc_names}
    coverage["calculations"]["checked"] = calc_names
    coverage["calculations"]["not_checked"].extend("表格计算：" + key for key in table_map if key not in covered_tables)
    if data.get("computation") is not None:
        coverage["calculations"]["not_checked"].append("computation中的有效/排除样本数及原因明细；本项只复算指标值和分组/周期记录数。")
    if calc_names:
        coverage["calculations"]["status"] = "failed" if any(c["status"] == "failed" and c["name"] in calc_names for c in checks) else "partial" if coverage["calculations"]["not_checked"] else "passed"
    _check_quality(data.get("quality_rules"), frame, check, coverage)
    quality_checks = [c for c in checks if c["name"].startswith("quality:")]
    if quality_checks:
        coverage["source"]["quality_checks"] = [c["name"] for c in quality_checks]
        coverage["source"]["quality_status"] = "failed" if any(c["status"] == "failed" for c in quality_checks) else "passed"
    if calc_names and coverage["calculations"]["status"] == "passed" and coverage["calculations"]["not_checked"]:
        coverage["calculations"]["status"] = "partial"
    claims = data.get("claims", [])
    if not isinstance(claims, list):
        check("claims_shape", False, "claims必须为列表。")
        coverage["claims"]["status"] = "failed"
    elif claims:
        texts = [text for text in summary if isinstance(text, str)] + [v.get("text") for v in findings if isinstance(v, dict)]
        ids = set()
        passed_claims = 0
        for index, claim in enumerate(claims):
            identifier = claim.get("id") if isinstance(claim, dict) else None
            try:
                if not isinstance(claim, dict) or not isinstance(identifier, str) or not identifier or identifier in ids:
                    raise ValueError("声明需要唯一非空id。")
                ids.add(identifier)
                if claim.get("kind") != "observation" or not _claim_number(claim.get("value")) or "value" not in claim:
                    raise ValueError("声明kind只能为observation，value应为有限数值或null。")
                evidence = claim.get("evidence")
                if not isinstance(evidence, dict):
                    raise ValueError("声明缺少有效证据引用。")
                if set(evidence) == {"metric"} and evidence["metric"] == "value" and isinstance(metric, dict) and "value" in metric:
                    supported = metric["value"]
                elif set(evidence) == {"table_id", "row", "column"}:
                    table = table_map.get(evidence["table_id"])
                    row, column = evidence["row"], evidence["column"]
                    if not table or not isinstance(row, int) or isinstance(row, bool) or row < 0 or row >= len(table["rows"]) or column not in table["columns"]:
                        raise ValueError("声明引用的表格、行或字段无效。")
                    supported = table["rows"][row][table["columns"].index(column)]
                else:
                    raise ValueError("声明证据引用不受支持。")
                if not _claim_number(supported) or not _same(claim["value"], supported):
                    raise ValueError("声明数值与所引用的计算证据不一致。")
                template = claim.get("template")
                if not isinstance(template, str):
                    raise ValueError("声明缺少template。")
                parts = list(string.Formatter().parse(template))
                fields = [(field, specifier, conversion) for literal, field, specifier, conversion in parts if field is not None]
                if fields != [("value", "", None)]:
                    raise ValueError("声明template仅允许一个{value}占位符，不允许其他字段或格式表达式。")
                format_spec = claim.get("format")
                if not isinstance(format_spec, dict) or not set(format_spec) <= {"unit", "precision", "currency"}:
                    raise ValueError("声明format仅支持unit/precision/currency。")
                from .metrics import format_value
                rendered = template.format(value=format_value(claim["value"], format_spec, preferences=data.get("preferences")))
                if claim.get("text") != rendered:
                    raise ValueError("声明text与统一格式化结果不一致。")
                if not any(isinstance(text, str) and rendered in text for text in texts):
                    raise ValueError("声明未出现在摘要或发现正文；可能已被替换。")
                check("claim:" + identifier, True, "数值、证据引用、格式化文本及正文位置一致。")
                passed_claims += 1
            except (ValueError, TypeError, KeyError, IndexError, ImportError, OverflowError) as exc:
                check("claim:" + (identifier if isinstance(identifier, str) else str(index)), False, str(exc))
        coverage["claims"] = {"status": "passed" if passed_claims == len(claims) else "failed", "checked": len(claims), "passed": passed_claims,
                              "detail": "只验证结构化数值声明；证据计算是否独立复核见calculations，语义和因果仍见narrative。"}
    return finish()


def _rows_same(expected, actual):
    return len(expected) == len(actual) and all(len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b)) for a, b in zip(expected, actual))


def _check_quality(rules, frame, check, coverage):
    if rules is None:
        return
    if not isinstance(rules, dict) or not set(rules) <= {"min_rows", "required_columns", "unique_key", "max_missing_fraction"}:
        check("quality_rules", False, "quality_rules包含不支持的设置。")
        return
    if frame is None:
        coverage["calculations"]["not_checked"].append("源数据质量阈值：源数据不可验证。")
        return
    if "min_rows" in rules:
        value = rules["min_rows"]
        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        check("quality:min_rows", valid and len(frame) >= value, "按完整源数据独立检查最小行数。")
    if "required_columns" in rules:
        names = rules["required_columns"]
        valid = isinstance(names, list) and all(isinstance(c, str) and c for c in names)
        check("quality:required_columns", valid and all(c in frame for c in names), "检查必需字段。")
    if "unique_key" in rules:
        names = rules["unique_key"]
        if names != []:  # An empty configured key explicitly disables this rule.
            valid = isinstance(names, list) and bool(names) and all(isinstance(c, str) and c in frame for c in names)
            check("quality:unique_key", valid and not frame[names].isna().any(axis=None) and not frame.duplicated(names).any(), "复合主键不得为空或重复。")
    if "max_missing_fraction" in rules:
        thresholds = rules["max_missing_fraction"]
        if not isinstance(thresholds, dict):
            check("quality:max_missing_fraction", False, "缺失率阈值应为按列映射。")
        else:
            for column, threshold in thresholds.items():
                valid = (isinstance(column, str) and column in frame and isinstance(threshold, (float, int))
                         and not isinstance(threshold, bool) and math.isfinite(threshold) and 0 <= threshold <= 1)
                actual = float(frame[column].isna().mean()) if valid and len(frame) else None
                check("quality:max_missing_fraction:" + str(column), valid and actual is not None and actual <= threshold,
                      "按完整源数据独立检查缺失比例。")
