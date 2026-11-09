"""Explicit project/presentation JSON configuration; no implicit home lookup."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .metrics import legacy_metric, normalize_metric


DEFAULT_PREFERENCES = {
    "schema_version": "1.0",
    "report": {"title": "DataLab 数据分析报告", "page_size": "A4", "cjk_font": None},
    "charts": {"color": "#286B85"},
    "display": {"precision": 2, "missing_label": "—"},
    "dashboard": {"interval": 5, "max_rows": 50000},
}
DEFAULT_QUALITY = {"required_columns": [], "unique_key": [], "max_missing_fraction": {}, "min_rows": 0}
DEFAULT_ANALYSIS = {"metric": None, "agg": "sum", "weight": None, "denominator": None,
                    "group": None, "date": None, "sheet": None, "key": None,
                    "timezone": "UTC", "freq": "M", "title": "DataLab 数据分析报告"}
BUSINESS_OVERRIDES = frozenset({"metric", "agg", "weight", "denominator", "filters"})
OVERRIDE_KEYS = frozenset(DEFAULT_ANALYSIS) | {"filters", "unit", "currency", "precision"}


def _object(value, allowed, context):
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {context} keys: {sorted(unknown, key=str)}")


def _name(value, context, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a nonempty string.")
    return value


def _columns(value, context, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a field name or list of field names.")
    for item in value:
        _name(item, context)
    if len(set(value)) != len(value):
        raise ValueError(f"{context} contains duplicate field names.")
    return list(value)


def _integer(value, context, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{context} must be an integer >= {minimum}" + (f" and <= {maximum}." if maximum is not None else "."))
    return value


def _number(value, context, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{context} must be a finite number.")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{context} is outside the permitted range.")
    return value


def _sheet(value):
    if value is None:
        return None
    if type(value) is int:
        return _integer(value, "sheet")
    return _name(value, "sheet")


def _timezone(value):
    _name(value, "timezone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone {value!r}; use an IANA name and ensure timezone data is installed.") from exc
    return value


def _frequency(value):
    if value not in ("D", "W", "M", "Q", "Y"):
        raise ValueError("frequency must be D, W, M, Q or Y.")
    return value


def _read_json(path):
    if path is None:
        raise ValueError("An explicit JSON configuration path is required.")
    path = Path(path).expanduser().resolve()
    from .safety import register_input
    register_input(path)
    if not path.is_file():
        raise ValueError(f"Configuration file not found: {path}")
    raw = path.read_bytes()
    if path.read_bytes() != raw:
        raise ValueError(f"Configuration changed while being read: {path}")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Non-finite JSON constant is not permitted: {value}")

    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs,
                           parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Configuration must be valid UTF-8 JSON: {path}") from exc
    return value, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def _project(value):
    _object(value, {"schema_version", "name", "data", "analysis", "metrics", "quality"}, "project")
    if value.get("schema_version") != "1.0":
        raise ValueError("project.schema_version must be '1.0'.")
    _name(value.get("name"), "project.name")
    result = deepcopy(value)
    data = result.setdefault("data", {})
    _object(data, {"grain", "entity_key", "date", "sheet"}, "project.data")
    if "grain" in data:
        if isinstance(data["grain"], list):
            data["grain"] = _columns(data["grain"], "grain")
        else:
            _name(data["grain"], "grain")
    if "entity_key" in data:
        data["entity_key"] = _columns(data["entity_key"], "entity_key", nullable=True)
    if "date" in data:
        _name(data["date"], "date", optional=True)
    if "sheet" in data:
        data["sheet"] = _sheet(data["sheet"])
    analysis = result.setdefault("analysis", {})
    _object(analysis, {"timezone", "frequency", "group"}, "project.analysis")
    if "timezone" in analysis:
        _timezone(analysis["timezone"])
    if "frequency" in analysis:
        _frequency(analysis["frequency"])
    if "group" in analysis:
        _name(analysis["group"], "group", optional=True)
    metrics = result.setdefault("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("project.metrics must be an object keyed by metric ID.")
    for identifier, spec in metrics.items():
        _name(identifier, "metric ID")
        if not isinstance(spec, dict):
            raise ValueError(f"Metric {identifier!r} must be an object.")
        if spec.get("id") not in (None, identifier):
            raise ValueError(f"Metric ID conflicts with registry key {identifier!r}.")
        metrics[identifier] = normalize_metric({**spec, "id": identifier})
    quality = result.setdefault("quality", {})
    _object(quality, DEFAULT_QUALITY, "project.quality")
    for key in ("required_columns", "unique_key"):
        if key in quality:
            quality[key] = _columns(quality[key], key)
    if "max_missing_fraction" in quality:
        thresholds = quality["max_missing_fraction"]
        if not isinstance(thresholds, dict):
            raise ValueError("max_missing_fraction must map field names to thresholds.")
        for column, threshold in thresholds.items():
            _name(column, "max_missing_fraction field")
            _number(threshold, f"max_missing_fraction.{column}", 0, 1)
    if "min_rows" in quality:
        _integer(quality["min_rows"], "min_rows")
    return result


def _preferences(value):
    _object(value, DEFAULT_PREFERENCES, "preferences")
    if value.get("schema_version") != "1.0":
        raise ValueError("preferences.schema_version must be '1.0'.")
    result = deepcopy(value)
    for section in ("report", "charts", "display", "dashboard"):
        fields = result.setdefault(section, {})
        _object(fields, DEFAULT_PREFERENCES[section], f"preferences.{section}")
    report = result["report"]
    if "title" in report:
        _name(report["title"], "report.title")
    if "page_size" in report and report["page_size"] not in ("A4", "Letter"):
        raise ValueError("report.page_size must be A4 or Letter.")
    if "cjk_font" in report:
        _name(report["cjk_font"], "report.cjk_font", optional=True)
    if "color" in result["charts"]:
        color = result["charts"]["color"]
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("charts.color must be a six-digit HEX color such as #2563EB.")
    display = result["display"]
    if "precision" in display:
        _integer(display["precision"], "display.precision", 0, 12)
    if "missing_label" in display and not isinstance(display["missing_label"], str):
        raise ValueError("display.missing_label must be text.")
    dashboard = result["dashboard"]
    if "interval" in dashboard:
        _number(dashboard["interval"], "dashboard.interval", 2)
    if "max_rows" in dashboard:
        _integer(dashboard["max_rows"], "dashboard.max_rows", 1)
    return result


def load_project(path):
    """Read and validate only the explicitly named project file."""
    return _project(_read_json(path)[0])


def load_preferences(path):
    """Read validated presentation settings, filling appearance defaults."""
    selected = _preferences(_read_json(path)[0])
    result = deepcopy(DEFAULT_PREFERENCES)
    for section in ("report", "charts", "display", "dashboard"):
        result[section].update(selected[section])
    return result


def resolve_settings(project_path=None, preferences_path=None, metric_id=None, overrides=None):
    """Resolve explicit non-None overrides > project > defaults, with provenance.

Presentation preferences cannot change metric inputs, filters or aggregation.
Selecting a registered metric and supplying business overrides is ambiguous and
is rejected; group/date/timezone and display overrides remain explicit options.
"""
    overrides = {} if overrides is None else overrides
    _object(overrides, OVERRIDE_KEYS, "overrides")
    explicit = {key: value for key, value in overrides.items() if value is not None}
    files, sources = {}, {}
    project = {"data": {}, "analysis": {}, "metrics": {}, "quality": {}}
    raw_project = {}
    if project_path is not None:
        raw_project, files["project"] = _read_json(project_path)
        project = _project(raw_project)
    preferences = deepcopy(DEFAULT_PREFERENCES)
    for section, fields in preferences.items():
        if isinstance(fields, dict):
            sources.update({f"preferences.{section}.{key}": "default" for key in fields})
    if preferences_path is not None:
        selected, files["preferences"] = _read_json(preferences_path)
        selected = _preferences(selected)
        for section in ("report", "charts", "display", "dashboard"):
            preferences[section].update(selected[section])
            sources.update({f"preferences.{section}.{key}": f"preferences.{section}.{key}" for key in selected[section]})
    analysis = deepcopy(DEFAULT_ANALYSIS)
    sources.update({f"analysis.{key}": "default" for key in analysis})
    analysis["title"] = preferences["report"]["title"]
    sources["analysis.title"] = sources["preferences.report.title"]
    for project_key, analysis_key in (("entity_key", "key"), ("date", "date"), ("sheet", "sheet")):
        if project_key in project["data"]:
            analysis[analysis_key] = deepcopy(project["data"][project_key])
            sources[f"analysis.{analysis_key}"] = f"project.data.{project_key}"
    for project_key, analysis_key in (("group", "group"), ("timezone", "timezone"), ("frequency", "freq")):
        if project_key in project["analysis"]:
            analysis[analysis_key] = project["analysis"][project_key]
            sources[f"analysis.{analysis_key}"] = f"project.analysis.{project_key}"
    quality = deepcopy(DEFAULT_QUALITY)
    quality.update(deepcopy(project["quality"]))
    sources.update({f"quality.{key}": f"project.quality.{key}" if key in project["quality"] else "default" for key in quality})
    definition = None
    if metric_id is not None:
        _name(metric_id, "metric_id")
        if metric_id not in project["metrics"]:
            raise ValueError(f"Registered metric not found: {metric_id!r}; provide its project JSON.")
        conflicts = sorted(BUSINESS_OVERRIDES & explicit.keys())
        if conflicts:
            raise ValueError(f"Registered metric {metric_id!r} cannot be combined with business overrides: {conflicts}")
        definition = deepcopy(project["metrics"][metric_id])
        raw_spec = raw_project["metrics"][metric_id]
        for key in definition:
            sources[f"metric_definition.{key}"] = (f"project.metrics.{metric_id}.{key}" if key in raw_spec else
                                                     f"project.metrics.{metric_id}" if key == "id" else "default")
        for metric_key, argument in (("column", "metric"), ("aggregation", "agg"), ("weight", "weight"), ("denominator", "denominator")):
            analysis[argument] = definition[metric_key]
            sources[f"analysis.{argument}"] = sources[f"metric_definition.{metric_key}"]
    for key in set(DEFAULT_ANALYSIS) & explicit.keys():
        analysis[key] = deepcopy(explicit[key])
        sources[f"analysis.{key}"] = f"overrides.{key}"
    for key in ("metric", "group", "date", "weight", "denominator"):
        _name(analysis[key], key, optional=True)
    analysis["sheet"] = _sheet(analysis["sheet"])
    analysis["key"] = _columns(analysis["key"], "key", nullable=True)
    _timezone(analysis["timezone"])
    _frequency(analysis["freq"])
    _name(analysis["title"], "title")
    if definition is None:
        has_metric = analysis["metric"] is not None or analysis["agg"] in ("count", "count_rows")
        if has_metric:
            definition = legacy_metric(analysis["metric"], analysis["agg"], analysis["weight"], analysis["denominator"])
            for key, argument in (("column", "metric"), ("aggregation", "agg"), ("weight", "weight"), ("denominator", "denominator")):
                sources[f"metric_definition.{key}"] = sources[f"analysis.{argument}"]
            for key in definition:
                sources.setdefault(f"metric_definition.{key}", "default")
        elif analysis["agg"] != "sum" or any(analysis[key] is not None for key in ("weight", "denominator")):
            raise ValueError("Aggregation requires a metric column; use count_rows for row counts.")
        elif any(key in explicit for key in ("filters", "unit", "currency", "precision")):
            raise ValueError("Metric filters/formatting require a metric definition.")
    if definition is not None:
        for key in ("filters", "unit", "currency", "precision"):
            if key in explicit:
                definition[key] = deepcopy(explicit[key])
                sources[f"metric_definition.{key}"] = f"overrides.{key}"
        definition = normalize_metric(definition)
        analysis["agg"] = definition["aggregation"]
    return {"metric_definition": definition, "analysis": analysis, "preferences": preferences,
            "quality": quality, "provenance": {"files": files, "field_sources": sources,
                "metric_id": metric_id, "project_name": project.get("name"),
                "data_grain": deepcopy(project["data"].get("grain"))}}
