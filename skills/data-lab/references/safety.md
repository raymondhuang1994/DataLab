# Read-only inputs and safe outputs

DataLab treats original data and every file already present before a task as read-only. This includes configurations, context, previous reports, source code and existing environments. Do not edit, replace, rename, delete or change permissions on them. Create derived data, revised context and revised reports in a new task workspace; retain originals.

## Output layout

Keep original data and results in separate directory trees, for example:

```text
project/
  inputs/sales.csv
  config/project.json
  config/preferences.json
  results/run-001/          # Created by analyze, not prepared in advance
  results/dashboard-001.html
  previews/word-001/        # Created by render_word
```

The helpers protect the resolved parent directory of each existing raw-data input, including its descendants. A new filename inside that directory is still forbidden. If the source is `project/sales.csv`, outputs must be outside `project/`; do not move the original to work around protection. Use a separate sibling workspace instead. Input symlinks resolve to their actual source location. A missing declared source is recorded as unavailable; its unknown contents cannot be verified.

| Operation | Required destination |
|---|---|
| `analyze`, `model`, `rerun` | A directory that does not exist, outside raw-data directories; even an existing empty directory is refused |
| `report`, `word`, `dashboard`, `compare`, `validate --out` | A new file outside raw-data directories; an existing results directory may receive a new filename |
| `render_word.py` | A new directory outside the input DOCX's parent directory; conversion uses a task-owned copy |
| `bootstrap.py --venv` | A new environment directory; never update an existing environment |
| `tools/package.py --out` | A new directory outside the package source tree |
| `tools/install_skill.py --dest` | A parent under which `data-lab` does not already exist; preserve previous installations |

Do not rerun an export over its previous filename. Choose `report-v2.docx`, `checks-review-001.json` or another new destination. For narrative changes to an existing bundle, create a new complete working copy, preserve relative table/chart references, make revisions there and rerun affected checks. Commands must not modify the old bundle as a shortcut. There is no overwrite/force option.

## What the bundled guards enforce

`datalab.safety` scopes managed writes to one output root. New files use exclusive creation. Existing files cannot become writable just because their paths are inside that root. Files created by the current in-memory session can be updated while their identity and content still match the session's record; this allows `analyze` to attach checks to its own newly generated bundle. A later command does not inherit that ownership.

Managed paths are checked for escape from the output root, collisions with registered inputs, links/directory junctions and changes to the output root or a session-owned file. These checks reduce accidental overwrite and common path mistakes. They are not a guarantee against every filesystem race or hostile process. Do not bypass a refused write with plain `open`, `os.replace`, shell redirection or another tool.

Finite output sessions write `audit/file-safety-<run-id>.json` under the output root. A single-file export uses the destination's parent as that root. The record contains:

- `protection: "software_guards"`, `os_read_only_enforced: false` and explicit limitations.
- `status: "completed" | "failed"`, with any recorded execution error.
- `inputs`: declared paths, resolved paths, input role, before/after fingerprints and `unchanged`, `unavailable` or `changed` status.
- `outputs`: registered new artifacts, their SHA-256 and size. This is not an inventory of every file touched by third-party code.
- `external_writers`: recorded external writers and their intended scope, with `os_enforced: false`.

Audit records cover registered inputs and managed/adopted outputs; they do not inventory the computer. They also contain local paths, so review them before external sharing. An early refusal before a session starts, or a destination that becomes unwritable, may leave no audit. If an input changes, the session fails and reports the change; it never tries to restore or write back the original. Fingerprints detect changes, not who caused them.

`config` and `validate` without `--out` print read-only results and do not create an output audit. `doctor` uses a fresh temporary cache and reports capabilities, not a file-safety certificate. `serve` has no persistent session audit: each load checks file stability, and later changes by the upstream producer are expected. It has no source-writing endpoint.

## Additional Python and SQL

An extra agent-written script does not automatically inherit the guards. Prefer task-owned working copies for transformations, declare all inputs, and keep generated code, outputs and caches in the new workspace. A working copy is an operational precaution, not a restriction on the script's access to the original path.

Code intentionally using the managed API can scope its own writes, for example after preparing `inputs/sales.csv` and choosing a nonexistent `results/custom-001`:

```python
from pathlib import Path
import pandas as pd
from datalab.safety import output_session, safe_open

source = Path("inputs/sales.csv")
output = Path("results/custom-001")
with output_session(output, raw_inputs=[source], directory=True):
    frame = pd.read_csv(source)
    totals = frame.groupby("region", dropna=False)["revenue"].sum()
    with safe_open(output / "region-totals.csv", encoding="utf-8-sig") as stream:
        totals.to_csv(stream)
```

Register configuration/context paths through `inputs=[...]` too. Use `safe_write_text`, `safe_write_bytes` or `safe_open` for managed writes. Ordinary library calls that accept a filesystem filename may bypass these functions; an `output_session` is not a process sandbox. Review the calculation and its assumptions separately from file protection.

For SQL use authorized read-only connections. Local file guards cannot prevent SQL writes, side-effecting functions or remote configuration changes. Save extracts and revised queries as new files; do not write analysis results back to the source database.

## When stronger isolation is required

The host must actually make original inputs read-only to the execution process and its children, while allowing writes only to a separate task/temp/cache area. Use an available host sandbox, read-only mount or equivalent isolated execution arrangement. DataLab does not create a portable Windows/macOS OS sandbox, configure database accounts, or silently change original permissions/ACLs.

The Python entrypoints disable bytecode writes; controlled plotting caches and rendering work use new task locations. External libraries and subprocesses still retain the host permissions they were given. A read-only file attribute alone is not a universal safeguard against replacing a file through a writable parent directory. Do not claim enforced isolation unless the actual host has been configured and checked. Describe unavailable isolation plainly and remain within the permitted execution scope.
