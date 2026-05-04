"""Mark templates as verified in ChromaDB based on canary judgments.

Reads N judged campaign-jsonl files. A task counts as verified if it is
triple-perfect (real_success=True, scene_matched_criterion=True, fab=0)
across ALL provided runs. Writes verified=true, verified_date, and
verified_metrics into the template's metadata.

Single-process. Do not run while another writer (post-commit indexer,
chat session) is touching the same ChromaDB collection.

Usage:
    python -m scripts.qa.mark_verified \\
        --runs workspace/qa_runs/campaign_X_groundtruth.jsonl \\
               workspace/qa_runs/campaign_Y_groundtruth.jsonl \\
               workspace/qa_runs/campaign_Z_groundtruth.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import chromadb

COLLECTION = "isaac_assist_templates"
INDEX_PATH = "workspace/tool_index"


def load_judged(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.open():
        d = json.loads(line)
        v = d.get("verdict", {}) or {}
        out[d["task"]] = {
            "real": bool(v.get("real_success")),
            "scene": bool(v.get("scene_matched_criterion")),
            "fab": int(v.get("fabricated_count", 0) or 0),
            "turns": int(d.get("turns", 0) or 0),
        }
    return out


def find_perfect(runs: list[dict[str, dict]]) -> list[str]:
    if not runs:
        return []
    candidates = set(runs[0].keys())
    for r in runs[1:]:
        candidates &= set(r.keys())
    perfect = []
    for tid in sorted(candidates):
        if all(r[tid]["real"] and r[tid]["scene"] and r[tid]["fab"] == 0 for r in runs):
            perfect.append(tid)
    return perfect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True, help="judged campaign jsonl files")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    runs = [load_judged(Path(p)) for p in args.runs]
    n_runs = len(runs)
    if n_runs < 2:
        print("Refusing to mark verified from <2 runs", file=sys.stderr)
        return 2

    perfect = find_perfect(runs)
    print(f"Triple-perfect across {n_runs} runs: {len(perfect)} task(s)")
    for tid in perfect:
        print(f"  {tid}")
    if not perfect:
        return 0

    client = chromadb.PersistentClient(path=INDEX_PATH)
    coll = client.get_collection(COLLECTION)

    existing = coll.get(ids=perfect, include=["metadatas"])
    have = {tid: meta for tid, meta in zip(existing["ids"], existing["metadatas"])}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updated, missing = [], []
    new_metas = []
    for tid in perfect:
        if tid not in have:
            missing.append(tid)
            continue
        meta = dict(have[tid] or {})
        meta["verified"] = True
        meta["verified_date"] = today
        meta["verified_runs"] = n_runs
        meta["verified_pass_rate"] = 1.0
        new_metas.append(meta)
        updated.append(tid)

    if missing:
        print(f"WARNING: {len(missing)} task(s) not in ChromaDB: {missing}", file=sys.stderr)

    if args.dry_run:
        print(f"[dry-run] would mark {len(updated)} verified")
        return 0

    if updated:
        coll.update(ids=updated, metadatas=new_metas)
        print(f"Marked {len(updated)} task(s) verified={today} runs={n_runs}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
