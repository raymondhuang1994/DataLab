# SQL and connected data

Adapted from upstream write-query/sql-queries. Discover tools actually available in the current host. The plugin ships no credentials, remote database account or automatic warehouse connection.

Confirm engine/dialect, table grain, keys, dimensions, definitions and filters. Inspect schema using authorized connected tools, or use schema supplied by the user. Write a query for manual execution if no connection exists and label it unexecuted.

Prefer explicit columns, documented CTEs, parameterized values, partition filters and bounded schema exploration. Quote identifiers using the actual engine's convention. Guard zero denominators. Check multi-to-many joins and count distinct entities where the measure requires it. Do not silently substitute SQL dialects.

Routine analysis uses read operations. Data mutation, schema changes and expensive/full-table operational changes require the user's corresponding intent. A SQL optimization suggestion is not permission to create indexes, vacuum tables, write results back or alter warehouse settings.

Export queried results to a local file for bundled analysis/model/dashboard commands, preserving the SQL, connection/source identifier, execution time and filters. A refreshing file dashboard only sees changes when that file is updated. Configure a separate authorized refresh process if the user needs live warehouse-backed data; do not claim an exported file maintains a live connection.

For repeated business analysis, store trusted definitions and common joins using [Business context](context.md).
