"""Deterministic descriptive analysis, never an automatic causal diagnosis."""
from __future__ import annotations
import csv
import importlib.metadata
import json
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from . import __version__
from .common import file_sha256, json_safe, utc_now, write_json
from .io import load_frame
from .profile import profile_frame
from .metrics import compute_metric, filter_frame, format_value, legacy_metric, normalize_metric
from .quality import check_quality
from .grouping import group_labels
from .safety import output_session, safe_open


def parse_dates(series, timezone="UTC"):
    if pd.api.types.is_numeric_dtype(series):
        raise ValueError("日期列为数值；请先明确 Excel 序号或 Unix 时间戳的单位并转换。")
    parsed = series.map(lambda value: pd.to_datetime(value, errors="coerce"))
    valid = parsed.dropna()
    aware = valid.map(lambda value: value.tzinfo is not None)
    if aware.any() and not aware.all():
        raise ValueError("日期混合了带时区和不带时区的值；请先统一时区，避免跨日归类错误。")
    if aware.any():
        return pd.to_datetime(parsed, utc=True).dt.tz_convert(timezone)
    return pd.to_datetime(parsed).dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")


def metric_value(frame, metric, agg, weight=None, denominator=None):
    return compute_metric(frame, legacy_metric(metric, agg, weight, denominator))["value"]


def _save_table(out, identifier, title, frame):
    path = out / "tables" / f"{identifier}.csv"
    # Raw CSV is a machine-readable data artifact, not an executable workbook.
    with safe_open(path, encoding="utf-8-sig", newline="") as stream:
        frame.to_csv(stream, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return {"id": identifier, "title": title, "columns": [str(c) for c in frame.columns],
            "rows": json_safe(frame.values.tolist()), "source_file": f"tables/{identifier}.csv"}


def analyze(input_path, output_dir, metric=None, group=None, date=None, agg="sum", weight=None,
            denominator=None, sheet=None, key=None, title="DataLab 数据分析报告", timezone="UTC", freq="M", charts=True,
            metric_definition=None, preferences=None, quality_rules=None, settings_provenance=None):
    options = dict(locals())
    inputs = []
    for value in (settings_provenance or {}).get("files", {}).values():
        if isinstance(value, dict) and value.get("path"):
            inputs.append(value["path"])
    with output_session(output_dir, inputs=inputs, raw_inputs=[input_path], directory=True):
        return _analyze_impl(**options)


def _analyze_impl(input_path, output_dir, metric=None, group=None, date=None, agg="sum", weight=None,
                  denominator=None, sheet=None, key=None, title="DataLab 数据分析报告", timezone="UTC", freq="M", charts=True,
                  metric_definition=None, preferences=None, quality_rules=None, settings_provenance=None):
    out = Path(output_dir).resolve()
    source = Path(input_path).resolve()
    original_hash = file_sha256(source)
    df = load_frame(source, sheet=sheet)
    if file_sha256(source) != original_hash:
        raise ValueError("读取期间源文件发生变化，请在文件保存完成后重试。")
    preferences = preferences or {}
    definition = normalize_metric(metric_definition) if metric_definition is not None else (
        legacy_metric(metric, agg, weight, denominator) if metric or agg in ("count", "count_rows", "count_nonmissing", "count_distinct") else None)
    if definition is not None:
        metric, agg = definition.get("column"), definition["aggregation"]
        weight, denominator = definition.get("weight"), definition.get("denominator")
    requested = [metric, group, date, weight, denominator]
    for col in [c for c in requested if c]:
        if col not in df:
            raise ValueError(f"字段不存在: {col}；可用字段: {list(df.columns)}")
    if agg not in ("sum", "mean", "median", "count_rows", "count_nonmissing", "count_distinct", "weighted_mean", "ratio"):
        raise ValueError(f"不支持的聚合: {agg}")
    if not definition and any([group, date, weight, denominator]):
        raise ValueError("分组或趋势分析需要显式 --metric；按记录数统计请使用 --agg count。")
    if agg == "weighted_mean" and not weight:
        raise ValueError("weighted_mean 需要 --weight。")
    if agg == "ratio" and not denominator:
        raise ValueError("ratio 需要 --denominator；按合计分子/合计分母计算。")
    if agg != "weighted_mean" and weight:
        raise ValueError("--weight 仅用于 --agg weighted_mean。")
    if agg != "ratio" and denominator:
        raise ValueError("--denominator 仅用于 --agg ratio。")
    quality = check_quality(df, quality_rules)
    if quality["status"] == "failed":
        raise ValueError("项目数据质量规则未通过：" + "；".join(c["detail"] for c in quality["checks"] if c["status"] == "failed"))
    # Fail before writing if the selected metric or filter is invalid.
    computation = compute_metric(df, definition) if definition is not None else None
    if group:
        group_labels(df[group])  # Validate the whole source before writing output.
    profile = profile_frame(df, key=key)
    limitations = [issue["detail"] for issue in profile["issues"]]
    limitations += ["自动摘要描述观测数据，不能单独证明因果关系。", "业务口径、样本代表性和数据完整性仍需结合上下文审阅。"]
    if source.suffix.lower() == ".xlsx":
        limitations.append("Excel 按保存的缓存值读取，未重新计算公式；公式单元格无缓存时可能为空。")
    tables, findings, generated_charts, claims = [], [], [], []
    summary = [f"读取 {len(df):,} 条记录、{len(df.columns)} 个字段；未自动删除重复记录或异常值。"]
    numeric_rows = [[c["name"], c["stats"].get("count"), c["stats"].get("mean"), c["stats"].get("median"), c["stats"].get("min"), c["stats"].get("max")] for c in profile["columns"] if c["kind"] == "numeric"]
    tables.append(_save_table(out, "profile", "字段质量概览", pd.DataFrame([[c["name"], c["dtype"], c["missing"], c["unique"]] for c in profile["columns"]], columns=["字段", "类型", "缺失数", "不同值数量"])))
    if numeric_rows:
        tables.append(_save_table(out, "numeric_summary", "数值字段概览", pd.DataFrame(numeric_rows, columns=["字段", "有效数", "均值", "中位数", "最小值", "最大值"])))
    metric_spec = {"column": metric, "aggregation": agg, "weight": weight, "denominator": denominator, "group": group, "date": date, "frequency": freq, "timezone": timezone}
    number_format = {k: definition[k] for k in ("unit", "precision", "currency") if definition is not None and k in definition}
    def claim(identifier, template, value, evidence):
        text = template.format(value=format_value(value, number_format, preferences))
        claims.append({"id": identifier, "kind": "observation", "template": template, "text": text,
                       "value": json_safe(value), "format": number_format, "evidence": evidence})
        return text
    checks = [{"name": "source_stable", "status": "passed", "detail": "读取前后源文件 SHA-256 一致。"}]
    work = filter_frame(df, definition.get("filters", [])) if definition is not None else df.copy()
    if definition is not None:
        total = computation["value"]
        label = definition.get("label") or metric or "记录数"
        safe_label = label.replace("{", "{{").replace("}", "}}")
        summary.append(claim("overall_metric", f"{safe_label}（{agg}）＝" + "{value}。", total, {"metric": "value"}))
        if computation["eligible_rows"] != len(df):
            limitations.append(f"项目指标过滤后纳入 {computation['eligible_rows']} / {len(df)} 条源记录；画像仍展示原始数据。")
        if computation["valid_rows"] < computation["eligible_rows"]:
            limitations.append("指标计算存在缺失或无效记录；比例和加权平均使用共同有效记录，未将缺失当作零。")
        if total is None:
            checks.append({"name": "metric_defined", "status": "warning", "detail": "指标为空，不能当作零值。"})
        if group:
            group_key = "__datalab_group"
            while group_key in work:
                group_key += "_"
            work[group_key] = group_labels(work[group])
            values = [[label, compute_metric(part, definition)["value"], len(part)] for label, part in work.groupby(group_key, sort=True, dropna=False)]
            group_frame = pd.DataFrame(values, columns=[group, "指标值", "记录数"])
            table = _save_table(out, "by_group", f"按 {group} 分组", group_frame)
            table["formats"] = {"指标值": number_format}
            tables.append(table)
            if agg in ("sum", "count_rows", "count_nonmissing") and total is not None:
                subtotal = math.fsum(v[1] for v in values if v[1] is not None)
                ok = math.isclose(subtotal, float(total), rel_tol=1e-10, abs_tol=1e-8)
                checks.append({"name": "group_reconciliation", "status": "passed" if ok else "failed", "detail": f"分组合计 {subtotal}，总体 {total}。"})
            valid = group_frame.dropna(subset=["指标值"])
            if not valid.empty:
                leader = valid.sort_values("指标值", ascending=False).iloc[0]
                leader_index = int(leader.name)
                leader_display = format_value(leader["指标值"], number_format, preferences)
                displayed_ties = valid["指标值"].map(lambda value: format_value(value, number_format, preferences)).eq(leader_display).sum()
                if displayed_ties > 1:
                    heading = f"按当前显示精度，{group} 中多个类别的最高展示值相同"
                    template = "这些类别均显示为 {value}；原始数值的更细差异保留在计算表，不能凭舍入后的展示区分高低。"
                else:
                    heading = f"{group} 中指标最高的类别为 {leader[group]}"
                    template = "按所选指标口径，该组指标值为 {value}；此比较不代表因果解释。"
                text = claim("highest_group", template, leader["指标值"], {"table_id": "by_group", "row": leader_index, "column": "指标值"})
                findings.append({"title": heading, "text": text, "evidence": {"claim_id": "highest_group", "table_id": "by_group"}})
            if charts:
                from .charts import chart_table
                generated_charts.append(chart_table(table, out / "charts", "by_group", ylabel=f"{label} · {agg}", metric_spec=definition, preferences=preferences))
        if date:
            parsed = parse_dates(work[date], timezone)
            invalid = int(parsed.isna().sum())
            if invalid:
                limitations.append(f"日期列 {date} 有 {invalid} 条缺失/无法解析；这些记录保留在总体统计，但未进入时间趋势。")
            period = parsed.dt.tz_localize(None).dt.to_period(freq)
            period_key = "__datalab_period"
            while period_key in work:
                period_key += "_"
            work[period_key] = period
            values = [(p, compute_metric(part, definition)["value"], len(part)) for p, part in work.dropna(subset=[period_key]).groupby(period_key, sort=True)]
            if values:
                indexed = {p: (v, n) for p, v, n in values}
                periods = pd.period_range(values[0][0], values[-1][0], freq=freq)
                rows = [[str(p), *indexed.get(p, (None, 0))] for p in periods]
                table = _save_table(out, "by_period", f"按 {freq} 周期观察 {metric or '记录数'}", pd.DataFrame(rows, columns=["期间", "指标值", "记录数"]))
                table["formats"] = {"指标值": number_format}
                tables.append(table)
                if any(p not in indexed for p in periods):
                    limitations.append("时间序列有缺失周期，图表显示为空而非零；未自动填补。")
                limitations.append("首尾周期可能不完整；未据此自动生成同比/环比或增长结论。")
                if charts:
                    from .charts import chart_table
                    generated_charts.append(chart_table(table, out / "charts", "by_period", "line", ylabel=f"{label} · {agg}", metric_spec=definition, preferences=preferences))
            else:
                limitations.append("日期列没有有效日期，无法生成趋势。")
    else:
        total = None
        limitations.append("未指定业务指标，当前仅输出数据画像；字段含义和分析问题应由使用者明确。")
    config = {"input_path": str(source), "metric": metric, "group": group, "date": date, "agg": agg,
              "weight": weight, "denominator": denominator, "sheet": sheet, "key": key,
              "title": title, "timezone": timezone, "freq": freq, "charts": charts,
              "metric_definition": definition, "preferences": preferences, "quality_rules": quality_rules or {}, "settings_provenance": settings_provenance or {}}
    result = {"schema_version": "1.1", "title": title, "created_at": utc_now(),
              "source": {"path": str(source), "sha256": original_hash, "rows": len(df), "columns": list(df.columns), "sheet": sheet},
              "profile": profile, "metric": {**metric_spec, "value": total}, "summary": summary,
              "metric_definition": definition, "computation": computation, "claims": claims,
              "preferences": preferences, "quality_rules": quality_rules or {}, "quality": quality,
              "settings_provenance": settings_provenance or {},
              "findings": findings, "tables": tables, "charts": [c for c in generated_charts if c],
              "limitations": list(dict.fromkeys(limitations)),
              "methodology": ["源文件只读打开；运行前后指纹核对见本次 audit/file-safety 记录。", f"指标口径：{json.dumps(metric_spec, ensure_ascii=False)}", "保留缺失类别；不自动去重、不删除异常值；每张图均关联结果数据表。"],
              "validation": {"status": "needs_review", "checks": checks, "note": "自动检查只覆盖计算与文件一致性，不等于业务和统计方法已完成独立审阅。"},
              "runtime": {"datalab": __version__, "python": sys.version.split()[0], "packages": {p: importlib.metadata.version(p) for p in ("pandas", "numpy")}}}
    write_json(out / "config.json", config)
    write_json(out / "result.json", result)
    from .report import render_html
    render_html(out / "result.json", out / "report.html")
    return result
