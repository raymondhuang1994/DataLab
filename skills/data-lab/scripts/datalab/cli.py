"""DataLab CLI. Doctor intentionally works before dependencies are installed."""
from __future__ import annotations
import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path


def doctor():
    import importlib
    import shutil
    import os
    import tempfile
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "datalab-mpl"))
    required = ["pandas", "numpy", "matplotlib", "openpyxl", "docx", "PIL", "scipy", "sklearn"]
    optional = ["pyarrow", "plotly"]
    capabilities = {m: importlib.util.find_spec(m) is not None for m in required + optional}
    errors = {}
    for module in required + optional:
        if capabilities[module]:
            try:
                importlib.import_module(module)
            except Exception as error:
                capabilities[module] = False
                errors[module] = str(error)
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("Asia/Shanghai")
        timezones = True
    except (ImportError, ValueError, KeyError):
        timezones = False
    features = {"analysis": all(capabilities[k] for k in ("pandas", "numpy", "matplotlib")),
                "modeling": all(capabilities[k] for k in ("pandas", "numpy", "sklearn", "scipy")),
                "word_export": all(capabilities[k] for k in ("docx", "PIL")),
                "dashboard": capabilities["pandas"], "timezone_database": timezones,
                "word_renderer_on_path": bool(shutil.which("soffice"))}
    return {"python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
            "supported_python": sys.version_info >= (3, 11), "packages": capabilities,
            "ready": sys.version_info >= (3, 11) and all(capabilities[m] for m in required) and timezones,
            "capabilities": features, "import_errors": errors,
            "notes": ["此检查不代表 ChatGPT/Codex 已安装或启用此插件。", "本地刷新看板需要持续运行的 Python 进程；网络数据库需宿主中已授权的连接工具。", "Word 生成不需要安装 Microsoft Word；版式预览需要 Word、LibreOffice 或宿主预览工具。"]}


def parser(command=None):
    root = argparse.ArgumentParser(prog="datalab", description="DataLab 数据科学工作台")
    from . import __version__
    root.add_argument("--version", action="version", version="DataLab " + __version__)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查 Python、平台与依赖").set_defaults(func=lambda args: doctor())
    analyze_parser = sub.add_parser("analyze", help="数据画像、明确口径的分组/趋势分析和报告")
    analyze_parser.add_argument("--input", required=True); analyze_parser.add_argument("--out", required=True)
    analyze_parser.add_argument("--project", help="显式项目 JSON 配置")
    analyze_parser.add_argument("--preferences", help="显式用户偏好 JSON 配置")
    analyze_parser.add_argument("--metric-id", help="项目内注册指标名称")
    for name in ("metric", "group", "date", "weight", "denominator", "sheet", "key"):
        analyze_parser.add_argument("--" + name)
    analyze_parser.add_argument("--agg", choices=["sum", "mean", "median", "count", "count_rows", "count_nonmissing", "count_distinct", "weighted_mean", "ratio"])
    analyze_parser.add_argument("--freq", choices=["D", "W", "M", "Q", "Y"])
    analyze_parser.add_argument("--timezone")
    analyze_parser.add_argument("--title")
    analyze_parser.add_argument("--unit", choices=["number", "percent", "currency"])
    analyze_parser.add_argument("--currency")
    analyze_parser.add_argument("--precision", type=int)
    analyze_parser.add_argument("--no-charts", action="store_true")
    def run_analysis(args):
        from .analysis import analyze
        from .settings import resolve_settings
        from .validation import validate_bundle
        from .common import write_json
        from .report import render_html
        fields = ("metric", "group", "date", "agg", "weight", "denominator", "sheet", "key", "title", "timezone", "freq", "unit", "currency", "precision")
        resolved = resolve_settings(args.project, args.preferences, args.metric_id, {key: getattr(args, key) for key in fields if getattr(args, key) is not None})
        result = analyze(args.input, args.out, **resolved["analysis"], charts=not args.no_charts,
                         metric_definition=resolved["metric_definition"], preferences=resolved["preferences"],
                         quality_rules=resolved["quality"], settings_provenance=resolved["provenance"])
        bundle = Path(args.out) / "result.json"
        verification = validate_bundle(bundle)
        result["validation"] = verification
        write_json(bundle, result)
        write_json(Path(args.out) / "checks.json", verification)
        render_html(bundle, Path(args.out) / "report.html")
        return {"output_dir": str(Path(args.out).resolve()), "status": verification["status"], "checks_status": verification["checks_status"], "summary": result["summary"], "limitations": result["limitations"]}
    analyze_parser.set_defaults(func=run_analysis)
    report = sub.add_parser("report", help="从 result.json 重新生成便携 HTML 报告")
    report.add_argument("--bundle", required=True); report.add_argument("--out", required=True)
    def run_report(args):
        from .report import render_html
        return render_html(args.bundle, args.out)
    report.set_defaults(func=run_report)
    validate = sub.add_parser("validate", help="检查成果一致性并复核源数据")
    validate.add_argument("--bundle", required=True); validate.add_argument("--out")
    validate.add_argument("--no-source", action="store_true")
    def run_validate(args):
        from .validation import validate_bundle
        from .common import write_json
        result = validate_bundle(args.bundle, verify_source=not args.no_source)
        if args.out:
            from .common import guard_output_path
            bundle_path = Path(args.bundle).resolve()
            guard_output_path(bundle_path, json.loads(bundle_path.read_text(encoding="utf-8")), args.out)
            write_json(args.out, result)
        return result
    validate.set_defaults(func=run_validate)
    replay = sub.add_parser("rerun", help="按已保存的 config.json 重跑到新目录")
    replay.add_argument("--config", required=True); replay.add_argument("--out", required=True)
    replay.add_argument("--input", help="原文件移动后的新路径")
    def run_replay(args):
        from .analysis import analyze
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if args.input:
            config["input_path"] = args.input
        allowed = {"input_path", "metric", "group", "date", "agg", "weight", "denominator", "sheet", "key", "title", "timezone", "freq", "charts", "metric_definition", "preferences", "quality_rules", "settings_provenance"}
        if set(config) - allowed:
            raise ValueError("config.json 含不支持的字段。")
        result = analyze(output_dir=args.out, **config)
        return {"output_dir": args.out, "summary": result["summary"]}
    replay.set_defaults(func=run_replay)
    config = sub.add_parser("config", help="校验配置、展示生效设置与来源（不会修改文件）")
    config.add_argument("--project"); config.add_argument("--preferences"); config.add_argument("--metric-id")
    def run_config(args):
        from .settings import resolve_settings
        return resolve_settings(args.project, args.preferences, args.metric_id)
    config.set_defaults(func=run_config)
    # Import only the chosen executor: Word and help do not need scikit-learn.
    import importlib
    executors = [
        (("word",), "word_report", "add_word_parser", "从成果包导出 Word 报告"),
        (("dashboard", "serve"), "dashboard", "add_dashboard_parser", "离线或持续刷新的本地看板"),
        (("model",), "modeling", "add_model_parser", "训练、评估和比较预测模型"),
        (("compare",), "statistics", "add_stats_parser", "两个独立组的统计比较"),
    ]
    for names, module, register, help_text in executors:
        if command in names:
            getattr(importlib.import_module("." + module, __package__), register)(sub)
        else:
            for name in names:
                sub.add_parser(name, help=help_text)
    return root


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["doctor"]:
        result = doctor()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    try:
        args = parser(argv[0] if argv else None).parse_args(argv)
        result = args.func(args)
        if result is not None:
            from .common import json_safe
            print(json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if isinstance(result, dict) and result.get("status") == "failed" else 0
    except KeyboardInterrupt:
        print("DataLab 已停止。", file=sys.stderr)
        return 130
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(f"DataLab: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
