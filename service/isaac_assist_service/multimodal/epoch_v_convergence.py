"""Phase 82 — Epoch V convergence test.

Exercises the full Epoch V capability surface (Phases 59, 60, 65, 73, 74,
75, 78) as a single integration smoke-test that can run without a live Kit
instance.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 82.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List


PHASE_ID = 82
PHASE_TITLE = "Epoch V convergence test"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 82",
    }


# ---------------------------------------------------------------------------
# Individual step runners
# ---------------------------------------------------------------------------

def _step1_sensor_catalog_smoke() -> Dict[str, Any]:
    """Phase 73 — sensor catalog must have ≥100 entries via direct JSONL read."""
    catalog_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "workspace/knowledge/sensor_specs.jsonl"
    )
    entries: List[Dict] = []
    if catalog_path.exists():
        for line in catalog_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    count = len(entries)
    passed = count >= 100
    return {
        "step": 1,
        "name": "sensor_catalog_smoke (Phase 73)",
        "passed": passed,
        "reason": f"catalog has {count} entries (required ≥100)" if passed
                  else f"catalog has only {count} entries — need ≥100 (check sensor_specs.jsonl)",
    }


def _step2_sensor_catalog_query() -> Dict[str, Any]:
    """Phase 74 — query with depth+range filter must return ≥1 result."""
    try:
        from service.isaac_assist_service.multimodal.sensor_catalog_query import query_sensors
    except ImportError as exc:
        return {
            "step": 2,
            "name": "sensor_catalog_query (Phase 74)",
            "passed": False,
            "reason": f"import failed: {exc}",
        }

    results = query_sensors(
        "depth camera",
        filters={"max_range_m": 5.0},
    )
    count = len(results)
    passed = count >= 1
    return {
        "step": 2,
        "name": "sensor_catalog_query (Phase 74)",
        "passed": passed,
        "reason": f"max_range_m=5.0 depth filter returned {count} sensor(s)" if passed
                  else "depth camera filter returned 0 results — catalog may be empty or filters too strict",
    }


def _step3_user_object_class_registry() -> Dict[str, Any]:
    """Phase 75 — register a custom class and retrieve it."""
    try:
        from service.isaac_assist_service.multimodal.user_object_class_registry import (
            UserObjectClass,
            UserObjectClassRegistry,
        )
    except ImportError as exc:
        return {
            "step": 3,
            "name": "user_object_class_registry (Phase 75)",
            "passed": False,
            "reason": f"import failed: {exc}",
        }

    registry = UserObjectClassRegistry()
    cls = UserObjectClass(
        name="conveyor_widget",
        usd_ref="/omni/widgets/conveyor_widget.usd",
        category="factory_prop",
        footprint_xy_m=(0.05, 0.05),
        tags=["conveyor", "widget"],
        added_by="epoch_v_convergence_test",
    )
    ok = registry.register(cls)
    retrieved = registry.get("conveyor_widget")
    passed = ok and retrieved is not None and retrieved.name == "conveyor_widget"
    return {
        "step": 3,
        "name": "user_object_class_registry (Phase 75)",
        "passed": passed,
        "reason": "registered and retrieved conveyor_widget successfully" if passed
                  else f"register={ok}, retrieved={retrieved}",
    }


def _step4_factory_under_uncertainty() -> Dict[str, Any]:
    """Phase 59 — factory_under_uncertainty preset must have ≥5 ranges."""
    try:
        from service.isaac_assist_service.multimodal.sdg_factory_uncertainty import get_preset
    except ImportError as exc:
        return {
            "step": 4,
            "name": "factory_under_uncertainty preset (Phase 59)",
            "passed": False,
            "reason": f"import failed: {exc}",
        }

    preset = get_preset()
    ranges = preset.get("ranges", {})
    count = len(ranges)
    passed = count >= 5
    return {
        "step": 4,
        "name": "factory_under_uncertainty preset (Phase 59)",
        "passed": passed,
        "reason": f"preset has {count} range entries (required ≥5)" if passed
                  else f"preset has only {count} range entries — need ≥5",
    }


def _step5_five_more_presets() -> Dict[str, Any]:
    """Phase 60 — must have exactly 5 named presets available."""
    try:
        from service.isaac_assist_service.multimodal.sdg_5_more_presets import list_presets
    except ImportError as exc:
        return {
            "step": 5,
            "name": "5 more SDG presets (Phase 60)",
            "passed": False,
            "reason": f"import failed: {exc}",
        }

    presets = list_presets()
    count = len(presets)
    passed = count >= 5
    return {
        "step": 5,
        "name": "5 more SDG presets (Phase 60)",
        "passed": passed,
        "reason": f"{count} preset name(s) available: {presets}" if passed
                  else f"only {count} preset(s) — need ≥5; got: {presets}",
    }


def _step6_arena_leaderboard() -> Dict[str, Any]:
    """Phase 78 — submit a synthetic entry, assert top_k retrieves it."""
    try:
        from service.isaac_assist_service.multimodal.isaaclab_arena_leaderboard import Leaderboard
    except ImportError as exc:
        return {
            "step": 6,
            "name": "arena leaderboard (Phase 78)",
            "passed": False,
            "reason": f"import failed: {exc}",
        }

    with tempfile.TemporaryDirectory() as tmp:
        lb = Leaderboard(path=Path(tmp) / "test_arena.json")
        entry_id = lb.submit(
            scenario_id="epoch_v_smoke",
            score=99.5,
            agent_name="convergence_test_agent",
            metadata={"phase": 82},
        )
        top = lb.top_k("epoch_v_smoke", k=5)
        passed = (
            len(top) >= 1
            and top[0]["entry_id"] == entry_id
            and top[0]["score"] == 99.5
        )
    return {
        "step": 6,
        "name": "arena leaderboard (Phase 78)",
        "passed": passed,
        "reason": f"submitted entry_id={entry_id} and top_k retrieved it at rank 0" if passed
                  else f"top_k returned {top!r}",
    }


def _step7_training_run_persistence() -> Dict[str, Any]:
    """Phase 65 — upsert + get round-trip with a tmp dir."""
    try:
        from service.isaac_assist_service.multimodal.training_run_persistence import TrainingRunStore
    except ImportError as exc:
        return {
            "step": 7,
            "name": "training run persistence (Phase 65)",
            "passed": False,
            "reason": f"import failed: {exc}",
        }

    with tempfile.TemporaryDirectory() as tmp:
        store = TrainingRunStore(db_path=Path(tmp) / "test_runs.db")
        run_id = "epoch-v-test-run-001"
        store.upsert(
            run_id=run_id,
            task_name="pick_place_franka",
            algo="PPO",
            state="running",
            metadata={"phase": 82, "env": "Factory"},
        )
        record = store.get(run_id)
        passed = (
            record is not None
            and record["run_id"] == run_id
            and record["task_name"] == "pick_place_franka"
            and record["state"] == "running"
            and record["metadata"].get("phase") == 82
        )
    return {
        "step": 7,
        "name": "training run persistence (Phase 65)",
        "passed": passed,
        "reason": f"upsert+get round-trip for run_id={run_id} succeeded" if passed
                  else f"record mismatch: {record!r}",
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class EpochVCapabilityCheck:
    """Runs all 7 Epoch V capability smoke-tests and reports convergence."""

    def run(self) -> Dict[str, Any]:
        """Execute all steps and return per-step results + overall convergence_ok."""
        runners = [
            _step1_sensor_catalog_smoke,
            _step2_sensor_catalog_query,
            _step3_user_object_class_registry,
            _step4_factory_under_uncertainty,
            _step5_five_more_presets,
            _step6_arena_leaderboard,
            _step7_training_run_persistence,
        ]

        steps: List[Dict[str, Any]] = []
        for fn in runners:
            try:
                result = fn()
            except Exception as exc:
                result = {
                    "step": fn.__name__,
                    "name": fn.__name__,
                    "passed": False,
                    "reason": f"unexpected exception: {type(exc).__name__}: {exc}",
                }
            steps.append(result)

        convergence_ok = all(s["passed"] for s in steps)
        failed = [s["name"] for s in steps if not s["passed"]]

        return {
            "phase": PHASE_ID,
            "title": PHASE_TITLE,
            "steps": steps,
            "convergence_ok": convergence_ok,
            "failed_steps": failed,
            "summary": (
                f"All {len(steps)} steps passed — Epoch V capability surface converged"
                if convergence_ok
                else f"{len(failed)} step(s) failed: {failed}"
            ),
        }


def run_epoch_v_convergence() -> Dict[str, Any]:
    """Sync wrapper — run all Epoch V capability checks and return results."""
    return EpochVCapabilityCheck().run()
