# Validate analysis and artifacts

Adapted from upstream validate-data. A programmatic pass is evidence about the checks it ran, not certification of the full analysis.

## Data and calculations

Verify source version, time range, grain, population and metric definitions. Check important nulls, duplicate entities, join cardinality, unmatched records, denominator consistency, weighting and partial periods. Do not treat all negative values or percentages outside 0–100 as errors without knowing the metric.

Independently recompute key totals or trace sample records. Reconcile additive segments; explain legitimate overlap. In v0.2, `validate` accepts result schema 1.0/1.1 and checks the required title, source metadata and actual content; an empty version marker is invalid. It checks JSON/CSV row widths and values, referenced image/table paths, source SHA-256, source shape and stability while reading.

When the original source is available, the validator independently implements the declared filters and recomputes `sum`, `mean`, `median`, `ratio`, `weighted_mean`, `count_rows`, `count_nonmissing` and `count_distinct`. Legacy `count` is interpreted according to whether a column was supplied. Ratios and weighted means use jointly valid pairs; undefined denominators remain null. Declared `by_group` and `by_period` results, record counts, timezone grouping and missing periods are checked against the source, even if JSON and CSV were altered together. A profile-only report has no business metric to recheck.

Source quality rules (`min_rows`, `required_columns`, nonnull composite `unique_key`, and per-column `max_missing_fraction`) are independently evaluated on the **whole source before metric filters**. A producer-supplied `quality.status` is not evidence that the source passed.

Custom analytical tables, model metrics, business assumptions and data authenticity beyond the recorded source remain outside the independent calculation checks. Read `coverage.calculations.not_checked`; file agreement alone is not independent arithmetic verification.

Schema 1.1 analyses retain standard `by_group`/`by_period` tables when those results exist; presentation-specific tables may be added without replacing the machine-checkable evidence. A legacy 1.0 report that retained only custom group/trend tables receives a warning and partial calculation coverage rather than a false full pass.

## Structured numerical claims

For schema 1.1, register important numerical observations in `claims`, and place each exact rendered claim text in `summary` or a finding's `text`:

```json
{
  "id": "overall_conversion",
  "kind": "observation",
  "template": "整体转化率为{value}。",
  "text": "整体转化率为7.42%。",
  "value": 0.07416666666666667,
  "format": {"unit": "percent", "precision": 2},
  "evidence": {"metric": "value"}
}
```

Alternatively reference a numeric table cell with `{"table_id":"by_group","row":0,"column":"指标值"}`. Row indices start at zero. The validator checks unique IDs, finite numeric/null values, the evidence reference, the shared number/percent/currency formatter, and that the resulting text remains in the report. Templates allow exactly one `{value}` field and no field access, conversions or format expressions. Escape literal braces in data labels. Missing values use the configured missing label, not zero.

A claim's text matching its evidence does not independently validate an arbitrary evidence table. It also does not prove causality, justify recommendations or check all free narrative. Those limits remain explicit even when every claim passes.

## Read the result correctly

`status` is **`failed` or `needs_review`**, never automatic `ready`. `checks_status` is `passed`, `warning` or `failed` for the checks actually executed. A successfully checked report therefore normally returns `status="needs_review", checks_status="passed"`.

`coverage` separately reports `source`, `calculations`, `claims`, `narrative` and `visual`. Narrative and visual review remain `not_checked` by this CLI. Unavailable sources yield a warning; intentionally disabling source checks leaves source/calculation coverage explicitly unchecked. Retain that distinction when reporting validation to the user.

## Statistical and narrative checks

Check leakage, training-only preprocessing, untouched holdout, suitable split unit, baseline, class imbalance, dependence, uncertainty and multiple testing as applicable. Be alert to survivorship/selection bias, Simpson's paradox and outcome-defined segments.

For each conclusion, find the exact supporting result. Separate observation from explanation. Do not strengthen language beyond the design's evidence. If source data is unavailable, state the reduced verification coverage.

## Presentation checks

Charts: labels, units, baseline, period alignment, values and missingness. Dashboard: filters update all relevant views, counts reflect displayed/aggregated data, errors show stale state, source timestamps are clear. Word: inspect every rendered page for Chinese glyphs, table splits, image proportions, overflow and blank pages.

After actual independent review, the assistant may give a scope-limited assessment such as ready with stated limitations or needs revision. Record who/what reviewed which artifacts, retain file fingerprints and name remaining issues. Do not promote the CLI's `checks_status` into whole-report approval or inherit an old report's visual review after editing it.
