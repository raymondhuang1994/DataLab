"""Exercise observed non-destructive behavior, including concurrent writers."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from datalab.analysis import analyze
from datalab.cli import main
from datalab.safety import output_session, safe_write_text, cache_directory


def source_file(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    source = folder / "原始 数据.csv"
    source.write_text("category,value\nA,10\nB,20\n", encoding="utf-8")
    return source


def audits(root):
    return [json.loads(p.read_text(encoding="utf-8")) for p in (root / "audit").glob("*.json")]


def test_analysis_records_unchanged_original_and_final_artifacts(tmp_path):
    source = source_file(tmp_path)
    original = source.read_bytes()
    output = tmp_path / "results" / "run-1"
    analyze(source, output, metric="value", group="category", charts=False)
    assert source.read_bytes() == original
    record, = audits(output)
    assert record["status"] == "completed"
    assert record["os_read_only_enforced"] is False
    assert record["inputs"][0]["before"] == record["inputs"][0]["after"]
    assert record["inputs"][0]["status"] == "unchanged"
    assert all(item["status"] == "unchanged" for item in record["outputs"])
    assert any(item["path"].endswith("result.json") for item in record["outputs"])


def test_new_directory_inside_raw_data_tree_is_rejected(tmp_path):
    source = source_file(tmp_path)
    output = source.parent / "new-results"
    with pytest.raises(ValueError, match="原始数据目录"):
        analyze(source, output, metric="value", charts=False)
    assert not output.exists()
    assert list(source.parent.iterdir()) == [source]


def test_even_empty_previous_directory_is_not_a_new_task(tmp_path):
    source = source_file(tmp_path)
    output = tmp_path / "old-empty-folder"
    output.mkdir()
    with pytest.raises(ValueError, match="已有"):
        analyze(source, output, metric="value", charts=False)
    assert list(output.iterdir()) == []


def test_preexisting_config_and_previous_result_are_never_overwritten(tmp_path):
    config = tmp_path / "preferences.json"
    config.write_text('{"language":"zh"}', encoding="utf-8")
    before = config.read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        with output_session(config):
            safe_write_text(config, "changed")
    assert config.read_bytes() == before
    output = tmp_path / "new.txt"
    with output_session(output):
        safe_write_text(output, "first")
        safe_write_text(output, "final")
    with pytest.raises(ValueError, match="不能覆盖"):
        safe_write_text(output, "next-run")
    assert output.read_text(encoding="utf-8") == "final"


def test_scope_escape_is_refused_without_touching_external_file(tmp_path):
    external = tmp_path / "outside.txt"
    external.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError, match="成果目录"):
        with output_session(tmp_path / "run", directory=True):
            safe_write_text(tmp_path / "run/../outside.txt", "changed")
    assert external.read_text(encoding="utf-8") == "original"
    assert audits(tmp_path / "run")[0]["status"] == "failed"


def test_late_created_target_is_not_overwritten(tmp_path):
    output = tmp_path / "run"
    with output_session(output, directory=True):
        target = output / "arrived.txt"
        target.write_text("another writer", encoding="utf-8")
        with pytest.raises(ValueError, match="不能覆盖"):
            safe_write_text(target, "ours")
    assert target.read_text(encoding="utf-8") == "another writer"


def test_concurrent_creation_has_only_one_winner(tmp_path):
    barrier = threading.Barrier(2)
    target = tmp_path / "shared.txt"
    (tmp_path / "audit").mkdir()
    def write(value):
        try:
            with output_session(target):
                barrier.wait(timeout=5)
                safe_write_text(target, value)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, value) for value in ("first", "second")]
        success = [f.result(timeout=10) for f in futures]
    assert sum(success) == 1
    assert target.read_text(encoding="utf-8") in {"first", "second"}


def test_external_input_change_fails_and_is_not_restored(tmp_path):
    source = source_file(tmp_path)
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="输入发生变化"):
        with output_session(output, raw_inputs=[source], directory=True):
            safe_write_text(output / "result.txt", "computed")
            source.write_text("external change", encoding="utf-8")
    assert source.read_text(encoding="utf-8") == "external change"
    assert audits(output)[0]["inputs"][0]["status"] == "changed"


def test_external_output_change_is_detected_without_repair(tmp_path):
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="产物.*外部修改"):
        with output_session(output, directory=True):
            safe_write_text(output / "result.txt", "computed")
            (output / "result.txt").write_text("external", encoding="utf-8")
    assert (output / "result.txt").read_text(encoding="utf-8") == "external"
    assert audits(output)[0]["outputs"][0]["status"] == "changed"


def test_cache_is_unique_between_exports_in_same_results_directory(tmp_path):
    with output_session(tmp_path / "one.txt"):
        first = cache_directory("renderer")
        safe_write_text(first / "settings", "old cache")
        safe_write_text(tmp_path / "one.txt", "one")
    with output_session(tmp_path / "two.txt"):
        second = cache_directory("renderer")
        safe_write_text(second / "settings", "new cache")
        safe_write_text(tmp_path / "two.txt", "two")
    assert first != second
    assert (first / "settings").read_text(encoding="utf-8") == "old cache"


def test_hard_link_added_to_owned_output_prevents_updates(tmp_path):
    output = tmp_path / "run"
    with pytest.raises(ValueError):
        with output_session(output, directory=True):
            target = output / "ours.txt"
            safe_write_text(target, "original")
            os.link(target, tmp_path / "alias.txt")
            safe_write_text(target, "updated")
    assert (tmp_path / "alias.txt").read_text(encoding="utf-8") == "original"


@pytest.mark.skipif(os.name == "nt", reason="Windows junction equivalent below")
def test_symlink_input_protects_both_source_directories(tmp_path):
    source = source_file(tmp_path)
    linked = tmp_path / "linked-inputs"
    linked.mkdir()
    alias = linked / "source.csv"
    alias.symlink_to(source)
    for directory in (source.parent, linked):
        with pytest.raises(ValueError, match="原始数据目录"):
            analyze(alias, directory / "new-run", metric="value", charts=False)
        assert not (directory / "new-run").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows junction equivalent below")
def test_symlink_directory_inside_task_cannot_redirect_writes(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    with output_session(tmp_path / "run", directory=True):
        (tmp_path / "run/link").symlink_to(external, target_is_directory=True)
        with pytest.raises(ValueError, match="成果目录|链接"):
            safe_write_text(tmp_path / "run/link/new.txt", "forbidden")
    assert not (external / "new.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction test")
def test_windows_junction_cannot_redirect_task_writes(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    with output_session(tmp_path / "run", directory=True):
        link = tmp_path / "run/link"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)], check=True, capture_output=True)
        try:
            with pytest.raises(ValueError, match="成果目录|链接"):
                safe_write_text(link / "new.txt", "forbidden")
        finally:
            link.rmdir()
    assert not (external / "new.txt").exists()
