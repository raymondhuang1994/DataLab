# SQL and connected data

Adapted from upstream write-query/sql-queries. Discover tools actually available in the current host. The plugin ships no credentials, remote database account or automatic warehouse connection.

Confirm engine/dialect, table grain, keys, dimensions, definitions and filters. Inspect schema using authorized connected tools, or use schema supplied by the user. Write a query for manual execution if no connection exists and label it unexecuted.

Prefer explicit columns, documented CTEs, parameterized values, partition filters and bounded schema exploration. Quote identifiers using the actual engine's convention. Guard zero denominators. Check multi-to-many joins and count distinct entities where the measure requires it. Do not silently substitute SQL dialects.

DataLab analysis uses read operations. Use a connection or account with read-only database permissions when available; local output guards cannot restrict a remote database. Do not issue data mutation, schema changes, write-back, index creation, vacuum or warehouse configuration changes as part of analysis. A SELECT statement may call a function with side effects, so query text alone is not a substitute for database permissions. A requested operational change belongs to a separately authorized workflow.

Export queried results to a new file in the task workspace for bundled analysis/model/dashboard commands, preserving the SQL, connection/source identifier, execution time and filters. Keep the export in an input subdirectory and generated reports in a separate output subdirectory. Save revised queries and extracts under new names; never replace existing files or write into an original data directory. See [Read-only inputs and safe outputs](safety.md).

A refreshing file dashboard only sees changes when its file is updated by a separate process. DataLab's server reads that file; it does not refresh a warehouse extract itself. If the user needs live warehouse-backed data, name the separate authorized refresh process and its output ownership; do not modify the original source or claim an exported file maintains a live connection.

For repeated business analysis, store trusted definitions and common joins using [Business context](context.md).
