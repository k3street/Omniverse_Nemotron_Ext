"""inject_diagnose_args.py — bulk-update CP-*.json templates with diagnose_args.

Recovery script for the overnight chain step 4 NO_DIAGNOSE_ARGS issue.

For each CP-*.json template that lacks diagnose_args:
  1. Run _suggest_for_template() (regex parsing of the code field)
  2. If suggestion has at least robot_path → inject as diagnose_args
  3. Save back to the template file

Idempotent: skips templates that already have diagnose_args.

Usage:
  python scripts/qa/inject_diagnose_args.py
  python scripts/qa/inject_diagnose_args.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts/qa"))

from suggest_diagnose_args import _suggest_for_template


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing diagnose_args (default: skip)")
    args = p.parse_args()

    templates_dir = REPO_ROOT / "workspace/templates"
    cp_files = sorted(templates_dir.glob("CP-*.json"))

    n_updated = 0
    n_skipped = 0
    n_no_suggestion = 0

    for f in cp_files:
        try:
            template = json.loads(f.read_text())
        except Exception as e:
            print(f"[SKIP] {f.name}: parse error {e}", flush=True)
            n_skipped += 1
            continue

        if template.get("diagnose_args") and not args.force:
            n_skipped += 1
            continue

        suggestion = _suggest_for_template(template)
        clean = {k: v for k, v in suggestion.items() if not k.startswith("_")}

        # Validate: need at least robot_path or cycles
        is_multi = "cycles" in clean
        has_robot = bool(clean.get("robot_path") or is_multi)
        if not has_robot:
            print(f"[NO-SUGG] {f.name}: no robot_path / cycles found in code", flush=True)
            n_no_suggestion += 1
            continue

        # Filter out None values
        clean = {k: v for k, v in clean.items() if v is not None}
        if is_multi:
            # Filter cycle entries: drop None fields
            clean["cycles"] = [
                {k: v for k, v in cyc.items() if v is not None}
                for cyc in clean.get("cycles", [])
            ]

        if args.dry_run:
            print(f"[DRY] {f.name}: would inject diagnose_args = {clean}", flush=True)
            continue

        template["diagnose_args"] = clean
        f.write_text(json.dumps(template, indent=2))
        n_updated += 1
        kind = suggestion.get("_suggestion_kind", "?")
        print(f"[OK]  {f.name}: injected ({kind})", flush=True)

    print("-" * 70)
    print(f"summary: updated={n_updated} skipped={n_skipped} no_suggestion={n_no_suggestion} "
          f"total={len(cp_files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
