"""Phase 47b inventory scanner.

Scans `service/isaac_assist_service/chat/tools/handlers/*.py` using the
AST-based `audit_handler_module` heuristic from Phase 47b's module, and
writes:

* `docs/audits/honesty_inventory.md` — grouped markdown report for human
  reviewers (clean / warn / critical bins).
* `docs/audits/honesty_baseline.json` — machine-readable baseline for
  future diffing (find newly-introduced silent-success risks).

Run:

    python scripts/honesty_inventory.py

Exit code 0 always; this is an inventory tool, not a CI gate.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDLER_DIR = ROOT / "service" / "isaac_assist_service" / "chat" / "tools" / "handlers"
INVENTORY_MD = ROOT / "docs" / "audits" / "honesty_inventory.md"
BASELINE_JSON = ROOT / "docs" / "audits" / "honesty_baseline.json"

sys.path.insert(0, str(ROOT))

from service.isaac_assist_service.multimodal.sub_phase_47b_honesty_decorator_long_tail import (  # noqa: E402
    audit_handler_module,
)


_CRITICAL_TAGS = {"no_success_key", "string_return"}
_WARN_TAGS = {"returns_none_literal", "bare_return"}


def _severity_for(tags: list[str]) -> str:
    tag_set = set(tags)
    if tag_set & _CRITICAL_TAGS:
        return "critical"
    if tag_set & _WARN_TAGS:
        return "warn"
    return "info"


def scan_handlers() -> dict:
    """Walk handlers/*.py and run audit_handler_module on each."""
    scanned_at = datetime.now(timezone.utc).isoformat()
    inventory: dict = {
        "scanned_at": scanned_at,
        "handler_dir": str(HANDLER_DIR.relative_to(ROOT)),
        "modules": {},
        "totals": {"critical": 0, "warn": 0, "info": 0, "clean_modules": 0},
    }

    if not HANDLER_DIR.is_dir():
        return inventory

    py_files = sorted(
        p for p in HANDLER_DIR.glob("*.py") if not p.name.startswith("_")
    )
    for module_path in py_files:
        findings = audit_handler_module(module_path)
        clean = not findings
        bucketed: dict = {"critical": [], "warn": [], "info": []}
        for fn_name, tags in findings.items():
            sev = _severity_for(tags)
            bucketed[sev].append({"function": fn_name, "tags": tags})
            inventory["totals"][sev] += 1
        if clean:
            inventory["totals"]["clean_modules"] += 1
        inventory["modules"][module_path.name] = {
            "n_findings": sum(len(v) for v in bucketed.values()),
            "findings": bucketed,
            "clean": clean,
        }
    return inventory


def render_markdown(inv: dict) -> str:
    lines = [
        "# Honesty Inventory — Phase 47b scanner",
        "",
        f"Generated: `{inv['scanned_at']}`",
        f"Handler directory: `{inv['handler_dir']}`",
        "",
        "## Summary",
        "",
        f"- **Critical** findings: {inv['totals']['critical']}",
        f"- **Warn** findings: {inv['totals']['warn']}",
        f"- **Info** findings: {inv['totals']['info']}",
        f"- **Clean modules**: {inv['totals']['clean_modules']} of {len(inv['modules'])}",
        "",
        "Severity definitions:",
        "- **critical**: function has no `success` key in any returned dict, or returns a bare string. Likely silent failure surface.",
        "- **warn**: function returns `None` or has bare `return` paths — caller cannot distinguish from intentional empty result.",
        "- **info**: low-confidence heuristic hit; review recommended.",
        "",
        "## Per-module findings",
        "",
    ]
    for module_name, data in sorted(inv["modules"].items()):
        if data["clean"]:
            continue
        lines.append(f"### `{module_name}` — {data['n_findings']} findings")
        lines.append("")
        for sev in ("critical", "warn", "info"):
            entries = data["findings"][sev]
            if not entries:
                continue
            lines.append(f"**{sev.upper()}** ({len(entries)}):")
            for e in entries:
                tags = ", ".join(e["tags"])
                lines.append(f"- `{e['function']}` — tags: {tags}")
            lines.append("")
    lines.append("## Clean modules")
    lines.append("")
    for module_name, data in sorted(inv["modules"].items()):
        if data["clean"]:
            lines.append(f"- `{module_name}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    inv = scan_handlers()
    INVENTORY_MD.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_MD.write_text(render_markdown(inv), encoding="utf-8")
    BASELINE_JSON.write_text(json.dumps(inv, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {INVENTORY_MD.relative_to(ROOT)}")
    print(f"wrote {BASELINE_JSON.relative_to(ROOT)}")
    print(
        f"totals: critical={inv['totals']['critical']}, "
        f"warn={inv['totals']['warn']}, info={inv['totals']['info']}, "
        f"clean_modules={inv['totals']['clean_modules']}/{len(inv['modules'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
