# Business context

Adapted from upstream data-context-extractor. Build reusable context when the task actually calls for it; do not permanently store every exploratory observation.

Record in a user-chosen project file:

- Terms and metric definitions, including units, numerator/denominator and weighting.
- Grain, keys, table relationships and allowed aggregation levels.
- Source names/versions and evidence supporting definitions.
- Excluded populations, valid statuses and incomplete observation windows.
- Business timezone/calendar, owner and last reviewed date.
- Known quality issues and common query pitfalls.

Keep observed facts, user-confirmed definitions and unverified hypotheses separate. Never store credentials or access tokens in these files. Re-read supplied context when it may have changed. A local context file does not synchronize automatically across devices.
