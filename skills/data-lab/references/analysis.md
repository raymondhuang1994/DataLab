# Analysis workflow

Adapted from Anthropic's analyze/explore-data workflows; DataLab adds executable helpers and result bundles.

## Scope the question

Identify the decision, metric, unit of observation, period, comparison and relevant dimensions. If the user asks why a metric changed, break it into observable components before offering explanations. A contribution decomposition explains arithmetic change; it is not evidence of causality.

Read and profile the actual data. Check missingness, duplicate rows/keys, cardinality, distributions, unusual encoding, date coverage and freshness. Keep suspicious records until their meaning is understood. Inspect multiple-sheet Excel metadata before selecting a table. Formula values are cached values unless a spreadsheet calculation tool recomputes them.

Use `analyze` to generate a starting bundle. It does not infer business metrics. Prefer a supplied project's registered metric (`--project ... --metric-id ...`), so reports and dashboards share its formula, filters and display unit. Supported aggregations are `sum`, `mean`, `median`, `weighted_mean`, `ratio`, `count_rows`, `count_nonmissing` and `count_distinct`. Legacy `count` maps to nonmissing metric entries when a metric column is present, otherwise rows. Rates usually need summed numerators and denominators, not the mean of row-level percentages.

## Go beyond a profile

- Reconcile totals with segment subtotals when additive.
- Compare like periods, including partial-month and timezone checks.
- Inspect whether an overall movement persists across relevant segments.
- Check cohort eligibility and whether future observation windows are complete.
- For funnels, verify event ordering and unique entity definitions, rather than merely counting each event's presence.
- For joins, assert the intended relationship and inspect unmatched keys/row multiplication.
- For correlations, disclose sample sizes, missing-value treatment and potential confounders.

Create task-specific SQL/Python when needed. Save it beside the results and describe assumptions. The CLI is a reliable starting tool, not a replacement for answering the user's question.

## Narrative

An evidence-backed finding records a claim, the supporting table/metric, scope and caveat. Update `summary`, `findings`, `methodology` and `limitations` in `result.json` when authoring a report. Keep calculated values and source fingerprints intact unless the analysis is actually rerun. Schema 1.1 adds structured `claims`: preserve existing claim texts or update their templates and evidence together; deleting/replacing a linked statement without updating its claim fails validation. Add numeric claims for material added conclusions. See [bundle contract](runtime.md). Re-run validation, then re-render HTML/Word after changes. `checks_status=passed` still leaves overall `status=needs_review`; independently review free narrative and causal language.

Do not force a fixed number of findings. Say when the available data cannot support a requested explanation, prediction or causal claim.
