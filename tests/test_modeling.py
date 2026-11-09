"""Numerical and leakage checks for the executable modeling workflow."""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "data-lab" / "scripts"))
from datalab.modeling import _cv_splits, _parse_times, _resolve_task, train_model
from datalab.validation import validate_bundle
from datalab.word_report import export_word


def _csv(tmp_path, data, name="data.csv"):
    path = tmp_path / name
    data.to_csv(path, index=False)
    return path


def test_regression_beats_mean_baseline_and_reproduces(tmp_path):
    rng = np.random.default_rng(91)
    x, z = rng.normal(size=(2, 150))
    data = pd.DataFrame({"x": x, "z": z, "target": 3*x - 2*z + rng.normal(scale=0.05, size=150)})
    source = _csv(tmp_path, data)
    out = tmp_path / "result"
    result = train_model(source, out, "target", task="regression", model_families=["linear"], cv_folds=3)
    assert result["holdout_metrics"]["rmse"] < 0.2
    assert result["holdout_metrics"]["rmse"] < result["baseline_holdout_metrics"]["rmse"] / 10
    assert result["selected_model"].startswith("ridge_")
    assert (out / "model_diagnostics.png").stat().st_size > 1000
    assert (out / "code" / "datalab" / "modeling.py").is_file()
    bundle = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert bundle["schema_version"] == "1.0"
    assert bundle["validation"]["status"] == "needs_review"
    assert bundle["source"]["rows"] == len(data)
    assert validate_bundle(out / "result.json")["checks_status"] == "passed"
    assert "data:image/png;base64," in (out / "report.html").read_text(encoding="utf-8")
    word = export_word(out / "result.json", out / "model_report.docx")
    assert word["tables"] >= 2 and word["charts"] == 1
    with zipfile.ZipFile(out / "model_report.docx") as package:
        assert any(name.startswith("word/media/") for name in package.namelist())
    # Test the delivered rerun script, rather than only checking its text.
    run = subprocess.run([sys.executable, str(out / "reproduce.py"), "--input", str(source),
                          "--out", str(tmp_path / "reproduced")], capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert run.returncode == 0, run.stderr
    repeated = json.loads((tmp_path / "reproduced" / "model_metrics.json").read_text(encoding="utf-8"))
    assert repeated["holdout_metrics"] == result["holdout_metrics"]


def test_temporal_holdout_cannot_change_selection_or_imputation(tmp_path):
    dates = pd.date_range("2024-01-01", periods=60).repeat(2)
    x = np.arange(120, dtype=float)
    x[[2, 8, 15, 30]] = np.nan
    x[96:] = 100000  # This cannot affect training medians or scaling.
    categories = np.where(np.arange(120) < 96, "known", "holdout_only")
    data = pd.DataFrame({"date": dates, "x": x, "category": categories,
                         "target": np.arange(120, dtype=float) * 3 + 2})
    source = _csv(tmp_path, data)
    result = train_model(source, tmp_path / "one", "target", task="regression", time_column="date",
                         model_families=["linear"], cv_folds=3)
    assigned = pd.read_csv(tmp_path / "one" / "split_assignments.csv")
    assert assigned.groupby("time_utc")["partition"].nunique().max() == 1
    train_rows = assigned.loc[assigned.partition == "train", "source_row_zero_based"]
    expected_median = data.iloc[train_rows].x.median()
    assert result["preprocessing"]["numeric_imputer_statistics"]["x"] == expected_median
    assert "holdout_only" not in result["preprocessing"]["categorical_vocabulary"]["category"]
    for fold in result["split"]["cv_windows"]:
        assert fold["train_end"] < fold["validation_start"]
    data.loc[96:, "target"] += 1000000
    changed = train_model(_csv(tmp_path, data, "changed.csv"), tmp_path / "two", "target", task="regression",
                          time_column="date", model_families=["linear"], cv_folds=3)
    assert changed["selected_model"] == result["selected_model"]
    assert changed["training_cv_score"] == result["training_cv_score"]
    assert changed["preprocessing"] == result["preprocessing"]
    assert changed["holdout_metrics"] != result["holdout_metrics"]


def test_cv_keeps_duplicate_timestamp_groups_together():
    times = pd.Series(pd.date_range("2024-01-01", periods=24, tz="UTC").repeat(3))
    X = pd.DataFrame({"x": np.arange(len(times))})
    y = pd.Series(np.arange(len(times), dtype=float))
    folds, _ = _cv_splits(X, y, "regression", times, 4, 42)
    for train, valid in folds:
        assert times.iloc[train].max() < times.iloc[valid].min()
        assert not set(times.iloc[train]) & set(times.iloc[valid])


def test_classification_stratifies_and_reports_missing_targets(tmp_path):
    rng = np.random.default_rng(13)
    x = rng.normal(size=200)
    data = pd.DataFrame({"signal": x, "noise": rng.normal(size=200),
                         "target": np.where(x > 0, "yes", "no")})
    data.loc[0, "target"] = None
    result = train_model(_csv(tmp_path, data), tmp_path / "classification", "target",
                         task="classification", model_families=["linear"], cv_folds=3)
    assert result["split"]["method"] == "stratified_random"
    assert result["data_checks"]["missing_target_rows_excluded"] == 1
    assert result["holdout_metrics"]["accuracy"] > 0.85
    assert result["holdout_metrics"]["roc_auc"] > 0.95
    assert result["holdout_metrics"]["macro_f1"] > result["baseline_holdout_metrics"]["macro_f1"]
    predictions = pd.read_csv(tmp_path / "classification" / "holdout_predictions.csv")
    assert np.allclose(predictions.filter(like="probability_").sum(axis=1), 1)
    assert set(predictions.actual) == {"yes", "no"}
    assert validate_bundle(tmp_path / "classification" / "result.json")["checks_status"] == "passed"
    bundle = json.loads((tmp_path / "classification" / "result.json").read_text(encoding="utf-8"))
    assert any(table["id"] == "model_confusion_matrix" for table in bundle["tables"])


def test_random_forest_candidate_executes(tmp_path):
    rng = np.random.default_rng(28)
    x = rng.uniform(-3, 3, size=120)
    data = pd.DataFrame({"x": x, "target": x*x})
    result = train_model(_csv(tmp_path, data), tmp_path / "forest", "target", task="regression",
                         model_families=["random_forest"], cv_folds=2)
    assert result["selected_model"].startswith("random_forest_")
    assert result["holdout_metrics"]["rmse"] < result["baseline_holdout_metrics"]["rmse"] / 2


def test_rare_class_fails_instead_of_unstratified_fallback(tmp_path):
    data = pd.DataFrame({"x": np.arange(30), "target": ["a"] * 29 + ["b"]})
    with pytest.raises(ValueError, match="分层"):
        train_model(_csv(tmp_path, data), tmp_path / "out", "target", task="classification")


@pytest.mark.parametrize("features", [["x", "target"], ["x", "date"]])
def test_target_and_split_timestamp_cannot_be_features(tmp_path, features):
    data = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=30),
                         "x": np.arange(30), "target": np.arange(30)})
    with pytest.raises(ValueError, match="不能包含"):
        train_model(_csv(tmp_path, data), tmp_path / "out", "target", features=features,
                    task="regression", time_column="date")


def test_forecast_reports_honest_evaluation_and_future_output(tmp_path):
    data = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=90),
                         "target": np.arange(90) * 2.0 + 10})
    result = train_model(_csv(tmp_path, data), tmp_path / "forecast", "target", task="forecast",
                         time_column="date", model_families=["linear"], cv_folds=3,
                         forecast_horizon=4, forecast_lags=[1, 2, 3])
    assert result["holdout_metrics"]["rmse"] < result["baseline_holdout_metrics"]["rmse"]
    assert result["forecast"]["evaluation"] == "rolling_one_step_with_observed_history"
    assert result["forecast"]["future_model_fit_rows"] == 87
    future = pd.read_csv(tmp_path / "forecast" / "future_forecast.csv")
    assert len(future) == 4
    assert np.all(np.diff(future.prediction) > 0)
    assert future.prediction.iloc[0] > data.target.iloc[-1]
    assert np.allclose(future.prediction, [190, 192, 194, 196], atol=1)
    assert pd.Timestamp(future.time_utc.iloc[0]) > pd.Timestamp(data.date.iloc[-1], tz="UTC")


@pytest.mark.parametrize("problem", ["missing_target", "duplicate_time", "missing_period"])
def test_forecast_rejects_ambiguous_time_series(tmp_path, problem):
    data = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=45), "target": np.arange(45, dtype=float)})
    if problem == "missing_target":
        data.loc[5, "target"] = np.nan
    elif problem == "duplicate_time":
        data.loc[5, "date"] = data.loc[4, "date"]
    else:
        data = data.drop(index=5)
    with pytest.raises(ValueError):
        train_model(_csv(tmp_path, data), tmp_path / "out", "target", task="forecast", time_column="date")


def test_mixed_offsets_are_normalised_but_naive_aware_mixture_is_rejected():
    values, naive = _parse_times(pd.Series(["2024-01-01T08:00:00+08:00", "2024-01-01T01:00:00Z"]))
    assert not naive
    assert values.iloc[0].isoformat() == "2024-01-01T00:00:00+00:00"
    assert values.iloc[1].isoformat() == "2024-01-01T01:00:00+00:00"
    with pytest.raises(ValueError, match="混合"):
        _parse_times(pd.Series(["2024-01-01T08:00:00", "2024-01-01T01:00:00Z"]))


def test_small_integer_target_requires_explicit_task():
    with pytest.raises(ValueError, match="歧义"):
        _resolve_task(pd.Series([0, 1, 2, 3] * 10), "auto")
    assert _resolve_task(pd.Series([0, 1, 2, 3] * 10), "classification") == "classification"


def test_default_features_exclude_identifier_names(tmp_path):
    rng = np.random.default_rng(6)
    x = rng.normal(size=60)
    data = pd.DataFrame({"customer_id": [f"user_{i}" for i in range(60)], "x": x, "target": 4*x})
    result = train_model(_csv(tmp_path, data), tmp_path / "out", "target", task="regression",
                         model_families=["linear"], cv_folds=2)
    assert result["features"] == ["x"]
    assert result["data_checks"]["identifier_columns_excluded"] == ["customer_id"]


def test_source_changed_during_read_is_rejected(tmp_path, monkeypatch):
    from datalab import modeling
    data = pd.DataFrame({"x": range(30), "target": np.arange(30, dtype=float)})
    source = _csv(tmp_path, data)
    real_load = modeling.load_frame

    def changing_load(path, sheet=None):
        frame = real_load(path, sheet=sheet)
        data.assign(target=data.target + 100).to_csv(path, index=False)
        return frame

    monkeypatch.setattr(modeling, "load_frame", changing_load)
    with pytest.raises(ValueError, match="读取期间源文件发生变化"):
        train_model(source, tmp_path / "out", "target", task="regression")
    assert not (tmp_path / "out").exists()


def test_input_inside_output_directory_is_rejected_without_modification(tmp_path):
    data = pd.DataFrame({"x": range(30), "target": np.arange(30, dtype=float)})
    source = _csv(tmp_path, data)
    original = source.read_bytes()
    with pytest.raises(ValueError, match="不能包含输入文件"):
        train_model(source, tmp_path, "target", task="regression")
    assert source.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == [source.name]


def test_nonempty_output_directory_is_preserved(tmp_path):
    data = pd.DataFrame({"x": range(30), "target": np.arange(30, dtype=float)})
    source = _csv(tmp_path, data)
    output = tmp_path / "out"
    output.mkdir()
    protected = output / "model_metrics.json"
    protected.write_text("existing result", encoding="utf-8")
    with pytest.raises(ValueError, match="目录必须为空"):
        train_model(source, output, "target", task="regression")
    assert protected.read_text(encoding="utf-8") == "existing result"
    assert list(output.iterdir()) == [protected]


def test_output_file_introduced_during_training_is_not_overwritten(tmp_path, monkeypatch):
    from datalab import modeling
    data = pd.DataFrame({"x": range(30), "target": np.arange(30, dtype=float)})
    source = _csv(tmp_path, data)
    output = tmp_path / "out"
    protected = output / "model_metrics.json"
    real_load = modeling.load_frame

    def adding_output(path, sheet=None):
        frame = real_load(path, sheet=sheet)
        output.mkdir()
        protected.write_text("keep this concurrent result", encoding="utf-8")
        return frame

    monkeypatch.setattr(modeling, "load_frame", adding_output)
    with pytest.raises(ValueError, match="目录必须为空"):
        train_model(source, output, "target", task="regression", model_families=["linear"], cv_folds=2)
    assert protected.read_text(encoding="utf-8") == "keep this concurrent result"
    assert list(output.iterdir()) == [protected]
