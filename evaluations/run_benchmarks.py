"""Run task fixtures with independent expected answers; no claim of agent autonomy."""
import argparse
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/data-lab/scripts"))
from datalab.analysis import analyze
from datalab.common import write_json
from datalab.validation import validate_bundle


def equal(actual, expected):
    if expected is None:
        return actual is None
    return isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10)


def run_case(case, directory):
    directory.mkdir()
    source = directory / "source.json"
    write_json(source, case["data"])
    out = directory / "result"
    if case["kind"] == "empty_bundle":
        bundle = directory / "empty.json"
        write_json(bundle, {"schema_version":"1.1"})
        checks = validate_bundle(bundle)
        assert checks["status"] == "failed", checks
        return {"checks_status":checks["checks_status"]}
    try:
        result = analyze(source, out, metric_definition=case.get("metric"), charts=False,
                         quality_rules=case.get("quality", {}), **case.get("analysis", {}))
    except ValueError as error:
        if case.get("expected_error"):
            return {"rejected":True,"reason":str(error)}
        raise
    assert not case.get("expected_error"), "Task should have rejected invalid inputs"
    if "expected" in case:
        assert equal(result["metric"]["value"],case["expected"]), (result["metric"]["value"],case["expected"])
    for table_id, expected_rows in case.get("expected_tables", {}).items():
        table = next(t for t in result["tables"] if t["id"] == table_id)
        assert len(table["rows"]) == len(expected_rows), table
        for actual_row, expected_row in zip(table["rows"],expected_rows):
            assert actual_row[0] == expected_row[0]
            assert equal(actual_row[1],expected_row[1])
            assert actual_row[2] == expected_row[2]
    kind = case["kind"]
    if kind == "tamper_metric":
        result["metric"]["value"] = .99
    if kind == "tamper_claim":
        result["summary"] = ["整体转化率为99%，已证明营销活动有效。"]
    if kind == "missing_source":
        source.unlink()
    write_json(out / "result.json",result)
    checks = validate_bundle(out / "result.json")
    write_json(directory / "checks.json",checks)
    if kind in ("tamper_metric","tamper_claim"):
        assert checks["status"] == "failed", checks
    elif kind == "missing_source":
        assert checks["checks_status"] == "warning" and checks["status"] == "needs_review", checks
    else:
        assert checks["checks_status"] == "passed", checks
        assert checks["status"] == "needs_review", "Automatic checks must not certify the entire analysis"
    return {"value":result["metric"]["value"],"checks_status":checks["checks_status"],"status":checks["status"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out",required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        parser.error("Use a new output directory to retain prior benchmark results")
    out.mkdir(parents=True)
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    started=time.monotonic(); results=[]
    for case in cases:
        try:
            observed=run_case(case,out / case["id"])
            results.append({"id":case["id"],"task":case["task"],"passed":True,"observed":observed})
        except Exception as exc:
            results.append({"id":case["id"],"task":case["task"],"passed":False,"error":f"{type(exc).__name__}: {exc}"})
    summary={"kind":"deterministic_business_task_benchmarks","synthetic_data":True,
             "passed":sum(r["passed"] for r in results),"total":len(results),"seconds":round(time.monotonic()-started,3),
             "scope":"Checks computations and validation against known answers; does not score autonomous reasoning, narrative or visual quality.","results":results}
    write_json(out / "results.json",summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='results'},ensure_ascii=False,indent=2))
    for failure in [r for r in results if not r["passed"]]:
        print(failure["id"],failure["error"],file=sys.stderr)
    return 0 if summary["passed"]==len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
