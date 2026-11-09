import json
from datalab.analysis import analyze
from datalab.metrics import format_value
from datalab.validation import validate_bundle


def test_integer_format_retains_units_above_float_precision():
    assert format_value(27021597764222973, {"unit":"number", "precision":0}) == "27,021,597,764,222,973"
    assert format_value(27021597764222973, {"unit":"currency", "currency":"CNY", "precision":2}) == "CNY 27,021,597,764,222,973.00"


def test_independent_integer_sum_detects_one_unit_tampering(tmp_path):
    source=tmp_path / "large.csv"
    source.write_text("amount\n9007199254740991\n9007199254740991\n9007199254740991\n",encoding="utf-8")
    out=tmp_path / "result"
    analyze(source,out,metric="amount",charts=False)
    bundle=out / "result.json"
    assert validate_bundle(bundle)["checks_status"] == "passed"
    data=json.loads(bundle.read_text(encoding="utf-8"))
    data["metric"]["value"] += 1
    bundle.write_text(json.dumps(data),encoding="utf-8")
    assert validate_bundle(bundle)["status"] == "failed"
