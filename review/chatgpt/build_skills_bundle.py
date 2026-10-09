#!/usr/bin/env python3
"""Bundle the skill files (plus format, permission catalog and tools) into one markdown file for an
outside reviewer. Agency standards only: the bundle contains no client data.
Usage: python3 review/chatgpt/build_skills_bundle.py <out.md>"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from wavyos.policy import ACTIONS  # noqa: E402
from wavyos.execution.tools import TOOLS  # noqa: E402
from wavyos.taxonomy import AREAS  # noqa: E402

parts = ["# Wavy OS agent skills: review bundle\n", (ROOT / "skills/FORMAT.md").read_text(),
         "\n# Permission catalog\n\n| Action | Level | Approvers | Locked |\n|---|---|---|---|\n" +
         "\n".join(f"| {a.key} | {a.level} | {', '.join(sorted(a.approvers)) or '-'} | {'yes' if a.floor == 'explicit_approval' else ''} |"
                   for a in ACTIONS.values()),
         "\n\n# Agent tools\n\n" + "\n".join(f"- `{t.name}` ({', '.join(t.purposes)}): {t.description}" for t in TOOLS.values()),
         "\n\n# Operating areas\n\n" + "\n".join(f"- `{k}`: {v[1]}" for k, v in AREAS.items())]
for sub in ("roles", "areas"):
    for p in sorted((ROOT / "skills" / sub).glob("*.md")):
        parts.append(f"\n\n<!-- ===================== FILE: {sub}/{p.name} ===================== -->\n\n{p.read_text()}")
out = Path(sys.argv[1])
out.write_text("\n".join(parts))
print(f"{out}: {out.stat().st_size // 1024} KB, {len(out.read_text().split())} words")
