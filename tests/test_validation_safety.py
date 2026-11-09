"""A matching prefix is insufficient when a CSV row has the wrong width."""
import csv
import json
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "data-lab" / "scripts"))
from datalab.validation import validate_bundle


def _bundle(tmp_path, second_csv_row):
    original = tmp_path / "original.csv"
    original.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")
    data = {"schema_version": "1.0", "title": "Row-width validation", "source": {
        "path": str(original), "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "rows": 2, "columns": ["a", "b", "c"]}, "tables": [{
        "id": "values", "title": "Values", "columns": ["a", "b", "c"],
        "rows": [[1, 2, 3], [4, 5, 6]], "source_file": "values.csv",
    }], "charts": []}
    path = tmp_path / "result.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with (tmp_path / "values.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["a", "b", "c"])
        writer.writerow([1, 2, 3])
        writer.writerow(second_csv_row)
    return path


@pytest.mark.parametrize("bad_row", [[4, 5], [4, 5, 6, 7], []], ids=["missing_column", "extra_column", "empty_row"])
def test_csv_row_width_mismatch_fails_even_when_shared_values_match(tmp_path, bad_row):
    result = validate_bundle(_bundle(tmp_path, bad_row), verify_source=False)
    assert result["status"] == "failed"
    assert next(check for check in result["checks"] if check["name"] == "table_file:values")["status"] == "failed"


def test_exact_csv_row_width_and_values_pass(tmp_path):
    result = validate_bundle(_bundle(tmp_path, [4, 5, 6]), verify_source=False)
    assert result["checks_status"] == "passed"
    assert result["status"] == "needs_review"
    assert result["coverage"]["source"]["status"] == "not_checked"
