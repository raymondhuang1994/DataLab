"""Static charts with common fonts on Windows/macOS and reproducible source tables."""
from __future__ import annotations
import os
from pathlib import Path
import tempfile


def setup_plotting():
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "datalab-mpl"))
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager, pyplot as plt
    preferred = ["Microsoft YaHei", "PingFang SC", "Heiti TC", "Noto Sans CJK SC", "Source Han Sans SC", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    fonts = [f for f in preferred if f in available]
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": fonts or ["DejaVu Sans"],
                         "axes.unicode_minus": False, "figure.dpi": 150, "savefig.dpi": 180,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "font.size": 10, "axes.titlesize": 13,
                         "svg.fonttype": "none"})
    return plt, fonts


def chart_table(table, directory, filename, kind="bar", ylabel="数值", metric_spec=None, preferences=None):
    import numpy as np
    plt, fonts = setup_plotting()
    preferences = preferences or {}
    color = preferences.get("charts", {}).get("color", "#286B85")
    rows = table["rows"]
    if not rows:
        return None
    if kind == "bar":
        rows = sorted(rows, key=lambda r: r[1] if r[1] is not None else -float("inf"), reverse=True)[:20]
    x = [str(row[0]) for row in rows]
    y = [float(row[1]) if row[1] is not None else np.nan for row in rows]
    fig, ax = plt.subplots(figsize=(9, min(10, max(4.5, len(rows) * .28))) if kind == "bar" else (9, 4.7))
    if kind == "bar":
        ax.barh(x[::-1], y[::-1], color=color)
        ax.set_xlabel(ylabel)
        ax.axvline(0, color="#64748B", linewidth=.8)
    else:
        ax.plot(range(len(x)), y, color=color, marker="o", linewidth=2, markersize=3)
        positions = list(range(0, len(x), max(1, len(x) // 8)))
        ax.set_xticks(positions, [x[i] for i in positions], rotation=25, ha="right")
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=.16)
    if metric_spec:
        from matplotlib.ticker import FuncFormatter, MaxNLocator
        from .metrics import format_value
        formatter = FuncFormatter(lambda value, position: format_value(value, metric_spec, preferences))
        (ax.xaxis if kind == "bar" else ax.yaxis).set_major_formatter(formatter)
        if kind != "bar":
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=3))
            finite = [value for value in y if np.isfinite(value)]
            precision = metric_spec.get("precision")
            if precision is None:
                precision = preferences.get("display", {}).get("precision", 2)
            quantum = 10.0 ** (-precision) / (100 if metric_spec.get("unit") == "percent" else 1)
            if finite:
                low, high = ax.get_ylim()
                center = (min(finite) + max(finite)) / 2
                minimum_span = max(4 * quantum, float(np.spacing(abs(center))) * 8)
                if high - low < minimum_span:
                    low, high = center - minimum_span / 2, center + minimum_span / 2
                    if min(finite) >= 0 and low < 0:
                        low, high = 0, minimum_span
                    ax.set_ylim(low, high)
    ax.set_title(table["title"], loc="left", pad=14)
    fig.tight_layout()
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg"):
        fig.savefig(directory / f"{filename}.{extension}", bbox_inches="tight")
    plt.close(fig)
    return {"title": table["title"], "path": f"charts/{filename}.png", "svg_path": f"charts/{filename}.svg",
            "caption": f"来源：{table['source_file']}。" + ("图表仅显示排名前 20 类；完整数据见结果表。" if kind == "bar" and len(table["rows"]) > 20 else ""),
            "table_id": table["id"], "fonts": fonts, "value_axis_limits": list(ax.get_xlim() if kind == "bar" else ax.get_ylim()),
            "value_axis_tick_labels": [tick.get_text() for tick in (ax.get_xticklabels() if kind == "bar" else ax.get_yticklabels())]}
