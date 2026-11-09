"""Exercise redirected CLI output under a legacy Windows code page."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "skills/data-lab/scripts/run.py"


def invoke(*arguments):
    return subprocess.run(
        [sys.executable, str(ENTRY), *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )


def test_project_json_keeps_chinese_in_redirected_output():
    result = invoke("config", "--project", ROOT / "examples/project.json", "--metric-id", "gross_margin")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["metric_definition"]["label"] == "整体毛利率"


def test_missing_chinese_path_keeps_error_readable(tmp_path):
    result = invoke("config", "--project", tmp_path / "不存在.json")
    assert result.returncode == 2
    assert "不存在.json" in result.stderr
