"""Auditable tabular prediction with untouched holdout evaluation.

No serialized model is loaded. Reproduction uses source, parameters and data.
Forecasting is a single, regular series: rolling one-step evaluation, followed
by explicitly labelled recursive future predictions; it is not causal inference.
"""
from __future__ import annotations

import csv
from io import BytesIO
import json
import math
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .common import file_sha256, json_safe, utc_now, write_json
from .io import load_frame
from .safety import cache_directory, current_session, output_session, register_input, safe_open, safe_write_bytes, safe_write_text


def _resolve_task(y, task):
    if task not in {"auto", "regression", "classification", "forecast"}:
        raise ValueError("task 必须为 auto、regression、classification 或 forecast。")
    if task != "auto":
        return task
    if (pd.api.types.is_bool_dtype(y.dtype)
            or not pd.api.types.is_numeric_dtype(y.dtype)
            or y.dropna().nunique() == 2):
        return "classification"
    nonmissing = y.dropna()
    if (2 < nonmissing.nunique() <= 20
            and np.isfinite(nonmissing).all()
            and np.equal(nonmissing, np.floor(nonmissing)).all()):
        raise ValueError("少量不同整数值的目标存在分类/回归歧义；请显式指定 task='classification' 或 'regression'。")
    return "regression"


def _parse_times(series):
    if pd.api.types.is_numeric_dtype(series.dtype):
        raise ValueError("数值时间字段的单位/格式不明确；请先转换为明确日期或带时区时间。")
    try:
        stamps = [pd.Timestamp(value) for value in series]
    except (ValueError, TypeError) as exc:
        raise ValueError("时间字段含无法解析的值；请先明确日期格式。") from exc
    if any(pd.isna(value) for value in stamps):
        raise ValueError("时间字段含缺失值；请修复后再按时间评估。")
    awareness = {value.tzinfo is not None for value in stamps}
    if len(awareness) > 1:
        raise ValueError("时间字段混合带时区和无时区时间；请先统一时间语义，避免错误排序。")
    return pd.Series(pd.to_datetime(stamps, utc=True)), awareness == {False}


def _identifier_name(name):
    return bool(re.search(r"(^id$|_id$|^id_|uuid|guid|编号|身份证|邮箱|电话|手机|邮编)", str(name), re.I))


def _time_split(times, test_size):
    """Split unique timestamps, never rows within the same timestamp."""
    unique = pd.Index(times.drop_duplicates())
    if len(unique) < 4:
        raise ValueError("按时间评估至少需要 4 个不同时间点。")
    n_test = max(1, math.ceil(len(unique) * test_size))
    if len(unique) - n_test < 3:
        raise ValueError("训练时间点太少；减小 test_size 或增加历史数据。")
    cutoff = unique[-n_test]
    return np.flatnonzero(times < cutoff), np.flatnonzero(times >= cutoff)


def _cv_splits(X, y, task, times, cv_folds, seed):
    from sklearn.model_selection import KFold, StratifiedKFold, TimeSeriesSplit

    skipped = []
    if times is not None:
        unique = pd.Index(times.drop_duplicates())
        n_splits = min(cv_folds, len(unique) - 1)
        if n_splits < 2:
            raise ValueError("训练数据不足以进行时间交叉验证。")
        folds = []
        for train_groups, valid_groups in TimeSeriesSplit(n_splits=n_splits).split(unique):
            train = np.flatnonzero(times.isin(unique[train_groups]))
            valid = np.flatnonzero(times.isin(unique[valid_groups]))
            if task == "classification" and y.iloc[train].nunique() < 2:
                skipped.append("早期训练窗口只有一个类别，该窗口不能训练分类器。")
                continue
            if len(train) < 3:
                skipped.append("早期训练窗口少于 3 行，跳过该窗口。")
                continue
            folds.append((train, valid))
    elif task == "classification":
        n_splits = min(cv_folds, int(y.value_counts().min()))
        if n_splits < 2:
            raise ValueError("训练集中每个类别至少需要 2 行，才能进行分层交叉验证。")
        folds = list(StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed).split(X, y))
    else:
        n_splits = min(cv_folds, len(X) // 3)
        if n_splits < 2:
            raise ValueError("训练数据至少需要 6 行，才能进行交叉验证。")
        folds = list(KFold(n_splits=n_splits, shuffle=True, random_state=seed).split(X))
    if len(folds) < 2:
        raise ValueError("至少需要 2 个可行交叉验证窗口；请增加数据或调整留出比例。")
    return folds, skipped


def _normalise_features(X):
    """Deterministic type conversion only: no fitted statistics or selection."""
    X = X.copy()
    numeric, categorical, infinity_counts = [], [], {}
    for column in X:
        s = X[column]
        if pd.api.types.is_datetime64_any_dtype(s.dtype):
            raise ValueError(f"特征 {column!r} 是日期类型；请先创建具有业务含义的日期特征，或将其指定为 time_column。")
        if pd.api.types.is_numeric_dtype(s.dtype) and not pd.api.types.is_bool_dtype(s.dtype):
            numeric.append(column)
            s = pd.to_numeric(s, errors="raise").astype(float)
            infinity_counts[column] = int(np.isinf(s).sum())
            X[column] = s.replace([np.inf, -np.inf], np.nan)
        else:
            categorical.append(column)
            X[column] = s.astype(object).map(lambda v: np.nan if pd.isna(v) else str(v))
    return X, numeric, categorical, infinity_counts


def _pipeline(estimator, numeric, categorical):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    transformers = []
    if numeric:
        transformers.append(("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
        ]), numeric))
    if categorical:
        transformers.append(("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="constant", fill_value="__DATALAB_MISSING__", keep_empty_features=True)),
            ("encode", OneHotEncoder(handle_unknown="ignore", max_categories=50)),
        ]), categorical))
    return Pipeline([
        ("preprocess", ColumnTransformer(transformers, remainder="drop")),
        ("model", estimator),
    ])


def _candidates(task, numeric, categorical, model_families, seed, forecast=False):
    from sklearn.base import BaseEstimator, RegressorMixin
    from sklearn.dummy import DummyClassifier, DummyRegressor
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LogisticRegression, Ridge

    class PersistenceRegressor(RegressorMixin, BaseEstimator):
        """Last observed value; only used on explicitly prepared lag features."""
        def fit(self, X, y):
            self.n_features_in_ = X.shape[1]
            return self

        def predict(self, X):
            return np.asarray(X["lag_1"], dtype=float)

    if forecast:
        result = [("baseline_persistence", PersistenceRegressor())]
    else:
        baseline = DummyClassifier(strategy="prior") if task == "classification" else DummyRegressor(strategy="mean")
        result = [("baseline_prior" if task == "classification" else "baseline_mean", _pipeline(baseline, numeric, categorical))]
    if "linear" in model_families:
        for strength in (0.1, 1.0, 10.0):
            estimator = (LogisticRegression(C=strength, max_iter=3000, random_state=seed)
                         if task == "classification" else Ridge(alpha=strength, solver="lsqr"))
            name = f"logistic_C_{strength:g}" if task == "classification" else f"ridge_alpha_{strength:g}"
            result.append((name, _pipeline(estimator, numeric, categorical)))
    if "random_forest" in model_families:
        model_cls = RandomForestClassifier if task == "classification" else RandomForestRegressor
        for depth in (6, None):
            estimator = model_cls(n_estimators=120, max_depth=depth, min_samples_leaf=2, random_state=seed, n_jobs=1)
            result.append((f"random_forest_depth_{depth}", _pipeline(estimator, numeric, categorical)))
    return result


def _metrics(y, pred, task, labels=None, probability=None, model_classes=None):
    from sklearn.metrics import (accuracy_score, balanced_accuracy_score, confusion_matrix,
                                 f1_score, log_loss, mean_absolute_error, mean_squared_error,
                                 r2_score, roc_auc_score)
    if task != "classification":
        return {"mae": float(mean_absolute_error(y, pred)),
                "rmse": float(np.sqrt(mean_squared_error(y, pred))),
                "r2": float(r2_score(y, pred)) if len(y) > 1 and np.var(y) > 0 else None}
    result = {"accuracy": float(accuracy_score(y, pred)),
              "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
              "macro_f1": float(f1_score(y, pred, labels=labels, average="macro", zero_division=0)),
              "class_labels": list(labels),
              "confusion_matrix": confusion_matrix(y, pred, labels=labels).tolist()}
    if probability is not None and set(y).issubset(set(model_classes)):
        result["log_loss"] = float(log_loss(y, probability, labels=list(model_classes)))
        if len(model_classes) == 2 and len(set(y)) == 2:
            result["roc_auc"] = float(roc_auc_score(np.asarray(y) == model_classes[1], probability[:, 1]))
    return result


def _preprocessing_audit(model, numeric, categorical):
    if not hasattr(model, "named_steps"):
        return {"method": "persistence; no fitted preprocessing"}
    pre = model.named_steps["preprocess"]
    result = {"fit_scope": "training_partition_only", "numeric_columns": numeric, "categorical_columns": categorical}
    if numeric:
        num = pre.named_transformers_["numeric"]
        result["numeric_imputer_statistics"] = dict(zip(numeric, num.named_steps["impute"].statistics_.tolist()))
        result["numeric_scaler_mean"] = dict(zip(numeric, num.named_steps["scale"].mean_.tolist()))
    if categorical:
        encoder = pre.named_transformers_["categorical"].named_steps["encode"]
        result["categorical_vocabulary"] = {name: values.tolist() for name, values in zip(categorical, encoder.categories_)}
    names = pre.get_feature_names_out().tolist()
    estimator = model.named_steps["model"]
    if hasattr(estimator, "coef_"):
        result["transformed_feature_names"] = names
        result["coefficients"] = np.asarray(estimator.coef_).tolist()
        result["intercept"] = np.asarray(estimator.intercept_).tolist()
        result["coefficient_note"] = "标准化和编码后的预测系数，不是因果效应。"
    if hasattr(estimator, "feature_importances_"):
        result["impurity_feature_importances"] = dict(zip(names, estimator.feature_importances_.tolist()))
        result["importance_note"] = "训练树的不纯度重要性；可能偏向高基数字段，不代表因果关系。"
    return result


def _plot_diagnostics(output, y, pred, metrics, task, times):
    import os
    plotting_cache = cache_directory("matplotlib")
    os.environ["MPLCONFIGDIR"] = str(plotting_cache)
    current_session().record_external_writer("Matplotlib font cache", plotting_cache)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if task == "classification":
        matrix = np.asarray(metrics["confusion_matrix"])
        fig, ax = plt.subplots(figsize=(7, 6), layout="constrained")
        im = ax.imshow(matrix, cmap="Blues")
        fig.colorbar(im, ax=ax, label="Count")
        ax.set(xlabel="Predicted class index", ylabel="Actual class index", title="Untouched holdout: confusion matrix")
        ax.set_xticks(range(len(matrix)))
        ax.set_yticks(range(len(matrix)))
        if len(matrix) <= 15:
            for row in range(len(matrix)):
                for column in range(len(matrix)):
                    ax.text(column, row, str(matrix[row, column]), ha="center", va="center",
                            color="white" if matrix[row, column] > matrix.max() / 2 else "black")
    else:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), layout="constrained")
        y, pred = np.asarray(y, dtype=float), np.asarray(pred, dtype=float)
        axes[0].scatter(y, pred, s=22, alpha=0.7)
        lo, hi = min(y.min(), pred.min()), max(y.max(), pred.max())
        axes[0].plot([lo, hi], [lo, hi], color="#888888", linestyle="--")
        axes[0].set(xlabel="Actual", ylabel="Predicted", title="Untouched holdout: predictions")
        if times is not None:
            axes[1].plot(times, y, label="Actual")
            axes[1].plot(times, pred, label="Prediction")
            axes[1].set(title="Chronological holdout", xlabel="Time", ylabel="Target")
            axes[1].tick_params(axis="x", rotation=30)
            axes[1].legend()
        else:
            axes[1].scatter(pred, y - pred, s=22, alpha=0.7)
            axes[1].axhline(0, color="#888888", linestyle="--")
            axes[1].set(xlabel="Predicted", ylabel="Actual - predicted", title="Holdout residuals")
    path = output / "model_diagnostics.png"
    try:
        image = BytesIO()
        fig.savefig(image, format="png", dpi=170)
        safe_write_bytes(path, image.getvalue())
    finally:
        plt.close(fig)
    return path.name


def _write_reproduction(output, config):
    """Copy source, not executables/models or data; rerun with an explicit input."""
    destination = output / "code" / "datalab"
    for path in Path(__file__).parent.glob("*.py"):
        register_input(path)
        safe_write_bytes(destination / path.name, path.read_bytes())
    script = '''"""Reproduce with the same dependency versions in model_metrics.json."""
import argparse
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "code"))
from datalab.modeling import train_model
from datalab.common import configure_utf8_stdio
from datalab.safety import output_session

configure_utf8_stdio()

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True, help="Path to the original data file")
parser.add_argument("--out", required=True, help="New empty output directory")
args = parser.parse_args()
with output_session(args.out, inputs=[root / "model_config.json"], raw_inputs=[args.input], directory=True):
    config = json.loads((root / "model_config.json").read_text(encoding="utf-8"))
    result = train_model(args.input, args.out, **config["parameters"])
print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
'''
    safe_write_text(output / "reproduce.py", script, encoding="utf-8")
    write_json(output / "model_config.json", config)


def _write_generic_bundle(output, result, source, source_hash, input_rows, input_columns, sheet):
    """Expose model evidence through the shared HTML/Word result contract."""
    from .report import render_html

    tables = []

    def table(identifier, title, columns, rows):
        rows = json_safe(rows)
        relative = f"tables/{identifier}.csv"
        path = output / relative
        with safe_open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
        tables.append({"id": identifier, "title": title, "columns": columns,
                       "rows": rows, "source_file": relative})

    metric_labels = {"mae": "MAE（平均绝对误差）", "rmse": "RMSE（均方根误差）", "r2": "R²（决定系数）",
                     "accuracy": "准确率", "balanced_accuracy": "平衡准确率", "macro_f1": "Macro F1",
                     "log_loss": "Log loss（对数损失）", "roc_auc": "ROC AUC"}
    metrics, baseline = result["holdout_metrics"], result["baseline_holdout_metrics"]
    metric_keys = [key for key in dict.fromkeys([*metrics, *baseline]) if key in metric_labels]
    table("model_holdout_metrics", "独立留出集：模型与简单基线", ["指标", "选中候选", "简单基线"],
          [[metric_labels[key], metrics.get(key), baseline.get(key)] for key in metric_keys])
    table("model_cv_candidates", "仅用训练数据进行的候选比较", ["候选", "选择指标", "CV均值", "CV标准差", "状态", "失败原因"],
          [[row["candidate"], result["selection_metric"], row.get("mean"), row.get("std"), row["status"], row.get("error", "")]
           for row in result["candidates"]])
    if result["task"] == "classification":
        labels = metrics["class_labels"]
        table("model_confusion_matrix", "留出集混淆矩阵", ["真实类别"] + [f"预测：{label}" for label in labels],
              [[label, *counts] for label, counts in zip(labels, metrics["confusion_matrix"])])

    task_labels = {"regression": "回归", "classification": "分类", "forecast": "单变量时间序列预测"}
    kind = task_labels[result["task"]]
    split = result["split"]
    main_metric = result["selection_metric"]
    score, baseline_score = metrics.get(main_metric), baseline.get(main_metric)
    score_text = lambda value: "未定义" if value is None else f"{value:.6g}"
    summary = [f"本次对“{result['target']}”进行{kind}；训练内部交叉验证选中 {result['selected_model']}。",
               f"使用 {split['train_rows']} 行训练、{split['holdout_rows']} 行独立留出及 {split['cv_folds']} 个训练验证折。",
               f"留出集 {main_metric}：选中候选 {score_text(score)}，简单基线 {score_text(baseline_score)}。"]
    methodology = [f"输入共 {input_rows} 行；缺失目标排除 {result['data_checks']['missing_target_rows_excluded']} 行；"
                   f"滞后预热排除 {result['data_checks']['lag_warmup_rows_excluded']} 行。",
                   "特征：" + "、".join(result["features"]) + "。",
                   f"分区方式：{split['method']}。候选及参数仅按训练内部 CV 的 {main_metric} 选择；"
                   "留出集未用于预处理拟合、参数选择或候选排序。",
                   "数值填补和标准化、类别编码都在各折的训练 Pipeline 中拟合。"
                   "时间任务将相同时间戳的记录放在同一分区，并使用向前扩展训练窗口。",
                   "简单基线为回归训练均值、分类训练类别先验，或预测任务的上一期真实值；"
                   "基线也参与训练 CV 比较，允许选出简单基线。",
                   "候选表中的标准差是不同验证折分数的波动，不是置信区间。各折相关或时间跨度不一致时应审慎解读。",
                   "逐行结果见 holdout_predictions.csv，分区见 split_assignments.csv；行位置是读取后的数据表从 0 开始的索引。"
                   "该报告不重复嵌入整张预测表。",
                   "完整指标和预处理审计见 model_metrics.json；输入指纹及参数见 model_config.json；"
                   "code/ 与 reproduce.py 用于在匹配依赖版本下复现。"]
    if result["task"] == "forecast":
        methodology.extend(["历史性能来自滚动单步评估：每个预测时点使用此前已经观察到的真实目标。"
                            "这不是从同一固定起点预测整个留出区间的多步回测。",
                            "完成最终留出评估后，才在全部历史上重拟合选中候选，递归生成 future_forecast.csv。"
                            f"未来跨度为 {result['forecast']['horizon']} 期；未提供预测区间或对应的固定起点多步历史准确率。"])
    chart = {"id": "model_diagnostics", "title": "独立留出集模型诊断", "path": "model_diagnostics.png",
             "caption": "图中只包含最终留出集。回归图的逐行数据见 holdout_predictions.csv；分类图按下方混淆矩阵的类别顺序编号。",
             "source_file": "holdout_predictions.csv"}
    if result["task"] == "classification":
        chart["table_id"] = "model_confusion_matrix"
    bundle = {"schema_version": "1.0", "title": f"DataLab {kind}报告：{result['target']}",
              "created_at": result["created_at"],
              "source": {"path": str(source), "sha256": source_hash, "rows": input_rows,
                         "columns": input_columns, "sheet": sheet},
              "summary": summary, "findings": [], "tables": tables, "charts": [chart],
              "methodology": methodology, "limitations": result["warnings"],
              "validation": {"status": "needs_review", "checks": [
                  {"name": "source_stable", "status": "passed", "detail": "读取前后源文件 SHA-256 一致。"},
                  {"name": "holdout_design", "status": "passed", "detail": "先分区，再在训练分区内部选择模型；最后评估留出集。"}],
                  "note": "自动检查不等于方法已独立审阅；仍需确认特征可用时点、样本独立性和应用边界。"},
              "model": {"task": result["task"], "selected_model": result["selected_model"],
                        "metrics_file": "model_metrics.json", "predictions_file": "holdout_predictions.csv"}}
    write_json(output / "result.json", bundle)
    render_html(output / "result.json", output / "report.html")


def train_model(input_path, output_dir, target, features=None, task="auto", time_column=None,
                test_size=0.2, seed=42, model_families=None, cv_folds=5, sheet=None,
                forecast_lags=None, forecast_horizon=6):
    """Train into a new task directory while preserving all original inputs."""
    with output_session(output_dir, raw_inputs=[input_path], directory=True):
        return _train_model_impl(input_path, output_dir, target, features=features, task=task,
                                 time_column=time_column, test_size=test_size, seed=seed,
                                 cv_folds=cv_folds, model_families=model_families, sheet=sheet,
                                 forecast_lags=forecast_lags, forecast_horizon=forecast_horizon)


def _train_model_impl(input_path, output_dir, target, features=None, task="auto", time_column=None,
                test_size=0.2, seed=42, model_families=None, cv_folds=5, sheet=None,
                forecast_lags=None, forecast_horizon=6):
    """Compare baseline, linear/logistic and forests using TRAIN-only CV.

    Selection uses RMSE (regression) or macro F1 (classification); the holdout
    is evaluated only after selection. Set task='forecast' for a regular,
    univariate series with lag features and recursive future predictions.
    Missing targets are reported and excluded in supervised tasks. Forecast
    tasks reject missing targets and irregular timestamps instead of changing
    the time grid silently. No arbitrary pickle/joblib input is accepted.
    """
    try:
        import sklearn
        from sklearn.base import clone
        from sklearn.model_selection import train_test_split
    except ImportError as exc:
        raise ValueError("建模需要 scikit-learn；请在独立环境安装 skills/data-lab/scripts/requirements.txt 或运行项目的 pip install .。") from exc

    if not 0 < test_size < 1:
        raise ValueError("test_size 必须大于 0 且小于 1。")
    if not isinstance(cv_folds, int) or cv_folds < 2:
        raise ValueError("cv_folds 至少为 2。")
    if not isinstance(forecast_horizon, int) or forecast_horizon < 1:
        raise ValueError("forecast_horizon 必须是正整数。")
    model_families = ["linear", "random_forest"] if model_families is None else list(model_families)
    if not model_families or set(model_families) - {"linear", "random_forest"}:
        raise ValueError("model_families 只能选择 linear、random_forest，且至少选择一个。")
    output = Path(output_dir).expanduser().resolve()
    source = Path(input_path).expanduser().resolve()
    if source.is_relative_to(output):
        raise ValueError("建模成果目录不能包含输入文件；请把输入与输出放在不同目录。")
    source_hash = file_sha256(source)
    data = load_frame(source, sheet=sheet)
    if file_sha256(source) != source_hash:
        raise ValueError("读取期间源文件发生变化；请固定数据快照后重新运行。")
    input_rows, input_columns = len(data), list(data.columns)
    if target not in data:
        raise ValueError(f"目标字段不存在: {target}")
    if time_column == target:
        raise ValueError("目标字段与时间字段不能相同。")
    if time_column is not None and time_column not in data:
        raise ValueError(f"时间字段不存在: {time_column}")
    if not data.columns.is_unique:
        raise ValueError("字段名必须唯一。")
    notes = ["预测关联不构成因果证据；需确认每个特征在真实预测时已经可用。",
             "没有自动识别同一实体的重复观测；同一主体跨分区时需另做分组评估。"]
    requested_task = task
    resolved = _resolve_task(data[target], task)
    forecasting = resolved == "forecast"
    effective_task = "regression" if forecasting else resolved
    if task == "auto":
        notes.append(f"auto 将目标解释为 {resolved}；数值编码的多类别目标请显式指定 classification。")
    raw_y = data[target]
    missing = raw_y.isna() | raw_y.astype(str).str.fullmatch(r"\s*")
    if effective_task == "regression":
        numeric_y = pd.to_numeric(raw_y, errors="coerce")
        invalid = ~missing & (numeric_y.isna() | ~np.isfinite(numeric_y))
        if invalid.any():
            raise ValueError(f"回归目标有 {int(invalid.sum())} 个非数值/非有限值；请明确清理或重新指定任务。")
    missing_count = int(missing.sum())
    if forecasting and missing_count:
        raise ValueError("预测序列的目标包含缺失值；请先按明确方法修复时间序列，不能直接删除时间点。")
    positions = np.flatnonzero(~missing)
    data = data.loc[~missing].copy().reset_index(drop=True)
    if missing_count:
        notes.append(f"排除 {missing_count} 行缺失目标；特征缺失由训练内预处理填补。")
    y = pd.to_numeric(data[target]).astype(float) if effective_task == "regression" else data[target].astype(str)
    times = None
    if time_column is not None:
        times, naive_time = _parse_times(data[time_column])
        if naive_time:
            notes.append("原时间无时区，输出按 UTC 标记且保持原钟面时间；跨来源比较前应先确认并转换真实时区。")
        order = np.argsort(times.to_numpy(), kind="stable")
        data, y = data.iloc[order].reset_index(drop=True), y.iloc[order].reset_index(drop=True)
        positions, times = positions[order], times.iloc[order].reset_index(drop=True)
    if effective_task == "classification" and y.nunique() < 2:
        raise ValueError("分类目标至少需要两个类别。")

    warmup = 0
    frequency = None
    lags = None
    observed_history = None
    excluded_identifiers = []
    if forecasting:
        if time_column is None:
            raise ValueError("forecast 必须指定 time_column。")
        if features is not None:
            raise ValueError("当前 forecast 只支持单变量滞后预测；不接受外生特征或多序列面板。")
        if times.duplicated().any():
            raise ValueError("forecast 每个时间点必须只有一行；多个主体请分开建模或先定义聚合口径。")
        frequency = pd.infer_freq(pd.DatetimeIndex(times)) if len(times) >= 3 else None
        if frequency is None:
            raise ValueError("forecast 需要规则且无缺口的时间序列；不会自动填补缺期。")
        lags = [1, 2, 3] if forecast_lags is None else list(forecast_lags)
        if not lags or any(not isinstance(lag, int) or lag <= 0 for lag in lags) or len(set(lags)) != len(lags):
            raise ValueError("forecast_lags 必须为不重复的正整数列表。")
        lags = sorted(set([1] + lags))
        warmup = max(lags)
        observed_history = y.tolist()
        X = pd.DataFrame({f"lag_{lag}": y.shift(lag) for lag in lags}).iloc[warmup:].reset_index(drop=True)
        y, times, positions = y.iloc[warmup:].reset_index(drop=True), times.iloc[warmup:].reset_index(drop=True), positions[warmup:]
        notes.extend(["历史评估为滚动单步预测：每个预测点使用此前已观测的真实目标，不是固定起点多步回测。",
                      "未来预测是递归多步预测；误差可能累积，未提供预测区间，也未评估该多步区间的历史准确率。"])
    else:
        if features is None:
            excluded_identifiers = [c for c in data if c not in {target, time_column} and _identifier_name(c)]
            chosen = [c for c in data if c not in {target, time_column} and c not in excluded_identifiers]
            if excluded_identifiers:
                notes.append("自动排除疑似标识符：" + ", ".join(excluded_identifiers) + "；如确有预测含义，可通过 features 明确指定。")
        else:
            chosen = list(features)
            named_ids = [c for c in chosen if _identifier_name(c)]
            if named_ids:
                notes.append("显式启用了疑似标识符特征：" + ", ".join(named_ids) + "；需检查个体记忆及跨分区重复风险。")
        if target in chosen or (time_column is not None and time_column in chosen):
            raise ValueError("特征不能包含目标字段或拆分用的时间字段。")
        if not chosen or len(set(chosen)) != len(chosen) or set(chosen) - set(data):
            raise ValueError("features 必须为非空、无重复且存在于输入中的字段列表。")
        X = data[chosen].copy()
    if len(X) < 12:
        raise ValueError("有效建模记录至少需要 12 行；实际需求取决于类别、时间点和任务复杂度。")
    X, numeric, categorical, infinity_counts = _normalise_features(X)
    if any(infinity_counts.values()):
        notes.append("特征中的正/负无穷已作为缺失处理，数量记录在数据检查中。")
    if times is not None:
        train, test = _time_split(times, test_size)
        split_method = "chronological_timestamp_groups"
        if pd.infer_freq(pd.DatetimeIndex(times.drop_duplicates())) is None:
            notes.append("时间间隔不规则；CV 按时间点组而非等时长窗口切分，各折指标需结合窗口长度解读。")
    else:
        if effective_task == "classification":
            classes = y.nunique()
            n_test = math.ceil(test_size * len(y))
            if y.value_counts().min() < 2 or n_test < classes or len(y) - n_test < classes:
                raise ValueError("无法分层拆分：每类至少 2 行，训练和留出集各至少能容纳全部类别。")
        train, test = train_test_split(np.arange(len(X)), test_size=test_size, random_state=seed,
                                       stratify=y if effective_task == "classification" else None)
        split_method = "stratified_random" if effective_task == "classification" else "random"
        notes.append("随机拆分假设记录相互独立；时间相关数据应指定 time_column。")
    if len(train) < 6 or len(test) < 2:
        raise ValueError("拆分后训练至少 6 行、留出至少 2 行；请调整 test_size 或补充数据。")
    X_train, X_test = X.iloc[train].reset_index(drop=True), X.iloc[test].reset_index(drop=True)
    y_train, y_test = y.iloc[train].reset_index(drop=True), y.iloc[test].reset_index(drop=True)
    t_train = times.iloc[train].reset_index(drop=True) if times is not None else None
    t_test = times.iloc[test].reset_index(drop=True) if times is not None else None
    if effective_task == "classification" and y_train.nunique() < 2:
        raise ValueError("时间训练分区只有一个类别，无法训练分类器；请增加早期数据。")
    for column in categorical:
        cardinality = X_train[column].nunique()
        if cardinality > 50 and cardinality / len(X_train) > 0.8:
            notes.append(f"特征 {column!r} 在训练集中近似唯一 ID；请检查记忆个体/信息泄漏风险。编码最多保留 50 类。")
    folds, skipped = _cv_splits(X_train, y_train, effective_task, t_train, cv_folds, seed)
    notes.extend(skipped)
    candidates = _candidates(effective_task, numeric, categorical, model_families, seed, forecasting)
    score_name = "macro_f1" if effective_task == "classification" else "rmse"
    labels_train = sorted(y_train.unique().tolist()) if effective_task == "classification" else None
    comparisons, selected, selected_name, best_score = [], None, None, None
    captured_warnings = []
    for name, candidate in candidates:
        scores = []
        try:
            for fold_train, fold_valid in folds:
                model = clone(candidate)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    model.fit(X_train.iloc[fold_train], y_train.iloc[fold_train])
                    predictions = model.predict(X_train.iloc[fold_valid])
                    value = _metrics(y_train.iloc[fold_valid], predictions, effective_task, labels_train)[score_name]
                    captured_warnings.extend(str(item.message) for item in caught)
                if not np.isfinite(value):
                    raise ValueError("交叉验证指标不是有限数值。")
                scores.append(value)
            mean = float(np.mean(scores))
            comparisons.append({"candidate": name, "status": "ok", "metric": score_name,
                                "mean": mean, "std": float(np.std(scores)), "fold_scores": scores})
            better = best_score is None or (mean > best_score if effective_task == "classification" else mean < best_score)
            if better:
                selected, selected_name, best_score = candidate, name, mean
        except (ValueError, TypeError, RuntimeError) as exc:
            comparisons.append({"candidate": name, "status": "failed", "error": str(exc)})
    if selected is None:
        raise ValueError("所有候选均无法完成训练交叉验证：" + json.dumps(comparisons, ensure_ascii=False))
    if any(row["status"] == "failed" for row in comparisons):
        notes.append("有候选模型训练失败；原因记录在 model_comparison.json，失败候选未参与选择。")
    fitted = clone(selected).fit(X_train, y_train)
    baseline = clone(candidates[0][1]).fit(X_train, y_train)
    predictions, baseline_predictions = fitted.predict(X_test), baseline.predict(X_test)
    labels = sorted(set(y_train) | set(y_test)) if effective_task == "classification" else None
    probabilities = fitted.predict_proba(X_test) if effective_task == "classification" else None
    baseline_probabilities = baseline.predict_proba(X_test) if effective_task == "classification" else None
    model_classes = fitted.classes_ if effective_task == "classification" else None
    metrics = _metrics(y_test, predictions, effective_task, labels, probabilities, model_classes)
    baseline_metrics = _metrics(y_test, baseline_predictions, effective_task, labels, baseline_probabilities,
                                baseline.classes_ if effective_task == "classification" else None)
    if effective_task == "classification" and set(y_test) - set(y_train):
        notes.append("时间留出集出现训练中未见类别；已在混淆矩阵和 F1 中计入错误，概率指标无法完整计算。")

    fold_windows = []
    for fold_train, fold_valid in folds:
        row = {"train_rows": len(fold_train), "validation_rows": len(fold_valid)}
        if t_train is not None:
            row.update(train_start=t_train.iloc[fold_train].min(), train_end=t_train.iloc[fold_train].max(),
                       validation_start=t_train.iloc[fold_valid].min(), validation_end=t_train.iloc[fold_valid].max())
        fold_windows.append(row)
    split = {"method": split_method, "requested_test_fraction": test_size, "actual_test_fraction": len(test) / len(X),
             "train_rows": len(train), "holdout_rows": len(test), "cv_folds": len(folds), "cv_windows": fold_windows,
             "model_selection": "training CV only; holdout evaluated after selection"}
    if times is not None:
        split.update(train_start=t_train.min(), train_end=t_train.max(), holdout_start=t_test.min(), holdout_end=t_test.max())
    config = {"input_path_at_run": str(source), "input_sha256": source_hash,
              "parameters": {"target": target, "features": list(features) if features is not None else None,
                             "task": requested_task, "time_column": time_column, "test_size": test_size,
                             "seed": seed, "model_families": model_families, "cv_folds": cv_folds,
                             "sheet": sheet, "forecast_lags": lags, "forecast_horizon": forecast_horizon}}
    result = {"task": resolved, "target": target, "features": X.columns.tolist(), "selected_model": selected_name,
              "created_at": utc_now(), "versions": {"python": sys.version.split()[0], "sklearn": sklearn.__version__,
                                                      "pandas": pd.__version__, "numpy": np.__version__},
              "data_checks": {"input_rows": len(data) + missing_count, "missing_target_rows_excluded": missing_count,
                              "lag_warmup_rows_excluded": warmup, "usable_rows": len(X), "feature_infinities": infinity_counts,
                              "identifier_columns_excluded": excluded_identifiers},
              "split": split, "selection_metric": score_name, "training_cv_score": best_score,
              "holdout_metrics": metrics, "baseline_holdout_metrics": baseline_metrics,
              "preprocessing": _preprocessing_audit(fitted, numeric, categorical),
              "warnings": list(dict.fromkeys(notes + captured_warnings)), "candidates": comparisons}
    # Every artifact is published through the task's exclusive writer. Files
    # introduced by another process during training cannot be overwritten.
    prediction_table = pd.DataFrame({"source_row_zero_based": positions[test], "actual": y_test,
                                     "prediction": predictions, "baseline_prediction": baseline_predictions})
    if t_test is not None:
        prediction_table.insert(1, "time_utc", t_test)
    if probabilities is not None:
        for index, label in enumerate(model_classes):
            prediction_table[f"probability_class_{index}"] = probabilities[:, index]
        result["probability_columns"] = {f"probability_class_{i}": label for i, label in enumerate(model_classes)}
    with safe_open(output / "holdout_predictions.csv", "w", encoding="utf-8-sig", newline="") as stream:
        prediction_table.to_csv(stream, index=False)
    assignments = pd.DataFrame({"source_row_zero_based": positions, "partition": "train"})
    assignments.loc[test, "partition"] = "holdout"
    if times is not None:
        assignments["time_utc"] = times
    with safe_open(output / "split_assignments.csv", "w", encoding="utf-8-sig", newline="") as stream:
        assignments.to_csv(stream, index=False)
    if forecasting:
        future_model = clone(selected).fit(X, y)
        history = list(observed_history)
        future_predictions = []
        for _ in range(forecast_horizon):
            row = pd.DataFrame([{f"lag_{lag}": history[-lag] for lag in lags}], columns=X.columns)
            value = float(future_model.predict(row)[0])
            if not np.isfinite(value):
                raise ValueError("递归预测产生非有限数值；请减少预测期数并检查序列。")
            future_predictions.append(value)
            history.append(value)
        dates = pd.date_range(times.iloc[-1], periods=forecast_horizon + 1, freq=frequency)[1:]
        with safe_open(output / "future_forecast.csv", "w", encoding="utf-8-sig", newline="") as stream:
            pd.DataFrame({"time_utc": dates, "prediction": future_predictions,
                          "baseline_persistence": observed_history[-1]}).to_csv(stream, index=False)
        result["forecast"] = {"frequency": frequency, "lags": lags, "horizon": forecast_horizon,
                              "evaluation": "rolling_one_step_with_observed_history",
                              "future": "recursive_multi_step_without_prediction_intervals",
                              "future_model_fit_rows": len(X), "refit_timing": "after final holdout evaluation",
                              "file": "future_forecast.csv"}
    result["diagnostics_chart"] = _plot_diagnostics(output, y_test, predictions, metrics, effective_task, t_test)
    result["output_dir"] = str(output)
    result["bundle_path"] = str(output / "result.json")
    result["report_html"] = str(output / "report.html")
    result = json_safe(result)
    write_json(output / "model_metrics.json", result)
    write_json(output / "model_comparison.json", comparisons)
    _write_reproduction(output, config)
    lines = ["# DataLab 建模结果", "", f"任务：{resolved}；目标：{target}；候选选择：{selected_name}。", "",
             "## 验证设计", "", f"训练 {len(train)} 行，留出 {len(test)} 行；训练内部 {len(folds)} 折验证。",
             f"按训练 CV 的 {score_name} 选择候选；留出集未用于预处理拟合、调参或选择模型。", "",
             "## 留出集结果", "", "```json", json.dumps(json_safe(metrics), ensure_ascii=False, indent=2), "```", "",
             "## 基线对照", "", "```json", json.dumps(json_safe(baseline_metrics), ensure_ascii=False, indent=2), "```", "",
             "![模型诊断](model_diagnostics.png)", "", "分类图的类别编号见 model_metrics.json 的 class_labels。" if effective_task == "classification" else "",
             "## 范围与注意事项", ""]
    lines.extend(f"- {note}" for note in result["warnings"])
    lines.extend(["", "## 复现", "", "在匹配依赖版本的独立环境执行：", "",
                  "```text", "python reproduce.py --input <原始数据文件> --out <新的空目录>", "```", "",
                  "model_config.json 记录数据哈希与参数，code/ 保存运行时 Python 源码。原始数据不自动复制。",
                  "未保存或加载 pickle/joblib 模型文件；通过复现脚本重建模型。", ""])
    safe_write_text(output / "model_report.md", "\n".join(lines), encoding="utf-8")
    _write_generic_bundle(output, result, source, source_hash, input_rows, input_columns, sheet)
    return result


def _model_command(args):
    return train_model(args.input, args.out, target=args.target, features=args.features, task=args.task,
                       time_column=args.time_column, test_size=args.test_size, seed=args.seed,
                       model_families=args.model_families, cv_folds=args.cv_folds, sheet=args.sheet,
                       forecast_lags=args.forecast_lags, forecast_horizon=args.forecast_horizon)


def add_model_parser(subparsers):
    parser = subparsers.add_parser("model", help="分类/回归候选比较、时间验证和单变量预测")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sheet", default=None)
    parser.add_argument("--target", required=True)
    parser.add_argument("--features", nargs="+", default=None)
    parser.add_argument("--task", choices=["auto", "regression", "classification", "forecast"], default="auto")
    parser.add_argument("--time-column", default=None)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--model-families", choices=["linear", "random_forest"], nargs="+", default=None)
    parser.add_argument("--forecast-lags", type=int, nargs="+", default=None)
    parser.add_argument("--forecast-horizon", type=int, default=6)
    parser.set_defaults(func=_model_command)
    return parser
