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

# Wilson helper for honest small-n verification
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _stats  # type: ignore  # noqa: E402

COLLECTION = "isaac_assist_templates"
INDEX_PATH = "workspace/tool_index"


def load_judged(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.open():
        if not line.strip():
            continue
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
    """Triple-perfect: every run on this task is real_success+scene+fab=0.
    Strict criterion suitable for atomic CW-tier tasks."""
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


def find_wilson_passing(
    runs: list[dict[str, dict]], threshold: float = 0.7
) -> list[tuple[str, int, int, float]]:
    """Wilson lower-bound criterion: a task passes if the 95% Wilson lower
    bound on its success rate exceeds `threshold`. More forgiving than
    triple-perfect for stochastic T4-tier dialog tasks where 5/6 honest
    successes is much stronger evidence than 3/3 lucky ones.

    Returns: [(task_id, passes, n, wilson_lower), ...]
    """
    if not runs:
        return []
    candidates = set(runs[0].keys())
    for r in runs[1:]:
        candidates &= set(r.keys())
    out = []
    for tid in sorted(candidates):
        passes = sum(
            1 for r in runs
            if r[tid]["real"] and r[tid]["scene"] and r[tid]["fab"] == 0
        )
        n = len(runs)
        lo = _stats.wilson_lower(passes, n)
        if lo >= threshold:
            out.append((tid, passes, n, lo))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True, help="judged campaign jsonl files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--wilson-threshold", type=float, default=None,
        help="If set (0-1), use Wilson lower-bound ≥ threshold instead of "
             "triple-perfect. Recommended: 0.7 for T4-tier (stochastic) "
             "tasks. Leave unset for strict triple-perfect (default).",
    )
    args = ap.parse_args()

    runs = [load_judged(Path(p)) for p in args.runs]
    n_runs = len(runs)
    if n_runs < 2:
        print("Refusing to mark verified from <2 runs", file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.wilson_threshold is not None:
        thresh = args.wilson_threshold
        if not (0.0 < thresh < 1.0):
            print(f"--wilson-threshold must be in (0,1), got {thresh}", file=sys.stderr)
            return 2
        wilson_passing = find_wilson_passing(runs, threshold=thresh)
        print(f"Wilson lower-bound ≥ {thresh:.2f} across {n_runs} runs: "
              f"{len(wilson_passing)} task(s)")
        for tid, passes, n, lo in wilson_passing:
            print(f"  {tid}  ({passes}/{n}, lower={lo:.2f})")
        if not wilson_passing:
            return 0
        perfect_ids = [t[0] for t in wilson_passing]
        # Per-task passes counts, for metadata
        wilson_meta = {t[0]: (t[1], t[2], t[3]) for t in wilson_passing}
    else:
        perfect_ids = find_perfect(runs)
        print(f"Triple-perfect across {n_runs} runs: {len(perfect_ids)} task(s)")
        for tid in perfect_ids:
            print(f"  {tid}")
        if not perfect_ids:
            return 0
        wilson_meta = None

    client = chromadb.PersistentClient(path=INDEX_PATH)
    coll = client.get_collection(COLLECTION)

    existing = coll.get(ids=perfect_ids, include=["metadatas"])
    have = {tid: meta for tid, meta in zip(existing["ids"], existing["metadatas"])}

    updated, missing = [], []
    new_metas = []
    for tid in perfect_ids:
        if tid not in have:
            missing.append(tid)
            continue
        meta = dict(have[tid] or {})
        meta["verified"] = True
        meta["verified_date"] = today
        meta["verified_runs"] = n_runs
        if wilson_meta is not None:
            passes, n, lo = wilson_meta[tid]
            meta["verified_passes"] = passes
            meta["verified_pass_rate"] = passes / n if n > 0 else 0.0
            meta["verified_wilson_lower"] = round(lo, 4)
            meta["verified_method"] = f"wilson>={args.wilson_threshold:.2f}"
        else:
            meta["verified_pass_rate"] = 1.0
            meta["verified_method"] = "triple_perfect"
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
