"""Explicit grouping labels shared by reports and dashboard payloads."""
from datetime import date, datetime
import math
from numbers import Real


def group_labels(series):
    import pandas as pd
    kinds = set()
    def label(value):
        if pd.isna(value):
            return "(缺失)"
        if isinstance(value, (bool,)) or type(value).__name__ == "bool_":
            kinds.add("boolean")
            return "true" if value else "false"
        if isinstance(value, Real):
            kinds.add("number")
            if not math.isfinite(value):
                raise ValueError(f"分组字段 {series.name} 含非有限数值，请先明确类别定义。")
            return str(int(value)) if value == int(value) else str(value)
        if isinstance(value, str):
            kinds.add("text")
            return value
        if isinstance(value, (datetime, date)):
            kinds.add("datetime")
            return value.isoformat()
        raise ValueError(f"分组字段 {series.name} 含不支持的类别类型；请显式转换。")
    labels = series.map(label)
    if len(kinds) > 1:
        raise ValueError(f"分组字段 {series.name} 混合类别类型 {sorted(kinds)}；请明确统一为文本、数值、布尔或日期，避免合并同名的不同类别。")
    if series.isna().any() and ((series.notna()) & (labels == "(缺失)")).any():
        raise ValueError("类别包含字面值 '(缺失)' 且存在空值；请先指定不冲突的标签。")
    return labels
