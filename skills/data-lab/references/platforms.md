# Host and OS compatibility

DataLab is a portable skill/plugin with optional local Python execution helpers. One package does not imply identical host capabilities.

## Capabilities to detect

1. Can the host read the requested uploaded/local/connected data?
2. Is a suitable Python environment with dependencies available?
3. Can generated images, HTML and DOCX be returned as files or previewed?
4. Is a long-running loopback server accessible from the user's browser?
5. Are authorized database tools available when needed?
6. Does the host actually enforce read-only inputs, or only offer normal writable filesystem access?

Use existing host tools where they fit. In a shell-capable host, resolve `scripts/run.py` from this skill, not the process working directory. Quote every path containing spaces. Use Python APIs rather than `open`, `start`, `rm`, PowerShell-only or Unix-only commands in reusable workflows.

## Windows and macOS

- Python >=3.11. Prefer Python 3.12 for the documented baseline.
- A venv executable is `Scripts/python.exe` on Windows and `bin/python` on macOS.
- File encodings are explicit UTF-8; CSV exports include a BOM for common Excel workflows.
- Preserve a portable Python reproduction entry point for custom analysis. Use `sys.executable`, `pathlib` and subprocess argument lists; a shell-only `.sh` wrapper does not reproduce the workflow on Windows. Supply the data and resolved configuration paths as explicit arguments.
- No requirement for Microsoft Word to generate DOCX. Optional LibreOffice supports rendering; it may use different fonts and page breaks.
- Dashboard servers bind to loopback and run only while the process remains active. Do not install background services or OS schedulers as part of ordinary dashboard creation.
- Choose a new task output directory outside the original data directories on both systems. Preserve existing files and environments; missing dependencies belong in a new venv. Do not change Windows ACLs, macOS permissions, file attributes or system font configuration as part of analysis.

## Write protection is a separate capability

The bundled `software_guards` check writes made through the DataLab helpers. They are not an operating-system sandbox and cannot restrict arbitrary agent-written scripts, subprocesses, database clients, third-party caches or other tools. A working copy keeps normal transformations away from originals, but does not remove the process's ability to access the original path.

For stronger prevention, the host must expose original inputs read-only to the entire execution process and its children, and provide separate writable task/temp/cache locations. Use an already supported read-only mount, sandbox or isolated execution environment; do not silently install isolation software or alter original permissions. Windows and macOS expose different facilities, so inspect the actual host instead of claiming a portable Skill creates this isolation automatically. Test a write attempt in a disposable fixture before describing the environment as enforced read-only.

Source fingerprints detect content changes; they do not prevent writes, attribute changes to a process, protect every file on the computer, or certify a host sandbox. See [Read-only inputs and safe outputs](safety.md) for the task-level contract.

## ChatGPT/Codex boundaries

Use the supported plugin installation route for the actual client. Local skill folders can be used by compatible Codex hosts. Browser-only ChatGPT does not automatically read a local plugin folder or connect to localhost. In hosts without Python execution, use an authorized execution/MCP service or return code clearly marked unexecuted. Avoid silent fallback to invented numbers.

Return static images/files when HTML cannot run inline. ChatGPT-specific inline UI is optional; the shipped HTML dashboard works in a standard browser. No Claude Artifacts API is assumed. No cross-device synchronization is included.

See the repository installation guide for package/manual installation. Test actual hosts separately; structural packaging validity is not proof of account eligibility or catalog installation.
