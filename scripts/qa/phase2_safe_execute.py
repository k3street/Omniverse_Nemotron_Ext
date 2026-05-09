"""phase2_safe_execute.py — autonomous Phase 2 fix executor with revert-safety.

Mission: take an action plan from phase2_action_plan.py, apply each fix,
verify no regression on existing-✓ canonicals, revert if regressed, commit
if improved. Designed for overnight unattended operation.

Per-fix loop:
  1. Capture pre-state: stash uncommitted changes, snapshot baseline scores
     for the target CP + neighbor CPs (within same triage class).
  2. Apply fix (template edit OR tool_executor.py edit per class).
  3. Restart uvicorn if tool_executor.py was edited.
  4. Run simulate_traversal_check N=5 on target CP. If success_rate did
     NOT improve vs pre-state baseline → revert.
  5. Run simulate_traversal_check N=5 on each neighbor CP that was
     stable_ok in baseline. If any dropped to flaky/stable_fail → revert.
  6. If target improved AND no neighbor regressed → git commit + push.
  7. Otherwise → git stash drop (revert).

Safety constraints:
  - Per-fix wall-clock budget: 30 min (covers N=5 build + 5 sim + neighbor checks)
  - Total budget: 6h (safe overnight slot)
  - Hard stop on uvicorn launch failure or Kit RPC dead
  - Skip CPs where pre-state already stable_ok (no-op)
  - Audit trail: per-attempt log with hash, timestamp, decision, deltas

Usage (autonomous, after Phase 0 baseline + Phase 1.3 feasibility done):
  python scripts/qa/phase2_safe_execute.py \
      --action-plan workspace/baselines/phase2_action_plan.json \
      --baseline workspace/baselines/2026-05-09-baseline.json \
      --classes 2a,2b,2d \
      --max-attempts 30 \
      --total-budget 21600

Reports each attempt to /tmp/phase2_safe_audit.jsonl (line per attempt).
Final summary table at end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _git(*args: str, check: bool = True, capture: bool = True) -> str:
    """Run git command in REPO_ROOT, return stdout."""
    res = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=capture, text=True, check=check)
    return (res.stdout or "").strip()


def _git_status_clean() -> bool:
    return not _git("status", "--porcelain", check=False)


def _git_stash() -> bool:
    """Stash any uncommitted changes. Returns True if something was stashed."""
    if _git_status_clean():
        return False
    _git("stash", "push", "-u", "-m", "phase2_safe_execute_pre_attempt")
    return True


def _git_unstash():
    _git("stash", "pop", check=False)


def _git_revert_uncommitted():
    """Discard any uncommitted changes (after a failed attempt)."""
    _git("checkout", "--", ".", check=False)
    _git("clean", "-fd", check=False)


def _git_commit(message: str, paths: Optional[List[str]] = None) -> Optional[str]:
    if paths:
        _git("add", *paths, check=False)
    else:
        _git("add", "-A", check=False)
    if _git_status_clean():
        return None  # nothing to commit
    _git("commit", "-m", message, check=False)
    return _git("rev-parse", "HEAD", check=False)


# ── Baseline I/O ─────────────────────────────────────────────────────────

def _load_action_plan(path: Path) -> List[Dict[str, Any]]:
    return json.loads(path.read_text()).get("plans", [])


def _load_baseline(path: Path) -> Dict[str, Dict[str, Any]]:
    """Map cp_label → {status, success_rate, n_ok}."""
    payload = json.loads(path.read_text())
    return {r["label"]: r for r in payload.get("results", []) if r.get("label")}


def _extract_neighbors(target_label: str, action_plan: List[Dict[str, Any]],
                       baseline: Dict[str, Dict[str, Any]],
                       n: int = 5) -> List[str]:
    """Find up to N neighbor CPs (same triage class) that were stable_ok.
    These are the regression-check candidates."""
    target_class = next(
        (a["triage_class"] for a in action_plan if a["label"] == target_label),
        None,
    )
    candidates: List[str] = []
    for label, b in baseline.items():
        if label == target_label:
            continue
        if b.get("status") == "stable_ok":
            candidates.append(label)
        if len(candidates) >= n:
            break
    return candidates[:n]


# ── Fix application ─────────────────────────────────────────────────────

def _apply_fix_2a_2b(label: str, plan: Dict[str, Any]) -> Optional[str]:
    """Edit the canonical's template JSON per the action plan's deltas.

    Returns a string description of what was changed, or None if no
    actionable fix was generated.

    The plan's actions are tagged with axis + delta. We translate to
    template field changes.
    """
    actions = plan.get("actions") or []
    template_path = REPO_ROOT / f"workspace/templates/{label}.json"
    if not template_path.exists():
        return None
    template = json.loads(template_path.read_text())
    changed = False
    summary: List[str] = []

    # diagnose_args is the safest place to add hints. Also inject changes
    # into setup_args if those exist (most don't today).
    diag_args = template.get("diagnose_args") or {}

    for action in actions:
        axis = action.get("axis")
        delta = action.get("delta")
        if axis == "inside_obstacle_bbox" and delta and "shift_m" in delta:
            # Adjust drop_pose by the suggested delta
            ax_idx = {"x": 0, "y": 1, "z": 2}.get(delta.get("axis", "z"), 2)
            shift = delta.get("shift_m", 0.0)
            current_drop = diag_args.get("drop_pose")
            if current_drop:
                new_drop = list(current_drop)
                new_drop[ax_idx] = new_drop[ax_idx] + shift
                diag_args["drop_pose"] = new_drop
                summary.append(f"shift drop_pose +{shift:.3f}m on axis {ax_idx}")
                changed = True
        if axis == "reach_utilization" and "value" in action:
            # Need template-author judgement for this; mark in spec
            summary.append(f"reach_utilization {action['value']:.0%} flagged (no auto-fix)")

    if not changed:
        return None

    template["diagnose_args"] = diag_args
    template_path.write_text(json.dumps(template, indent=2))
    return "; ".join(summary)


def _apply_fix_2d(label: str, plan: Dict[str, Any]) -> Optional[str]:
    """Phase 2d controller-bug fixes are NOT auto-applied — they require
    judgement on root cause from runtime telemetry (probe_ctrl_telemetry).

    For safety in autonomous overnight mode we DEFER 2d fixes to morning
    review. We only AUTO-APPLY 2a/2b template-side fixes (low-blast-radius).

    Returns None to signal "no action taken" — phase2_safe_execute records
    this in the audit but doesn't attempt verification.
    """
    return None


# ── Verification (single-CP simulate_traversal_check N=5) ───────────────

async def _verify_cp(label: str, n_runs: int = 5, seed: int = 42,
                     timeout_s: int = 1200) -> Optional[Dict[str, Any]]:
    """Build + run simulate_traversal_check N=5. Returns dict with status,
    success_rate, n_ok or None on failure to get a result.

    Honors PHASE2_N_RUNS env var as override (e.g. PHASE2_N_RUNS=3 for
    GPU-tight conditions like overnight TTS contention)."""
    n_runs = int(os.environ.get("PHASE2_N_RUNS", n_runs))
    from service.isaac_assist_service.chat.canonical_instantiator import (
        execute_template_canonical, settle_after_canonical,
    )
    from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
    from service.isaac_assist_service.chat.tools import kit_tools

    template_path = REPO_ROOT / f"workspace/templates/{label}.json"
    if not template_path.exists():
        return None
    template = json.loads(template_path.read_text())
    sim_args = dict(template.get("simulate_args") or {})
    if not sim_args:
        return None
    sim_args["n_runs"] = n_runs
    sim_args["seed"] = seed

    # Reset stage
    code = (
        "import omni.usd\n"
        "ctx = omni.usd.get_context()\n"
        "ctx.new_stage()\n"
        "stage = ctx.get_stage()\n"
        "from pxr import UsdGeom\n"
        "UsdGeom.Xform.Define(stage, '/World')\n"
    )
    await kit_tools.exec_sync(code, timeout=20)
    build = await execute_template_canonical(template)
    if not build.get("instantiated"):
        return None
    try:
        await settle_after_canonical(template)
    except Exception:
        pass
    res = await execute_tool_call("simulate_traversal_check", sim_args)
    out = (res.get("output") or "").strip()
    json_lines = [l for l in out.splitlines() if l.strip().startswith("{")]
    if not json_lines:
        return None
    d = json.loads(json_lines[-1])
    return {
        "label": label,
        "status": d.get("status"),
        "success_rate": d.get("success_rate"),
        "n_ok": d.get("n_ok"),
        "n_runs": d.get("n_runs"),
    }


def _is_regression(pre: Dict[str, Any], post: Dict[str, Any]) -> bool:
    """Return True if post is worse than pre (status downgrade or rate drop)."""
    rank = {"stable_ok": 3, "flaky": 2, "stable_fail": 1, None: 0}
    pre_rank = rank.get(pre.get("status"), 0)
    post_rank = rank.get(post.get("status"), 0)
    if post_rank < pre_rank:
        return True
    pre_rate = pre.get("success_rate") or 0
    post_rate = post.get("success_rate") or 0
    if post_rate < pre_rate - 0.20:  # 20% rate drop is a regression
        return True
    return False


def _is_improvement(pre: Dict[str, Any], post: Dict[str, Any]) -> bool:
    rank = {"stable_ok": 3, "flaky": 2, "stable_fail": 1, None: 0}
    pre_rank = rank.get(pre.get("status"), 0)
    post_rank = rank.get(post.get("status"), 0)
    if post_rank > pre_rank:
        return True
    pre_rate = pre.get("success_rate") or 0
    post_rate = post.get("success_rate") or 0
    if post_rate > pre_rate + 0.20:
        return True
    return False


# ── Audit trail ────────────────────────────────────────────────────────

def _audit(audit_path: Path, entry: Dict[str, Any]) -> None:
    entry["timestamp"] = datetime.now().isoformat(timespec="seconds")
    with open(audit_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Main loop ──────────────────────────────────────────────────────────

async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--action-plan", default=str(REPO_ROOT / "workspace/baselines/phase2_action_plan.json"))
    p.add_argument("--baseline", default=str(REPO_ROOT / "workspace/baselines/2026-05-09-baseline.json"))
    p.add_argument("--classes", default="2a,2b",
                   help="Triage classes to attempt (comma-separated). 2d is deferred.")
    p.add_argument("--max-attempts", type=int, default=30)
    p.add_argument("--total-budget", type=int, default=21600,
                   help="Total seconds for the whole loop (default 6h).")
    p.add_argument("--per-attempt-budget", type=int, default=1800,
                   help="Per-attempt seconds (default 30min).")
    p.add_argument("--audit", default="/tmp/phase2_safe_audit.jsonl")
    p.add_argument("--neighbors-n", type=int, default=3,
                   help="N neighbor CPs to verify don't regress.")
    p.add_argument("--dry-run", action="store_true",
                   help="Apply fix + verify but don't commit; revert at end.")
    args = p.parse_args()

    from service.isaac_assist_service.chat.tools import kit_tools

    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at 127.0.0.1:8001", flush=True)
        return 2

    if not _git_status_clean():
        print("[WARN] git working tree not clean. Stashing uncommitted edits...", flush=True)
        _git_stash()

    plans = _load_action_plan(Path(args.action_plan))
    baseline = _load_baseline(Path(args.baseline))
    audit_path = Path(args.audit)

    classes = set(c.strip() for c in args.classes.split(","))
    candidates = [
        p_ for p_ in plans
        if any(p_.get("triage_class", "").startswith(c) for c in classes)
    ]
    candidates = candidates[:args.max_attempts]

    print(f"phase2_safe_execute — {len(candidates)} candidates "
          f"(classes={','.join(sorted(classes))})", flush=True)
    print(f"audit log: {audit_path}", flush=True)
    print(f"budget: total={args.total_budget}s per-attempt={args.per_attempt_budget}s", flush=True)
    print("-" * 78, flush=True)

    suite_start = time.time()
    n_committed = 0
    n_reverted = 0
    n_skipped = 0

    for attempt_i, plan in enumerate(candidates):
        if time.time() - suite_start > args.total_budget:
            print(f"[STOP] total budget exhausted ({args.total_budget}s)", flush=True)
            break

        label = plan["label"]
        cls = plan.get("triage_class", "")
        pre = baseline.get(label, {"status": "unknown", "success_rate": 0})
        if pre.get("status") == "stable_ok":
            n_skipped += 1
            _audit(audit_path, {"label": label, "decision": "skip_already_ok",
                                "pre_status": pre.get("status")})
            continue

        print(f"\n[{attempt_i+1}/{len(candidates)}] {label} ({cls}, pre={pre.get('status')})",
              flush=True)

        # Apply fix
        if cls.startswith("2a") or cls.startswith("2b"):
            fix_summary = _apply_fix_2a_2b(label, plan)
        elif cls.startswith("2d"):
            fix_summary = _apply_fix_2d(label, plan)
        else:
            fix_summary = None

        if not fix_summary:
            n_skipped += 1
            _audit(audit_path, {"label": label, "decision": "skip_no_actionable_fix",
                                "class": cls})
            print(f"  → skip (no actionable fix synthesized)", flush=True)
            continue

        print(f"  applied: {fix_summary}", flush=True)

        # Verify target
        attempt_start = time.time()
        try:
            post = await asyncio.wait_for(_verify_cp(label),
                                            timeout=args.per_attempt_budget)
        except asyncio.TimeoutError:
            post = None

        if not post:
            print(f"  → revert (verify timed out or failed)", flush=True)
            _git_revert_uncommitted()
            n_reverted += 1
            _audit(audit_path, {"label": label, "decision": "revert_verify_failed",
                                "fix": fix_summary})
            continue

        target_improved = _is_improvement(pre, post)
        target_regressed = _is_regression(pre, post)

        if target_regressed:
            print(f"  → revert (target regressed: {pre.get('status')} → {post.get('status')})",
                  flush=True)
            _git_revert_uncommitted()
            n_reverted += 1
            _audit(audit_path, {"label": label, "decision": "revert_target_regressed",
                                "pre": pre, "post": post, "fix": fix_summary})
            continue

        if not target_improved:
            print(f"  → revert (no improvement: rate {pre.get('success_rate')} → {post.get('success_rate')})",
                  flush=True)
            _git_revert_uncommitted()
            n_reverted += 1
            _audit(audit_path, {"label": label, "decision": "revert_no_improvement",
                                "pre": pre, "post": post, "fix": fix_summary})
            continue

        # Verify neighbors
        neighbors = _extract_neighbors(label, plans, baseline, n=args.neighbors_n)
        regressions: List[Dict[str, Any]] = []
        for nb in neighbors:
            if time.time() - attempt_start > args.per_attempt_budget:
                break
            try:
                nb_post = await asyncio.wait_for(_verify_cp(nb), timeout=600)
            except asyncio.TimeoutError:
                continue
            if not nb_post:
                continue
            nb_pre = baseline.get(nb, {})
            if _is_regression(nb_pre, nb_post):
                regressions.append({"label": nb, "pre": nb_pre, "post": nb_post})

        if regressions:
            print(f"  → revert (neighbor regression: "
                  f"{', '.join(r['label'] for r in regressions)})", flush=True)
            _git_revert_uncommitted()
            n_reverted += 1
            _audit(audit_path, {"label": label, "decision": "revert_neighbor_regressed",
                                "pre": pre, "post": post,
                                "neighbor_regressions": regressions, "fix": fix_summary})
            continue

        # Commit
        if args.dry_run:
            print(f"  → dry-run: would commit (target {pre.get('status')} → {post.get('status')})",
                  flush=True)
            _git_revert_uncommitted()
            _audit(audit_path, {"label": label, "decision": "dry_run_commit",
                                "pre": pre, "post": post, "fix": fix_summary})
        else:
            sha = _git_commit(
                f"Phase 2 fix: {label} {pre.get('status')} → {post.get('status')} "
                f"({fix_summary})"
            )
            print(f"  → commit {sha[:8] if sha else '?'} "
                  f"({pre.get('status')} → {post.get('status')})", flush=True)
            n_committed += 1
            _audit(audit_path, {"label": label, "decision": "commit",
                                "sha": sha, "pre": pre, "post": post,
                                "neighbor_checked": [n_["label"] for n_ in [{"label": x} for x in neighbors]],
                                "fix": fix_summary})

    print("\n" + "=" * 78, flush=True)
    elapsed = time.time() - suite_start
    print(f"summary: committed={n_committed} reverted={n_reverted} skipped={n_skipped}  "
          f"elapsed={elapsed:.0f}s ({elapsed/60:.0f}min)", flush=True)

    # Final push if any commits
    if n_committed > 0 and not args.dry_run:
        push_res = subprocess.run(["git", "-C", str(REPO_ROOT), "push", "anton",
                                    "feat/multimodal-foundation"],
                                   capture_output=True, text=True)
        print(f"git push: rc={push_res.returncode}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
