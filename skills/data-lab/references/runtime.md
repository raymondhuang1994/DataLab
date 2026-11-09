# CLI and result bundles

Use a Python 3.11+ environment with `scripts/requirements.txt` installed. The commands below are relative to the **skill directory**; quote paths and substitute the correct interpreter for the host. `doctor` works before dependencies are installed.

```text
python scripts/run.py doctor
python scripts/run.py config --project project.json --preferences preferences.json --metric-id conversion
python scripts/run.py analyze --input channels.csv --project project.json --preferences preferences.json --metric-id conversion --out run-configured
python scripts/run.py analyze --input sales.csv --metric revenue --group region --date date --agg sum --out run-001
python scripts/run.py analyze --input conversion.csv --metric conversions --denominator visitors --agg ratio --group channel --out run-002
python scripts/run.py analyze --input segments.csv --metric average_spend --weight customers --agg weighted_mean --out run-003
python scripts/run.py compare --input experiment.csv --group variant --metric amount --out comparison.json
python scripts/run.py model --input customers.csv --target churn --task classification --features tenure spend plan --out model-001
python scripts/run.py model --input series.csv --target sales --task forecast --time-column date --forecast-horizon 6 --out forecast-001
python scripts/run.py dashboard --input sales.csv --date date --metric revenue --group region --agg sum --out dashboard.html
python scripts/run.py serve --input sales.csv --date date --metric revenue --group region --agg sum --interval 5 --port 8765
python scripts/run.py validate --bundle run-001/result.json --out run-001/checks.json
python scripts/run.py word --bundle run-001/result.json --out run-001/report.docx
python scripts/run.py report --bundle run-001/result.json --out run-001/report.html
python scripts/run.py rerun --config run-001/config.json --input relocated-sales.csv --out run-004
```

`--sheet` is required for Excel files with more than one sheet. `analyze --key` checks a specified key's uniqueness. `--freq` supports D/W/M/Q/Y, `--timezone` declares the grouping timezone. Missing dates do not enter time summaries; missing periods are not converted to zero. Source data is never overwritten. Use a new output directory for a new analysis/model run.

The model command has its own detailed artifacts and reproduction script; inspect [Modeling](modeling.md). It also writes the general `result.json` bundle and an offline HTML report, so `word --bundle model-001/result.json` can export model metrics, comparisons and diagnostics directly. Review its model limitations and retain the detailed prediction/backtest artifacts as evidence.

## General report bundle (schema 1.1; legacy 1.0 remains readable)

`result.json` is UTF-8 JSON, with these keys:

```json
{
  "schema_version": "1.1",
  "title": "Analysis title",
  "created_at": "ISO-8601 timestamp",
  "source": {"path": "original file", "sha256": "fingerprint", "rows": 100, "columns": ["field"]},
  "profile": {},
  "summary": ["Supported finding"],
  "findings": [{"title": "Finding", "text": "Explanation", "evidence": {"table_id": "metrics"}}],
  "tables": [{"id": "metrics", "title": "Metrics", "columns": ["Metric", "Value"], "rows": [["Count", 100]], "source_file": "tables/metrics.csv"}],
  "charts": [{"title": "Chart", "path": "charts/chart.png", "caption": "Source and scope", "table_id": "metrics"}],
  "methodology": ["Method and definitions"],
  "limitations": ["Material limitation"],
  "validation": {"status": "needs_review", "checks": [{"name": "check", "status": "passed", "detail": "What was checked"}]}
}
```

Schema 1.1 analysis adds `metric_definition` (the normalized formula and filters), `computation` (sample coverage), `preferences`, `quality_rules` and `settings_provenance` (configuration hashes and field origins). The `config.json` snapshot retains resolved settings so `rerun` does not change when an external preference file is edited. If there is no selected metric, `metric_definition` is null and the report is a profile only.

Key numeric statements can be recorded as machine-checkable claims:

```json
{
  "id": "overall_rate",
  "kind": "observation",
  "template": "整体转化率为 {value}。",
  "text": "整体转化率为 6.36%。",
  "value": 0.06363636363636363,
  "format": {"unit": "percent", "precision": 2},
  "evidence": {"metric": "value"}
}
```

Put these objects in `claims`. For a table value, use `evidence: {"table_id":"by_group","row":0,"column":"指标值"}`. `row` is zero-based. The template must contain exactly one `{value}`; escape literal braces in labels as `{{`/`}}`. Generate `text` using `metrics.format_value`, and include that exact text in `summary` or a finding's `text`. The validator verifies evidence, numeric value, formatted text and its presence in the narrative. Free explanations still require semantic review. `tables[].formats` may map a column name to a format specification; JSON/CSV retain numeric values while HTML/Word display percentages or currencies.

Validation returns overall `status: failed | needs_review` and separate `checks_status: passed | warning | failed`, with coverage for sources, calculations, claims, narrative and visuals. It never declares a whole report ready solely from automatic checks. The CLI `analyze` saves independent checks to `checks.json` and into the bundle; direct Python users should explicitly call `validate_bundle` after custom edits.

Asset paths are relative to `result.json` and must stay inside the bundle. JSON numeric values remain numeric. Do not put explanatory text into numeric fields. Tables contain full calculated values; the HTML renderer caps visible rows at 100 with a note. CSVs retain full result rows. Word renders the table rows supplied to it; choose useful detail rather than a million-row appendix.

## Interpreting helper output

`analyze` creates a profile and explicit aggregation, not a domain-specific investigation. The agent should examine the actual question, create further computations as needed, and add evidence-backed narrative before calling a report finished. `validate` returns a bounded automatic check result; human/business/statistical review remains separate. `doctor` checks the execution runtime, not plugin installation/account eligibility.

CSV output preserves raw text for reproducibility. If exporting untrusted text to a spreadsheet application, sanitize formula-like cells in a separate presentation copy; do not change the analytical source silently.
