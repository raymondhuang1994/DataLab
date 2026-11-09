"""Source preservation and no-clobber behavior of models and chart artifacts."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datalab.charts import chart_table
from datalab.modeling import train_model


def source_file(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "原始 数据.csv"
    values = np.arange(60, dtype=float)
    pd.DataFrame({"feature": values, "target": 3 * values + 2}).to_csv(source, index=False)
    return source


def train(source, output):
    return train_model(source, output, "target", features=["feature"],
                       task="regression", model_families=["linear"], cv_folds=2)


def test_training_preserves_original_and_produces_separate_evidence(tmp_path):
    source = source_file(tmp_path)
    before = source.read_bytes()
    output = tmp_path / "tasks" / "model"
    result = train(source, output)
    assert source.read_bytes() == before
    assert result["holdout_metrics"]["rmse"] < 1
    assert (output / "holdout_predictions.csv").is_file()
    assert (output / "reproduce.py").is_file()
    assert list((output / "audit").glob("*.json"))


def test_training_rejects_existing_output_without_touching_it(tmp_path):
    source = source_file(tmp_path)
    output = tmp_path / "history"
    output.mkdir()
    original = output / "previous-report.md"
    original.write_text("previous result", encoding="utf-8")
    with pytest.raises(ValueError):
        train(source, output)
    assert original.read_text(encoding="utf-8") == "previous result"
    assert list(output.iterdir()) == [original]


def test_training_cannot_create_artifacts_below_raw_data_folder(tmp_path):
    source = source_file(tmp_path)
    initial = list(source.parent.iterdir())
    with pytest.raises(ValueError):
        train(source, source.parent / "analysis")
    assert list(source.parent.iterdir()) == initial


def test_file_arriving_during_training_is_not_overwritten(tmp_path, monkeypatch):
    from datalab import modeling
    source = source_file(tmp_path)
    source_bytes = source.read_bytes()
    output = tmp_path / "task"
    original_plot = modeling._plot_diagnostics

    def another_writer(*args, **kwargs):
        (output / "model_report.md").write_text("another process owns this", encoding="utf-8")
        return original_plot(*args, **kwargs)

    monkeypatch.setattr(modeling, "_plot_diagnostics", another_writer)
    with pytest.raises(ValueError):
        train(source, output)
    assert (output / "model_report.md").read_text(encoding="utf-8") == "another process owns this"
    assert source.read_bytes() == source_bytes


def sample_table():
    return {"id": "sample", "title": "Sample", "source_file": "sample.csv",
            "columns": ["group", "amount"], "rows": [["A", 1], ["B", 2]]}


def test_standalone_chart_cannot_replace_existing_figures(tmp_path):
    destination = tmp_path / "figures"
    chart_table(sample_table(), destination, "comparison")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in destination.glob("comparison.*")}
    with pytest.raises(ValueError):
        chart_table(sample_table(), destination, "comparison")
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in destination.glob("comparison.*")} == before


def test_chart_filename_cannot_escape_task_directory(tmp_path):
    original = tmp_path / "previous.png"
    original.write_bytes(b"previous image")
    with pytest.raises(ValueError):
        chart_table(sample_table(), tmp_path / "figures", "../previous")
    assert original.read_bytes() == b"previous image"
