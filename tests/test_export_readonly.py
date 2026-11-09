"""Exports preserve original inputs and pre-existing report files."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

from datalab.dashboard import SourceStore, build_dashboard
from datalab.report import render_html
from datalab.safety import output_session
from datalab.statistics import add_stats_parser
from datalab.word_report import export_word


def fixture(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "原始 数据.csv"
    source.write_text("group,value\nA,1\nA,2\nB,4\nB,5\n", encoding="utf-8")
    bundle_dir = tmp_path / "prior-results"
    bundle_dir.mkdir()
    bundle = bundle_dir / "result.json"
    bundle.write_text(json.dumps({
        "schema_version": "1.1", "title": "只读导出测试", "summary": ["合计 12。"],
        "source": {"path": str(source), "rows": 4, "columns": ["group", "value"]},
        "tables": [{"id": "total", "title": "汇总", "columns": ["合计"], "rows": [[12]]}],
        "charts": [], "methodology": ["保留原件"], "limitations": [],
    }, ensure_ascii=False), encoding="utf-8")
    return source, bundle


@pytest.mark.parametrize("export,suffix", [(render_html, "html"), (export_word, "docx")])
def test_existing_unrelated_report_is_never_overwritten(tmp_path, export, suffix):
    source, bundle = fixture(tmp_path)
    output = tmp_path / "reports" / f"old.{suffix}"
    output.parent.mkdir()
    output.write_bytes(b"pre-existing historical report")
    originals = {p: p.read_bytes() for p in (source, bundle, output)}
    with pytest.raises(ValueError):
        export(bundle, output)
    assert {p: p.read_bytes() for p in originals} == originals


@pytest.mark.parametrize("export,suffix", [(render_html, "html"), (export_word, "docx")])
def test_new_export_is_separate_and_second_invocation_cannot_replace_it(tmp_path, export, suffix):
    source, bundle = fixture(tmp_path)
    output = tmp_path / "new-results" / f"report.{suffix}"
    originals = {p: p.read_bytes() for p in (source, bundle)}
    export(bundle, output)
    assert output.stat().st_size > 100
    previous = output.read_bytes()
    with pytest.raises(ValueError):
        export(bundle, output)
    assert output.read_bytes() == previous
    assert {p: p.read_bytes() for p in originals} == originals


def test_same_active_session_can_finish_its_own_html(tmp_path):
    source, bundle = fixture(tmp_path)
    root = tmp_path / "current-run"
    output = root / "report.html"
    with output_session(root, raw_inputs=[source], directory=True):
        render_html(bundle, output)
        render_html(bundle, output)
    assert output.is_file()


@pytest.mark.parametrize("export,suffix", [(render_html, "html"), (export_word, "docx")])
def test_export_cannot_create_new_file_in_raw_input_directory(tmp_path, export, suffix):
    source, bundle = fixture(tmp_path)
    output = source.parent / "nested" / f"report.{suffix}"
    before = {p.relative_to(source.parent): p.read_bytes() for p in source.parent.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        export(bundle, output)
    assert not output.parent.exists()
    assert {p.relative_to(source.parent): p.read_bytes() for p in source.parent.rglob("*") if p.is_file()} == before


def test_dashboard_does_not_overwrite_existing_config_destination(tmp_path):
    source, _ = fixture(tmp_path)
    output = tmp_path / "project.json"
    output.write_text('{"name":"existing project"}', encoding="utf-8")
    original = output.read_bytes()
    with pytest.raises(ValueError):
        build_dashboard(source, output, metric="value", group="group")
    assert output.read_bytes() == original


def test_statistics_export_does_not_overwrite_old_results(tmp_path):
    source, _ = fixture(tmp_path)
    output = tmp_path / "reports" / "comparison.json"
    output.parent.mkdir()
    output.write_text('{"old":true}', encoding="utf-8")
    parser = argparse.ArgumentParser()
    add_stats_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["compare", "--input", str(source), "--out", str(output),
                              "--group", "group", "--metric", "value"])
    with pytest.raises(ValueError):
        args.func(args)
    assert output.read_text(encoding="utf-8") == '{"old":true}'


def test_live_dashboard_allows_external_updates_without_writing_source(tmp_path):
    source, _ = fixture(tmp_path)
    store = SourceStore(source, live=True, metric="value", group="group")
    changed = "group,value\nA,7\nB,8\n"
    source.write_text(changed, encoding="utf-8")
    assert store.refresh(force=True)
    snapshot = store.snapshot()
    assert snapshot["protection"]["source_access"] == "read_only"
    assert snapshot["protection"]["external_source_updates_allowed"] is True
    assert snapshot["protection"]["os_isolation_verified"] is False
    assert source.read_text(encoding="utf-8") == changed


def render_module():
    script = Path(__file__).resolve().parents[1] / "skills/data-lab/scripts/render_word.py"
    spec = importlib.util.spec_from_file_location("datalab_render_word_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_word_renderer_only_receives_task_owned_copy(tmp_path, monkeypatch):
    module = render_module()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "原始文档.docx"
    source.write_bytes(b"original document bytes")
    output = tmp_path / "rendered"
    calls = []

    def fake_convert(arguments, timeout):
        calls.append(arguments)
        copied_input = Path(arguments[-1])
        target = Path(arguments[arguments.index("--outdir") + 1])
        assert copied_input != source
        assert copied_input.is_relative_to(output)
        assert copied_input.read_bytes() == source.read_bytes()
        assert target.is_relative_to(output)
        (target / "source.pdf").write_bytes(b"%PDF-1.4 generated test data")

    monkeypatch.setattr(module, "run_command", fake_convert)
    result = module.render_word(source, output, "fake-soffice")
    assert len(calls) == 1
    assert source.read_bytes() == b"original document bytes"
    assert Path(result["pdf"]).read_bytes().startswith(b"%PDF")
    assert result["protection"]["renderer_input"] == "task_owned_copy"
    with pytest.raises(ValueError):
        module.render_word(source, output, "fake-soffice")


def test_renderer_failure_keeps_original_and_prior_results(tmp_path, monkeypatch):
    module = render_module()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "original.docx"
    source.write_bytes(b"original")
    historical = tmp_path / "old-report.pdf"
    historical.write_bytes(b"old report")

    def fail(*args, **kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(module, "run_command", fail)
    with pytest.raises(RuntimeError, match="renderer unavailable"):
        module.render_word(source, tmp_path / "failed-run", "fake-soffice")
    assert source.read_bytes() == b"original"
    assert historical.read_bytes() == b"old report"
