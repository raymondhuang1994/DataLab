"""Two-group inference with effect estimates and explicit assumptions."""
from __future__ import annotations
import numpy as np
from scipy import stats
from .common import guard_output_path, json_safe, write_json
from .io import load_frame


def compare_groups(input_path, group, metric, method="welch", sheet=None, alpha=.05):
    if not 0 < alpha < 1:
        raise ValueError("alpha 必须介于 0 和 1。")
    frame = load_frame(input_path, sheet=sheet)
    if group not in frame or metric not in frame:
        raise ValueError("分组或指标字段不存在。")
    import pandas as pd
    values = pd.to_numeric(frame[metric], errors="raise").replace([np.inf, -np.inf], np.nan)
    valid = frame[group].notna() & values.notna()
    labels = sorted(frame.loc[valid, group].unique(), key=str)
    if len(labels) != 2:
        raise ValueError("该比较命令要求恰好两个有效组；多组/配对/重复测量需选择相应方法。")
    a, b = [values[valid & (frame[group] == label)].to_numpy(dtype=float) for label in labels]
    if min(len(a), len(b)) < 2:
        raise ValueError("每组至少需要两条有效观测。")
    difference = float(b.mean() - a.mean())
    se2 = a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b)
    if method == "welch":
        if se2 == 0:
            raise ValueError("两组均无样本变异，无法进行有效的 Welch 推断。")
        test = stats.ttest_ind(b, a, equal_var=False)
        freedom = se2**2 / ((a.var(ddof=1)/len(a))**2/(len(a)-1) + (b.var(ddof=1)/len(b))**2/(len(b)-1))
        delta = stats.t.ppf(1-alpha/2, freedom) * np.sqrt(se2)
        interval = [difference - float(delta), difference + float(delta)]
        effect_name = "mean_difference_B_minus_A"
        effect = difference
    elif method == "mannwhitney":
        test = stats.mannwhitneyu(b, a, alternative="two-sided", method="auto")
        interval = None
        effect_name = "rank_biserial_B_vs_A"
        effect = float(2*test.statistic/(len(a)*len(b))-1)
    else:
        raise ValueError("method 仅支持 welch 或 mannwhitney。")
    return json_safe({"method": method, "groups": [str(x) for x in labels], "n": [len(a), len(b)],
                      "means": [float(a.mean()), float(b.mean())], "medians": [float(np.median(a)), float(np.median(b))],
                      "excluded_rows": int((~valid).sum()), "effect_name": effect_name, "effect": effect,
                      "mean_difference_B_minus_A": difference, "confidence_level": 1-alpha,
                      "mean_difference_ci": interval, "statistic": test.statistic, "p_value": test.pvalue,
                      "alpha": alpha, "limitations": ["假设观测相互独立；重复用户/配对测量不能直接使用独立样本方法。", "此处未校正多重检验；需根据实际比较次数处理。", "显著性不代表因果或业务重要性。", "Mann–Whitney 检验比较秩/分布，不能在分布形状不同的情况下直接称为中位数检验。"]})


def add_stats_parser(subparsers):
    parser = subparsers.add_parser("compare", help="两个独立组的基础统计比较")
    parser.add_argument("--input", required=True); parser.add_argument("--out", required=True)
    parser.add_argument("--group", required=True); parser.add_argument("--metric", required=True)
    parser.add_argument("--method", choices=["welch", "mannwhitney"], default="welch")
    parser.add_argument("--sheet"); parser.add_argument("--alpha", type=float, default=.05)
    def run(args):
        output = guard_output_path(args.input, {}, args.out)
        result = compare_groups(args.input, args.group, args.metric, args.method, args.sheet, args.alpha)
        write_json(output, result)
        return result
    parser.set_defaults(func=run)
