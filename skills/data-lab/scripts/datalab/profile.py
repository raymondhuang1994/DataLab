"""Inspectable data quality and distribution summaries; does not clean source rows."""
from __future__ import annotations
import numpy as np
import pandas as pd
from .common import json_safe


def profile_frame(df, key=None):
    columns, issues = [], []
    duplicate_rows = int(df.duplicated().sum())
    if duplicate_rows:
        issues.append({"kind": "duplicate_rows", "severity": "warning", "count": duplicate_rows,
                       "detail": "存在完全重复记录；未自动删除，需结合一行代表的业务含义判断。"})
    for name in df:
        s = df[name]
        missing = int(s.isna().sum())
        item = {"name": str(name), "dtype": str(s.dtype), "missing": missing,
                "missing_rate": missing / len(df), "unique": int(s.nunique(dropna=True))}
        if missing:
            issues.append({"kind": "missing", "column": str(name), "severity": "warning", "count": missing,
                           "detail": f"字段 {name} 有 {missing} 条缺失；缺失并非零值。"})
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            finite = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
            item["kind"] = "numeric"
            item["nonfinite"] = int(s.notna().sum() - len(finite))
            item["stats"] = json_safe({"count": len(finite), "mean": finite.mean(), "median": finite.median(),
                                      "std": finite.std(ddof=1), "min": finite.min(), "max": finite.max(),
                                      "p25": finite.quantile(.25), "p75": finite.quantile(.75),
                                      "p95": finite.quantile(.95), "zeros": int((finite == 0).sum()),
                                      "negative": int((finite < 0).sum())})
            if len(finite):
                q1, q3 = finite.quantile([.25, .75]); iqr = q3 - q1
                item["iqr_outliers"] = int(((finite < q1 - 1.5 * iqr) | (finite > q3 + 1.5 * iqr)).sum()) if iqr else 0
            if item["nonfinite"]:
                issues.append({"kind": "nonfinite", "column": str(name), "severity": "warning",
                               "count": item["nonfinite"], "detail": "无穷值未纳入数值统计。"})
        else:
            item["kind"] = "datetime" if pd.api.types.is_datetime64_any_dtype(s) else "text"
            item["top_values"] = [{"value": json_safe(v), "count": int(n)} for v, n in s.value_counts().head(10).items()]
            if item["kind"] == "text":
                text = s.dropna().astype(str)
                whitespace = int((text != text.str.strip()).sum())
                item["whitespace_values"] = whitespace
                if whitespace:
                    issues.append({"kind": "whitespace", "column": str(name), "severity": "warning", "count": whitespace,
                                   "detail": "文本包含首尾空格；未自动合并可能不同的类别。"})
        columns.append(item)
    key_info = None
    if key:
        keys = [key] if isinstance(key, str) else key
        missing_keys = [c for c in keys if c not in df]
        if missing_keys:
            raise ValueError(f"主键字段不存在: {missing_keys}")
        key_info = {"columns": keys, "missing_rows": int(df[keys].isna().any(axis=1).sum()),
                    "duplicate_rows": int(df.duplicated(keys, keep=False).sum())}
        if key_info["missing_rows"] or key_info["duplicate_rows"]:
            issues.append({"kind": "key_integrity", "severity": "warning", "detail": "指定主键有缺失或不唯一。", **key_info})
    return {"rows": len(df), "column_count": len(df.columns), "columns": columns,
            "duplicate_rows": duplicate_rows, "key": key_info, "issues": issues,
            "notes": ["质量标记是待调查的问题，不代表记录一定错误。", "未自动去重、插补、删除异常值或转换百分比单位。"]}
