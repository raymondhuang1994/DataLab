"""Regression tests for silent CSV loss and destructive report destinations."""
import argparse
import base64
import csv
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from datalab.io import load_frame
from datalab.report import render_html
from datalab.statistics import add_stats_parser
from datalab.word_report import export_word


@pytest.mark.parametrize("suffix,delimiter", [("csv", ","), ("tsv", "\t")])
@pytest.mark.parametrize("rows", [
    [["1", "2", "3"], ["4", "5", "6"]],
    [["1", "2"], ["3"]],
    [["1", "2"], ["3", "4", ""]],
])
def test_delimited_records_must_match_header_width(tmp_path, suffix, delimiter, rows):
    source = tmp_path / f"source.{suffix}"
    with source.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=delimiter).writerows([["a", "b"], *rows])
    original = source.read_bytes()
    with pytest.raises(ValueError, match="字段数与表头不符"):
        load_frame(source)
    assert source.read_bytes() == original


@pytest.mark.parametrize("suffix,delimiter", [("csv", ","), ("tsv", "\t")])
def test_quoted_multiline_delimiters_and_missing_cells_are_preserved(tmp_path, suffix, delimiter):
    source = tmp_path / f"multiline.{suffix}"
    rows = [["customer_id", "note", "amount"],
            ["001", f'第一行{delimiter}内容\n第二行 "引号"', "10.5"],
            ["002", "NA", ""],
            ["003", "", "20"]]
    with source.open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream, delimiter=delimiter).writerows(rows)
    frame = load_frame(source)
    assert frame["customer_id"].tolist() == ["001", "002", "003"]
    assert frame["note"].iloc[0] == rows[1][1]
    assert frame["note"].iloc[1] == "NA"
    assert pd.isna(frame["note"].iloc[2]) and pd.isna(frame["amount"].iloc[1])
    assert frame["amount"].sum() == pytest.approx(30.5)


def test_blank_physical_records_skipped_but_empty_cells_retained(tmp_path):
    source = tmp_path / "blanks.csv"
    source.write_text('\na,b\n\n1,2\n,\n3,4\n\n', encoding="utf-8")
    frame = load_frame(source)
    assert len(frame) == 3
    assert frame.iloc[1].isna().all()
    assert frame["a"].sum() == 4


def test_malformed_quoted_record_is_rejected(tmp_path):
    source = tmp_path / "unclosed.csv"
    source.write_text('a,b\n"unterminated\n1,2', encoding="utf-8")
    with pytest.raises(ValueError, match="格式无效"):
        load_frame(source)


def bundle_fixture(tmp_path, relative_source=False):
    folder = tmp_path / "bundle"
    folder.mkdir()
    source = folder / "source.csv" if relative_source else tmp_path / "source.csv"
    source.write_text("sales\n10\n", encoding="utf-8")
    table = folder / "tables" / "summary.csv"
    table.parent.mkdir()
    table.write_text("sales\n10\n", encoding="utf-8")
    png = folder / "charts" / "chart.png"
    png.parent.mkdir()
    png.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
    svg = png.with_suffix(".svg")
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    metrics = folder / "model_metrics.json"
    metrics.write_text('{"mae":1}', encoding="utf-8")
    data = {"schema_version": "1.0", "title": "测试报告", "summary": ["合计 10"],
            "source": {"path": "source.csv" if relative_source else str(source), "rows": 1},
            "tables": [{"id": "summary", "title": "汇总", "columns": ["sales"], "rows": [[10]], "source_file": "tables/summary.csv"}],
            "charts": [{"title": "图表", "path": "charts/chart.png", "svg_path": "charts/chart.svg", "source_file": "tables/summary.csv"}],
            "model": {"metrics_file": "model_metrics.json"}}
    bundle = folder / "result.json"
    bundle.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return bundle, {"bundle": bundle, "source": source, "table": table, "png": png, "svg": svg, "metrics": metrics}


@pytest.mark.parametrize("resource", ["bundle", "source", "table", "png", "svg", "metrics"])
def test_report_refuses_to_overwrite_every_declared_resource(tmp_path, resource):
    bundle, paths = bundle_fixture(tmp_path)
    originals = {name: path.read_bytes() for name, path in paths.items()}
    with pytest.raises(ValueError, match="不能覆盖"):
        render_html(bundle, paths[resource])
    assert {name: path.read_bytes() for name, path in paths.items()} == originals


def test_report_protects_relative_source_and_normalized_alias(tmp_path):
    bundle, paths = bundle_fixture(tmp_path, relative_source=True)
    with pytest.raises(ValueError, match="不能覆盖"):
        render_html(bundle, paths["source"])
    with pytest.raises(ValueError, match="不能覆盖"):
        render_html(bundle, bundle.parent / "unused" / ".." / "tables" / "summary.csv")


def test_report_protects_hardlink_alias(tmp_path):
    bundle, paths = bundle_fixture(tmp_path)
    alias = tmp_path / "hardlink-report.html"
    try:
        os.link(paths["source"], alias)
    except OSError as exc:
        pytest.skip(f"Hardlinks are unavailable on this test filesystem: {exc}")
    before = paths["source"].read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        render_html(bundle, alias)
    assert paths["source"].read_bytes() == before


def test_report_can_write_external_html_and_regenerate_report(tmp_path):
    bundle, paths = bundle_fixture(tmp_path)
    originals = {name: path.read_bytes() for name, path in paths.items()}
    output = tmp_path / "outside" / "review.html"
    result = render_html(bundle, output)
    assert Path(result["output_path"]) == output
    document = output.read_text(encoding="utf-8")
    assert "测试报告" in document and "data:image/png;base64," in document
    assert {name: path.read_bytes() for name, path in paths.items()} == originals
    inside = bundle.parent / "report.html"
    render_html(bundle, inside)
    render_html(bundle, inside)
    assert inside.read_text(encoding="utf-8") == document


@pytest.mark.parametrize("resource", ["bundle", "source", "table", "png", "svg", "metrics"])
def test_word_uses_shared_guard_before_output_creation(tmp_path, resource):
    bundle, paths = bundle_fixture(tmp_path)
    originals = {name: path.read_bytes() for name, path in paths.items()}
    with pytest.raises(ValueError, match="不能覆盖"):
        export_word(bundle, paths[resource])
    assert {name: path.read_bytes() for name, path in paths.items()} == originals


def test_word_refuses_docx_hardlink_to_bundle_resource(tmp_path):
    bundle, paths = bundle_fixture(tmp_path)
    alias = tmp_path / "resource-alias.docx"
    try:
        os.link(paths["source"], alias)
    except OSError as exc:
        pytest.skip(f"Hardlinks are unavailable on this test filesystem: {exc}")
    original = paths["source"].read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        export_word(bundle, alias)
    assert paths["source"].read_bytes() == original


def _compare_args(source, output):
    parser = argparse.ArgumentParser()
    add_stats_parser(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["compare", "--input", str(source), "--out", str(output),
                              "--group", "group", "--metric", "value"])


def test_compare_destination_cannot_overwrite_input_or_alias(tmp_path):
    source = tmp_path / "experiment.csv"
    source.write_text("group,value\nA,1\nA,2\nB,4\nB,5\n", encoding="utf-8")
    original = source.read_bytes()
    args = _compare_args(source, source)
    with pytest.raises(ValueError, match="不能覆盖"):
        args.func(args)
    assert source.read_bytes() == original
    alias = tmp_path / "results.json"
    try:
        os.link(source, alias)
    except OSError as exc:
        pytest.skip(f"Hardlinks are unavailable on this test filesystem: {exc}")
    args = _compare_args(source, alias)
    with pytest.raises(ValueError, match="不能覆盖"):
        args.func(args)
    assert source.read_bytes() == original


def test_compare_can_export_results_without_changing_input(tmp_path):
    source = tmp_path / "experiment.csv"
    source.write_text("group,value\nA,1\nA,2\nB,4\nB,5\n", encoding="utf-8")
    original = source.read_bytes()
    output = tmp_path / "results" / "comparison.json"
    args = _compare_args(source, output)
    result = args.func(args)
    assert result["effect"] == pytest.approx(3)
    assert json.loads(output.read_text(encoding="utf-8"))["effect"] == pytest.approx(3)
    assert source.read_bytes() == original
