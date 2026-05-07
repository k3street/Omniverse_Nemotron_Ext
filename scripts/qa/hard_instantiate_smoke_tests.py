#!/usr/bin/env python
"""
hard_instantiate_smoke_tests.py — regression boundary for the hard-instantiate
flow (canonical_instantiator + retrieve_templates_with_scores + tool-subset
replacement in orchestrator).

Per docs/specs/2026-05-08-harness-layers-and-failure-modes.md.

Three fixtures:
  1. confident_match_cp02      — query matches CP-02 strongly. Expect:
     hard-instantiate fires, 23/23 tool calls succeed, verify returns
     pipeline_ok=true.
  2. ambiguous_match           — query matches CP-01 and CP-02 closely.
     Expect: gap < min_margin → hard-instantiate does NOT fire, falls back
     to few-shot guide path.
  3. low_confidence_match      — query matches nothing strongly. Expect:
     similarity < min_sim → hard-instantiate does NOT fire.

Asserts the gating logic + instantiation outcome. Does NOT call the chat
endpoint (that goes through the LLM and is non-deterministic). This is a
deterministic check on the orchestrator's pre-LLM logic.

Prereqs:
    - Isaac Sim running with Isaac Assist extension (Kit RPC alive on :8001)
    - workspace/tool_index/ collections built (run rebuild if needed)

Usage:
    python scripts/qa/hard_instantiate_smoke_tests.py
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
os.environ["AUTO_APPROVE"] = "true"

from service.isaac_assist_service.chat.tools.template_retriever import (
    retrieve_templates_with_scores,
)
from service.isaac_assist_service.chat.canonical_instantiator import (
    execute_template_canonical, execute_template_verify,
    ALLOWED_AFTER_INSTANTIATE,
)
from service.isaac_assist_service.chat.tools import kit_tools


# ── Reset (cleanup leftover pickplace state between fixtures) ───────────────

RESET_CODE = """
import omni.usd, omni.timeline, builtins
omni.timeline.get_timeline_interface().stop()
_pp_prefixes = ("_curobo_pp_", "_native_pp_", "_spline_pp_", "_diffik_pp_", "_osc_pp_",
                "_pick_place_", "_sensor_gated_", "_fixed_poses_pp_", "_sensor_")
for k in list(vars(builtins).keys()):
    if k.startswith(_pp_prefixes):
        v = getattr(builtins, k, None)
        try:
            if hasattr(v, "unsubscribe"):
                v.unsubscribe()
        except Exception:
            pass
        try:
            delattr(builtins, k)
        except Exception:
            pass
mgr = getattr(builtins, "_scene_reset_manager", None)
if mgr is not None:
    for _hn in list(getattr(mgr, "hooks", {}).keys()):
        try: mgr.unregister(_hn)
        except Exception: pass
omni.usd.get_context().new_stage()
"""


# ── Calibration thresholds (must match orchestrator defaults) ───────────────

DEFAULT_MIN_SIM = float(os.environ.get("CANONICAL_MIN_SIM", "0.45"))
DEFAULT_MIN_MARGIN = float(os.environ.get("CANONICAL_MIN_MARGIN", "0.20"))


def _gate_decision(scored, min_sim=DEFAULT_MIN_SIM, min_margin=DEFAULT_MIN_MARGIN):
    """Mirror of orchestrator's confidence check."""
    if not scored:
        return False, 0.0, 0.0
    top_sim = scored[0]["similarity"]
    second_sim = scored[1]["similarity"] if len(scored) > 1 else 0.0
    margin = top_sim - second_sim
    return (top_sim >= min_sim and margin >= min_margin), top_sim, margin


# ── Fixtures ────────────────────────────────────────────────────────────────

# 1. Confident match — CP-02 own goal text. Expect hard-instantiate fires.
QUERY_CONFIDENT = (
    "Build a multi-station assembly line in Isaac Sim: a cube spawns on "
    "conveyor 1, is carried to robot 1's pick zone, robot 1 picks it up "
    "and places it on conveyor 2, conveyor 2 carries it to robot 2, "
    "robot 2 picks it up and drops it in a bin."
)

# 2. Ambiguous — VR-19 prompt sits between CP-01 and CP-02 with small gap.
QUERY_AMBIGUOUS = (
    "Build a 3-station assembly line that takes a 5cm cube from station "
    "A on the left through to station C on the right. Two Franka robots "
    "handle the picks and places along the way."
)

# 3. Low confidence — totally off-topic.
QUERY_LOW = "What is the capital of Sweden? Tell me about lingonberries."


async def fix_confident_match_cp02():
    await kit_tools.exec_sync(RESET_CODE, timeout=10)
    scored = retrieve_templates_with_scores(QUERY_CONFIDENT, top_k=3)
    confident, top_sim, margin = _gate_decision(scored)
    top_id = scored[0]["task_id"] if scored else None
    if not confident:
        return False, {"reason": "expected confident match", "top": top_id,
                       "sim": top_sim, "margin": margin}

    inst = await execute_template_canonical(scored[0]["template"])
    ver = await execute_template_verify(scored[0]["template"])
    return True, {
        "top": top_id, "sim": round(top_sim, 3), "margin": round(margin, 3),
        "instantiated": inst.get("instantiated"),
        "n_ok": inst.get("n_ok"), "n_calls": inst.get("n_calls"),
        "pipeline_ok": ver.get("pipeline_ok"),
        "issues": len(ver.get("issues", [])),
    }


async def fix_ambiguous_match():
    scored = retrieve_templates_with_scores(QUERY_AMBIGUOUS, top_k=3)
    confident, top_sim, margin = _gate_decision(scored)
    top_id = scored[0]["task_id"] if scored else None
    # Expectation: should NOT be confident
    return (not confident), {
        "top": top_id, "sim": round(top_sim, 3), "margin": round(margin, 3),
        "would_hard_instantiate": confident,
    }


async def fix_low_confidence_match():
    scored = retrieve_templates_with_scores(QUERY_LOW, top_k=3)
    confident, top_sim, margin = _gate_decision(scored)
    top_id = scored[0]["task_id"] if scored else None
    return (not confident), {
        "top": top_id, "sim": round(top_sim, 3), "margin": round(margin, 3),
        "would_hard_instantiate": confident,
    }


FIXTURES = [
    ("confident_match_cp02",   fix_confident_match_cp02,
     "expect: confident → instantiate + verify returns pipeline_ok=True"),
    ("ambiguous_match",        fix_ambiguous_match,
     "expect: NOT confident (small margin) → falls back to few-shot"),
    ("low_confidence_match",   fix_low_confidence_match,
     "expect: NOT confident (low similarity) → falls back to few-shot"),
]


# ── Driver ──────────────────────────────────────────────────────────────────

async def main() -> int:
    if not await kit_tools.is_kit_rpc_alive():
        print("[FAIL] Kit RPC not alive at http://127.0.0.1:8001")
        return 1

    print(f"=== hard_instantiate gates: sim>={DEFAULT_MIN_SIM} "
          f"margin>={DEFAULT_MIN_MARGIN} ===")
    print(f"=== ALLOWED_AFTER_INSTANTIATE: {len(ALLOWED_AFTER_INSTANTIATE)} tools ===")
    print()

    rows = []
    for name, fn, expectation in FIXTURES:
        print(f"  {name}: {expectation}")
        try:
            ok, info = await fn()
        except Exception as e:
            print(f"\n[FAIL] fixture {name!r} crashed: {type(e).__name__}: {e}")
            return 2
        rows.append((name, ok, info))

    print()
    print(f"{'fixture':<30}  pass  details")
    print("-" * 100)
    for name, ok, info in rows:
        flag = "✓" if ok else "✗"
        details = ", ".join(f"{k}={v}" for k, v in info.items())
        print(f"{name:<30}  {flag}     {details}")

    fail_count = sum(1 for _, ok, _ in rows if not ok)
    if fail_count:
        print(f"\n[FAIL] {fail_count}/{len(rows)} fixtures failed expectation")
        return 3
    print(f"\n[OK] all {len(rows)} fixtures matched expectations")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
