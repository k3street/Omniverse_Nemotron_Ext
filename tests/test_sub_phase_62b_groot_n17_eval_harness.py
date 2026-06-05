"""Phase 62b — GR00T N1.7 eval harness tests.

Gate: pytest — verifies scenario enumeration, filtering, mock evaluation,
suite scoring, and Mimic/Dreams blueprint validation.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 62b.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mimic_config(**overrides):
    """Minimal valid MimicBlueprintConfig kwargs; override to test edge cases."""
    base = {
        "blueprint_id": "mimic-test-01",
        "num_episodes": 50,
        "episode_length_s": 20.0,
        "scene_template": "warehouse_table",
        "controller": "groot",
    }
    base.update(overrides)
    return base


def _make_dreams_config(**overrides):
    """Minimal valid DreamsBlueprintConfig kwargs; override to test edge cases."""
    base = {
        "blueprint_id": "dreams-test-01",
        "language_dataset": "s3://datasets/robot_lang_v2",
        "dataset_size": 2000,
        "target_skill_count": 15,
        "embedding_dim": 512,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1 — phase metadata
# ---------------------------------------------------------------------------

def test_phase_62b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        get_phase_metadata,
        PHASE_ID,
        PHASE_STATUS,
        PHASE_TITLE,
    )
    md = get_phase_metadata()
    assert md["phase"] == "62b"
    assert md["status"] == "landed"
    assert "gr00t" in md["title"].lower() or "groot" in md["title"].lower()
    assert PHASE_ID == "62b"
    assert PHASE_STATUS == "landed"
    assert PHASE_TITLE  # non-empty


# ---------------------------------------------------------------------------
# Test 2 — EVAL_SCENARIOS has ≥12 entries spanning ≥4 categories
# ---------------------------------------------------------------------------

def test_eval_scenarios_count_and_categories():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        EVAL_SCENARIOS,
        expected_eval_categories,
    )
    assert len(EVAL_SCENARIOS) >= 12, f"Expected ≥12 scenarios, got {len(EVAL_SCENARIOS)}"

    present_categories = {s.category for s in EVAL_SCENARIOS}
    assert len(present_categories) >= 4, (
        f"Expected ≥4 distinct categories, got {present_categories}"
    )

    # All scenario IDs must be unique
    ids = [s.scenario_id for s in EVAL_SCENARIOS]
    assert len(ids) == len(set(ids)), "Duplicate scenario_id values found"

    # expected_eval_categories returns all 5
    cats = expected_eval_categories()
    assert len(cats) == 5
    assert set(cats) == {
        "pick_place", "handoff", "navigation", "manipulation", "assembly"
    }


# ---------------------------------------------------------------------------
# Test 3 — filter_by_category returns matching scenarios
# ---------------------------------------------------------------------------

def test_filter_by_category():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    pp = harness.filter_by_category("pick_place")
    assert len(pp) >= 1
    assert all(s.category == "pick_place" for s in pp)

    nav = harness.filter_by_category("navigation")
    assert len(nav) >= 1
    assert all(s.category == "navigation" for s in nav)

    # Unknown category returns empty list, not an error
    empty = harness.filter_by_category("nonexistent_category")
    assert empty == []


# ---------------------------------------------------------------------------
# Test 4 — filter_by_difficulty returns matching scenarios
# ---------------------------------------------------------------------------

def test_filter_by_difficulty():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    l1 = harness.filter_by_difficulty("L1")
    assert len(l1) >= 1
    assert all(s.difficulty == "L1" for s in l1)

    l5 = harness.filter_by_difficulty("L5")
    assert len(l5) >= 1
    assert all(s.difficulty == "L5" for s in l5)

    # Unknown difficulty returns empty
    assert harness.filter_by_difficulty("L99") == []


# ---------------------------------------------------------------------------
# Test 5 — mock_evaluate returns deterministic EvalRun
# ---------------------------------------------------------------------------

def test_mock_evaluate_deterministic():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        EVAL_SCENARIOS,
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    scenario = EVAL_SCENARIOS[0]

    run1 = harness.mock_evaluate(scenario, model_version="gr00t-n1.7")
    run2 = harness.mock_evaluate(scenario, model_version="gr00t-n1.7")

    # Same inputs → same outputs (deterministic)
    assert run1.success == run2.success
    assert run1.score == run2.score
    assert run1.episode_steps == run2.episode_steps
    assert run1.success_criteria_met == run2.success_criteria_met
    assert run1.failure_mode == run2.failure_mode

    # Fields are correctly typed
    assert run1.scenario_id == scenario.scenario_id
    assert run1.model_version == "gr00t-n1.7"
    assert isinstance(run1.success, bool)
    assert 0.0 <= run1.score <= 1.0
    assert isinstance(run1.episode_steps, int)
    assert run1.episode_steps <= scenario.max_episode_steps
    assert isinstance(run1.success_criteria_met, dict)
    # Failure mode is set when not successful, None otherwise
    if not run1.success:
        assert run1.failure_mode is not None


# ---------------------------------------------------------------------------
# Test 6 — different model versions produce different (but still deterministic) results
# ---------------------------------------------------------------------------

def test_mock_evaluate_model_version_affects_result():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        EVAL_SCENARIOS,
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    # Use a scenario with moderate difficulty to maximise chance of divergence
    scenario = next(s for s in EVAL_SCENARIOS if s.difficulty in ("L3", "L4"))

    run_v17 = harness.mock_evaluate(scenario, model_version="gr00t-n1.7")
    run_v15 = harness.mock_evaluate(scenario, model_version="gr00t-n1.5")

    # Different model version → different hash → at least one field differs
    fields_differ = (
        run_v17.success != run_v15.success
        or run_v17.score != run_v15.score
        or run_v17.episode_steps != run_v15.episode_steps
    )
    assert fields_differ, (
        "mock_evaluate should produce different results for different model versions"
    )


# ---------------------------------------------------------------------------
# Test 7 — evaluate_suite runs all scenarios
# ---------------------------------------------------------------------------

def test_evaluate_suite_runs_all_scenarios():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        EVAL_SCENARIOS,
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    runs = harness.evaluate_suite("gr00t-n1.7")

    assert len(runs) == len(EVAL_SCENARIOS)
    run_ids = {r.scenario_id for r in runs}
    expected_ids = {s.scenario_id for s in EVAL_SCENARIOS}
    assert run_ids == expected_ids


# ---------------------------------------------------------------------------
# Test 8 — evaluate_suite accepts custom evaluator
# ---------------------------------------------------------------------------

def test_evaluate_suite_custom_evaluator():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        EvalRun,
        EvalScenario,
        GrootN17EvalHarness,
    )

    def always_succeed(scenario: EvalScenario, model_version: str) -> EvalRun:
        return EvalRun(
            scenario_id=scenario.scenario_id,
            model_version=model_version,
            success=True,
            score=1.0,
            episode_steps=1,
            success_criteria_met={k: True for k in scenario.success_criteria},
        )

    harness = GrootN17EvalHarness()
    runs = harness.evaluate_suite("test-model", evaluator=always_succeed)
    assert all(r.success for r in runs)
    assert all(r.score == 1.0 for r in runs)


# ---------------------------------------------------------------------------
# Test 9 — score_suite computes correct success_rate, by_category, by_difficulty
# ---------------------------------------------------------------------------

def test_score_suite_metrics():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    runs = harness.evaluate_suite("gr00t-n1.7")
    metrics = harness.score_suite(runs)

    assert metrics["total"] == len(runs)
    assert 0.0 <= metrics["success_rate"] <= 1.0
    assert 0.0 <= metrics["mean_score"] <= 1.0

    # Verify success_rate matches manual computation
    expected_sr = sum(1 for r in runs if r.success) / len(runs)
    assert abs(metrics["success_rate"] - round(expected_sr, 4)) < 1e-6

    # by_category and by_difficulty must be non-empty dicts
    assert isinstance(metrics["by_category"], dict)
    assert len(metrics["by_category"]) >= 4

    assert isinstance(metrics["by_difficulty"], dict)
    assert len(metrics["by_difficulty"]) >= 1

    # Each bucket has the right keys
    for _cat, bucket in metrics["by_category"].items():
        assert "total" in bucket
        assert "success_rate" in bucket
        assert "mean_score" in bucket
        assert 0.0 <= bucket["success_rate"] <= 1.0


# ---------------------------------------------------------------------------
# Test 10 — score_suite on empty list
# ---------------------------------------------------------------------------

def test_score_suite_empty():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    metrics = harness.score_suite([])
    assert metrics["total"] == 0
    assert metrics["success_rate"] == 0.0
    assert metrics["mean_score"] == 0.0


# ---------------------------------------------------------------------------
# Test 11 — validate_mimic_blueprint: clean config → empty issues
# ---------------------------------------------------------------------------

def test_validate_mimic_blueprint_clean():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        GrootN17EvalHarness,
        MimicBlueprintConfig,
    )
    harness = GrootN17EvalHarness()
    config = MimicBlueprintConfig(**_make_mimic_config())
    issues = harness.validate_mimic_blueprint(config)
    assert issues == [], f"Expected no issues, got: {issues}"


# ---------------------------------------------------------------------------
# Test 12 — validate_mimic_blueprint: num_episodes=0 → issue
# ---------------------------------------------------------------------------

def test_validate_mimic_blueprint_zero_episodes():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        GrootN17EvalHarness,
        MimicBlueprintConfig,
    )
    harness = GrootN17EvalHarness()
    config = MimicBlueprintConfig(**_make_mimic_config(num_episodes=0))
    issues = harness.validate_mimic_blueprint(config)
    assert len(issues) >= 1
    assert any("num_episodes" in iss for iss in issues)


# ---------------------------------------------------------------------------
# Test 13 — validate_mimic_blueprint: empty scene_template → issue
# ---------------------------------------------------------------------------

def test_validate_mimic_blueprint_empty_scene_template():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        GrootN17EvalHarness,
        MimicBlueprintConfig,
    )
    harness = GrootN17EvalHarness()
    config = MimicBlueprintConfig(**_make_mimic_config(scene_template=""))
    issues = harness.validate_mimic_blueprint(config)
    assert len(issues) >= 1
    assert any("scene_template" in iss for iss in issues)


# ---------------------------------------------------------------------------
# Test 14 — validate_dreams_blueprint: clean config → empty issues
# ---------------------------------------------------------------------------

def test_validate_dreams_blueprint_clean():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        DreamsBlueprintConfig,
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    config = DreamsBlueprintConfig(**_make_dreams_config())
    issues = harness.validate_dreams_blueprint(config)
    assert issues == [], f"Expected no issues, got: {issues}"


# ---------------------------------------------------------------------------
# Test 15 — validate_dreams_blueprint: empty language_dataset → issue
# ---------------------------------------------------------------------------

def test_validate_dreams_blueprint_empty_language_dataset():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        DreamsBlueprintConfig,
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    config = DreamsBlueprintConfig(**_make_dreams_config(language_dataset=""))
    issues = harness.validate_dreams_blueprint(config)
    assert len(issues) >= 1
    assert any("language_dataset" in iss for iss in issues)


# ---------------------------------------------------------------------------
# Test 16 — validate_dreams_blueprint: target_skill_count=0 → issue
# ---------------------------------------------------------------------------

def test_validate_dreams_blueprint_zero_target_skill_count():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        DreamsBlueprintConfig,
        GrootN17EvalHarness,
    )
    harness = GrootN17EvalHarness()
    config = DreamsBlueprintConfig(**_make_dreams_config(target_skill_count=0))
    issues = harness.validate_dreams_blueprint(config)
    assert len(issues) >= 1
    assert any("target_skill_count" in iss for iss in issues)


# ---------------------------------------------------------------------------
# Test 17 — EvalRun dataclass round-trip
# ---------------------------------------------------------------------------

def test_evalrun_dataclass_roundtrip():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        EvalRun,
    )
    run = EvalRun(
        scenario_id="groot_pick_single_object",
        model_version="gr00t-n1.7",
        success=True,
        score=0.92,
        episode_steps=120,
        success_criteria_met={"object_placed": True, "no_drop": True},
        failure_mode=None,
        run_at="2026-05-13T00:00:00+00:00",
    )
    assert run.scenario_id == "groot_pick_single_object"
    assert run.model_version == "gr00t-n1.7"
    assert run.success is True
    assert run.score == 0.92
    assert run.episode_steps == 120
    assert run.success_criteria_met["object_placed"] is True
    assert run.failure_mode is None
    assert run.run_at == "2026-05-13T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Test 18 — EvalRun.run_at auto-populated when blank
# ---------------------------------------------------------------------------

def test_evalrun_run_at_auto_populated():
    from service.isaac_assist_service.multimodal.sub_phase_62b_groot_n17_eval_harness import (
        EvalRun,
    )
    run = EvalRun(
        scenario_id="groot_snap_fit",
        model_version="gr00t-n1.7",
        success=False,
        score=0.3,
        episode_steps=200,
        success_criteria_met={},
        failure_mode="timeout",
    )
    # run_at should have been auto-set in __post_init__
    assert run.run_at  # non-empty
    assert "T" in run.run_at  # ISO-8601 format
