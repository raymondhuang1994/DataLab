"""Explicit project quality gates; never infer business rules from field names."""
def check_quality(frame, rules=None):
    rules = rules or {}
    checks = []
    def add(name, ok, detail):
        checks.append({"name": name, "status": "passed" if ok else "failed", "detail": detail})
    for column in rules.get("required_columns", []):
        add("required:" + column, column in frame, f"必需字段 {column}")
    if "min_rows" in rules:
        add("minimum_rows", len(frame) >= rules["min_rows"], f"源数据 {len(frame)} 行；要求至少 {rules['min_rows']} 行。")
    keys = rules.get("unique_key", [])
    if keys:
        present = all(k in frame for k in keys)
        valid = present and not frame[keys].isna().any(axis=None) and not frame.duplicated(keys).any()
        add("unique_key", bool(valid), f"主键 {keys} 必须存在、非空且组合唯一。")
    for column, maximum in rules.get("max_missing_fraction", {}).items():
        fraction = float(frame[column].isna().mean()) if column in frame and len(frame) else None
        add("missing_fraction:" + column, fraction is not None and fraction <= maximum,
            f"{column} 缺失比例 {fraction}；上限 {maximum}。")
    return {"status": "failed" if any(c["status"] == "failed" for c in checks) else "passed", "checks": checks}
