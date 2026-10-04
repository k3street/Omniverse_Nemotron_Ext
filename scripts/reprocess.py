#!/usr/bin/env python3
"""Re-run the stages a fix made stale, on every asset it touches.

Reads each queue entry's processing ledger (scripts/processing.py) and, for
the stages asked for, re-runs those that have not run under the current
rules - in order, with the pipeline's own functions (process_downloads.py),
so a reprocessed asset is what a fresh download would become.

    reprocess.py --dry-run                       # what is stale, by stage
    reprocess.py                                 # articulate, behaviors, verify, critic
    reprocess.py --stages classify,file          # opt in: a VLM call per asset
    reprocess.py --assets drill_1,wheelchair_01  # just these
    reprocess.py --classes pliers,scissors --limit 20

Stages: ingest, classify, file, articulate, behaviors, verify, critic, soft.
ingest rebuilds the derivative (what follows it re-runs too) and classify
calls the VLM: both are opt-in. verify and soft need the Isaac slot / the
Newton venv and run one asset at a time.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
QUEUE = REPO / "workspace" / "review_queue"
DEFAULT = ["articulate", "behaviors", "verify", "critic"]


def entries(only: list[str] | None, classes: list[str] | None):
    for qf in sorted(QUEUE.glob("*.json")):
        if qf.name.startswith("_"):
            continue
        try:
            e = json.loads(qf.read_text())
        except (OSError, ValueError):
            continue
        if not e.get("asset_id"):
            continue
        if only and e.get("asset_id") not in only:
            continue
        cls = e.get("class_hint") or (e.get("report") or {}).get("matched_class")
        if classes and cls not in classes:
            continue
        yield e


def run_stage(stage: str, a: str, library_root: Path) -> str:
    import process_downloads as pd
    from asset_review_hub import save_queue_entry, unarticulate

    if stage == "ingest":
        from ingest_asset import queue_file
        e = pd.entry_of(a)
        src = e.get("original_file") or e["file"]
        e = queue_file(src, e.get("class_hint"), a)
        return e["report"].get("verdict", "")
    if stage == "classify":
        from vlm_classify import classify_entry
        return classify_entry(a)
    if stage == "file":
        log = pd.file_assets([a], library_root, dry=False)
        return "; ".join(log) or "already in its folder"
    if stage == "articulate":
        from processing import articulated
        e = pd.entry_of(a)
        if articulated(e):
            unarticulate(e, "reprocess: the drafting rules changed")
        return pd.articulate(a, dry=False)
    if stage == "behaviors":
        from behaviors import check
        from processing import record
        e = pd.entry_of(a)
        bc = check(e)
        e["behavior_check"] = bc
        record(e, "behaviors", ok=all(v.get("ok") for v in bc.values()))
        save_queue_entry(e)
        return "all met" if all(v.get("ok") for v in bc.values()) else json.dumps(
            {k: v["missing"] for k, v in bc.items() if not v.get("ok")})
    if stage == "verify":
        return pd.animate(a)
    if stage == "critic":
        from motion_critic import critique
        r = critique(a)
        return "PASS" if r["pass"] else "FAIL: " + ", ".join(k for k, v in r["joints"].items() if not v.get("motion_ok"))
    if stage == "soft":
        return pd.soft_test(a)
    raise ValueError(f"unknown stage {stage!r}")


def main() -> int:
    from processing import ORDER, stale

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stages", default=",".join(DEFAULT), help=f"comma list from {ORDER}")
    ap.add_argument("--assets", help="comma list of asset ids")
    ap.add_argument("--classes", help="comma list of classes")
    ap.add_argument("--limit", type=int, default=0, help="at most this many assets")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--library", default=str(Path.home() / "Desktop/assets/SketchFab_Assets"))
    args = ap.parse_args()
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    bad = [s for s in stages if s not in ORDER]
    if bad:
        raise SystemExit(f"unknown stage(s) {bad}; known: {ORDER}")
    todo = []
    for e in entries(args.assets.split(",") if args.assets else None,
                     args.classes.split(",") if args.classes else None):
        st = stale(e, stages)
        if st:
            todo.append((e["asset_id"], st))
    if args.limit:
        todo = todo[: args.limit]
    by_stage = Counter(s for _, st in todo for s, _ in st)
    print(f"{len(todo)} asset(s) with stale stages: " + ", ".join(f"{s} {by_stage[s]}" for s in ORDER if by_stage[s]))
    report = {"started": datetime.now().isoformat(timespec="seconds"), "stages": stages, "dry_run": args.dry_run,
              "assets": {}}
    for a, st in todo:
        print(f"{a}: " + "; ".join(f"{s} ({why})" for s, why in st))
        if args.dry_run:
            report["assets"][a] = {s: why for s, why in st}
            continue
        import process_downloads as pd
        done = {}
        for s in ORDER:
            # re-read: a stage re-run changes what follows (an asset articulated
            # now has a verify and a critic to run that it had not before)
            now = dict(stale(pd.entry_of(a), stages))
            if s not in now or s in done:
                continue
            try:
                done[s] = run_stage(s, a, Path(args.library).expanduser())[:300]
            except Exception as ex:  # noqa: BLE001 - one asset's failure is a finding
                done[s] = f"FAILED: {type(ex).__name__}: {str(ex)[:200]}"
            print(f"   {s}: {done[s][:160]}")
        report["assets"][a] = done
    out = QUEUE / "_runs" / f"reprocess_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(f"report: {out}")
    print("REPORT " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
