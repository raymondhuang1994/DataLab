#!/usr/bin/env python3
"""Check shipping metadata, local skill references, Python syntax and bundled assets."""
import ast
import json
from pathlib import Path
import re
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    errors = []
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    compatibility = json.loads((root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest["name"] != "data-lab" or compatibility["name"] != manifest["name"] or compatibility["version"] != manifest["version"]:
        errors.append("Plugin identifiers/versions are inconsistent")
    skill = root / "skills/data-lab"
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\nname: data-lab\n") or "description:" not in text.split("---", 2)[1]:
        errors.append("Invalid skill frontmatter")
    for path in skill.rglob("*.md"):
        body = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^\s)]+)\)", body):
            if "://" not in target and not target.startswith("#"):
                target_path = (path.parent / target.split("#")[0]).resolve()
                if not target_path.exists():
                    errors.append(f"Missing reference: {path.relative_to(root)} -> {target}")
    for path in root.rglob("*.py"):
        if any(part in {".venv", "__pycache__", "build", "work"} for part in path.relative_to(root).parts):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(str(exc))
    for path in ["LICENSE", "NOTICE", "skills/data-lab/scripts/requirements.txt", "skills/data-lab/scripts/datalab/assets/dashboard.html", "skills/data-lab/scripts/datalab/assets/dashboard_metrics.js", "skills/data-lab/scripts/datalab/metrics.py", "skills/data-lab/scripts/datalab/settings.py", "skills/data-lab/scripts/datalab/safety.py", "skills/data-lab/references/safety.md", "skills/data-lab/references/settings.md", "skills/data-lab/agents/openai.yaml"]:
        if not (root / path).is_file():
            errors.append("Missing package file: " + path)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Package metadata, relative references, Python syntax and dashboard asset: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
