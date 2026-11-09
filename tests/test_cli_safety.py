import builtins
import pytest
from datalab.analysis import analyze
from datalab.cli import main


def test_validation_output_cannot_replace_bundle_or_source(tmp_path):
    (tmp_path / "inputs").mkdir()
    source = tmp_path / "inputs/data.csv"
    source.write_text("x\n3\n", encoding="utf-8")
    out = tmp_path / "analysis"
    analyze(source, out, metric="x", charts=False)
    bundle = out / "result.json"
    for protected in (source, bundle, out / "tables/profile.csv"):
        before = protected.read_bytes()
        assert main(["validate", "--bundle", str(bundle), "--out", str(protected)]) == 2
        assert protected.read_bytes() == before


def test_help_and_word_do_not_import_modeling_dependencies(monkeypatch):
    original = builtins.__import__
    def restricted(name, *args, **kwargs):
        if name.split(".")[0] in {"sklearn", "scipy"}:
            raise ImportError("Modeling packages are deliberately unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", restricted)
    for arguments in (["--help"], ["word", "--help"]):
        with pytest.raises(SystemExit) as stopped:
            main(arguments)
        assert stopped.value.code == 0
