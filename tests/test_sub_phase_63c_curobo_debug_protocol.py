"""Phase 63c — Per-robot-family cuRobo debugging protocol: pytest suite.

Gate: debug checklist exposes ≥6 checks per family; failure classifier maps
      failure modes to fix recipes.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mod():
    from service.isaac_assist_service.multimodal import (
        sub_phase_63c_curobo_debug_protocol as m,
    )
    return m


@pytest.fixture(scope="module")
def protocol(mod):
    return mod.CuRoboDebugProtocol()


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_metadata(mod):
    md = mod.get_phase_metadata()
    assert md["phase"] == "63c"
    assert md["status"] == "landed"
    assert "cuRobo" in md["title"] or "curobo" in md["title"].lower()
    assert "robot_families" in md
    assert "failure_modes" in md


# ---------------------------------------------------------------------------
# 2. expected_failure_modes has ≥8 entries
# ---------------------------------------------------------------------------


def test_expected_failure_modes_min_8(mod):
    modes = mod.expected_failure_modes()
    assert len(modes) >= 8, f"Expected ≥8 failure modes, got {len(modes)}: {modes}"


def test_expected_failure_modes_contains_key_entries(mod):
    modes = mod.expected_failure_modes()
    required = {
        "no_plan_found",
        "joint_limit_violation",
        "ik_singularity",
        "warp_kernel_oom",
        "self_collision_at_start",
        "goal_unreachable",
        "scene_collision_phantom",
        "obstacle_inflation_too_aggressive",
    }
    missing = required - set(modes)
    assert not missing, f"Missing failure modes: {missing}"


# ---------------------------------------------------------------------------
# 3. DEBUG_CHECKS_BY_FAMILY has ≥6 robot families
# ---------------------------------------------------------------------------


def test_debug_checks_by_family_min_6_families(mod):
    families = list(mod.DEBUG_CHECKS_BY_FAMILY.keys())
    assert len(families) >= 6, f"Expected ≥6 families, got {len(families)}: {families}"


def test_debug_checks_all_known_families_present(mod):
    expected = {"franka", "ur", "yaskawa", "abb", "kuka", "fanuc", "humanoid_g1", "humanoid_h1", "mobile_base"}
    present = set(mod.DEBUG_CHECKS_BY_FAMILY.keys())
    missing = expected - present
    assert not missing, f"Missing robot families: {missing}"


# ---------------------------------------------------------------------------
# 4. Each family in DEBUG_CHECKS_BY_FAMILY has ≥6 checks
# ---------------------------------------------------------------------------


def test_each_family_has_min_6_checks(mod):
    for family, checks in mod.DEBUG_CHECKS_BY_FAMILY.items():
        assert len(checks) >= 6, (
            f"Family '{family}' has only {len(checks)} checks (need ≥6)"
        )


# ---------------------------------------------------------------------------
# 5. FIXUP_RECIPES has ≥8 entries
# ---------------------------------------------------------------------------


def test_fixup_recipes_min_8(mod):
    assert len(mod.FIXUP_RECIPES) >= 8, (
        f"Expected ≥8 fixup recipes, got {len(mod.FIXUP_RECIPES)}"
    )


def test_fixup_recipes_cover_all_failure_modes(mod):
    """Every failure mode has at least one recipe."""
    modes_with_recipes = {r.failure_mode for r in mod.FIXUP_RECIPES}
    all_modes = set(mod.expected_failure_modes())
    missing = all_modes - modes_with_recipes
    assert not missing, f"No recipe for failure modes: {missing}"


# ---------------------------------------------------------------------------
# 6. checks_for("franka") returns franka-specific + universal checks
# ---------------------------------------------------------------------------


def test_checks_for_franka_includes_universal_and_specific(mod, protocol):
    checks = protocol.checks_for("franka")
    check_ids = {c.check_id for c in checks}
    # Universal checks
    assert "scene_obstacles_loaded" in check_ids
    assert "joint_limits_match_urdf" in check_ids
    assert "tcp_frame_set_correctly" in check_ids
    # Franka-specific check
    assert "franka_panda_link8_offset" in check_ids


def test_checks_for_franka_min_6(protocol):
    checks = protocol.checks_for("franka")
    assert len(checks) >= 6


# ---------------------------------------------------------------------------
# 7. recipe_for("warp_kernel_oom") returns a FixupRecipe
# ---------------------------------------------------------------------------


def test_recipe_for_warp_kernel_oom(mod, protocol):
    recipe = protocol.recipe_for("warp_kernel_oom")
    assert recipe is not None
    assert isinstance(recipe, mod.FixupRecipe)
    assert recipe.failure_mode == "warp_kernel_oom"
    assert len(recipe.steps) >= 1
    assert recipe.estimated_minutes > 0
    assert recipe.success_indicator


# ---------------------------------------------------------------------------
# 8. recipe_for unknown returns None
# ---------------------------------------------------------------------------


def test_recipe_for_unknown_returns_none(protocol):
    assert protocol.recipe_for("totally_unknown_failure") is None
    assert protocol.recipe_for("") is None


# ---------------------------------------------------------------------------
# 9. classify_failure: "planning failed: no feasible solution" → no_plan_found
# ---------------------------------------------------------------------------


def test_classify_failure_no_plan_found(protocol):
    result = protocol.classify_failure("planning failed: no feasible solution found")
    assert result == "no_plan_found"


def test_classify_failure_no_feasible(protocol):
    result = protocol.classify_failure("cuRobo: no feasible trajectory")
    assert result == "no_plan_found"


# ---------------------------------------------------------------------------
# 10. classify_failure: "joint limit exceeded" → joint_limit_violation
# ---------------------------------------------------------------------------


def test_classify_failure_joint_limit(protocol):
    result = protocol.classify_failure("Error: joint limit exceeded at joint 3")
    assert result == "joint_limit_violation"


# ---------------------------------------------------------------------------
# 11. classify_failure: "warp out of memory" → warp_kernel_oom
# ---------------------------------------------------------------------------


def test_classify_failure_warp_oom(protocol):
    result = protocol.classify_failure("warp out of memory during kernel launch")
    assert result == "warp_kernel_oom"


def test_classify_failure_oom_keyword(protocol):
    result = protocol.classify_failure("CUDA OOM: allocation failed")
    assert result == "warp_kernel_oom"


# ---------------------------------------------------------------------------
# 12. classify_failure: empty/unknown → None
# ---------------------------------------------------------------------------


def test_classify_failure_empty_returns_none(protocol):
    assert protocol.classify_failure("") is None
    assert protocol.classify_failure("   ") is None


def test_classify_failure_unknown_returns_none(protocol):
    assert protocol.classify_failure("some completely unrelated log line") is None


# ---------------------------------------------------------------------------
# 13. classify_failure: remaining modes
# ---------------------------------------------------------------------------


def test_classify_failure_singularity(protocol):
    result = protocol.classify_failure("IK singularity detected near current config")
    assert result == "ik_singularity"


def test_classify_failure_self_collision_at_start(protocol):
    result = protocol.classify_failure("self-collision at start state detected")
    assert result == "self_collision_at_start"


def test_classify_failure_goal_unreachable(protocol):
    result = protocol.classify_failure("goal is unreachable from current configuration")
    assert result == "goal_unreachable"


def test_classify_failure_phantom(protocol):
    result = protocol.classify_failure("phantom collision detected in world model")
    assert result == "scene_collision_phantom"


def test_classify_failure_inflation(protocol):
    result = protocol.classify_failure("obstacle inflation radius too aggressive")
    assert result == "obstacle_inflation_too_aggressive"


# ---------------------------------------------------------------------------
# 14. recipes_for_failures returns recipes for each matched failure
# ---------------------------------------------------------------------------


def test_recipes_for_failures_returns_matched(mod, protocol):
    failures = ["warp_kernel_oom", "joint_limit_violation", "no_plan_found"]
    recipes = protocol.recipes_for_failures(failures)
    assert len(recipes) == 3
    returned_modes = {r.failure_mode for r in recipes}
    assert returned_modes == set(failures)


def test_recipes_for_failures_skips_unknown(protocol):
    failures = ["warp_kernel_oom", "does_not_exist"]
    recipes = protocol.recipes_for_failures(failures)
    assert len(recipes) == 1
    assert recipes[0].failure_mode == "warp_kernel_oom"


def test_recipes_for_failures_deduplicates(protocol):
    """Same failure mode listed twice should yield one recipe."""
    failures = ["no_plan_found", "no_plan_found"]
    recipes = protocol.recipes_for_failures(failures)
    assert len(recipes) == 1


# ---------------------------------------------------------------------------
# 15. summary_for returns dict with required keys
# ---------------------------------------------------------------------------


def test_summary_for_franka_has_required_keys(protocol):
    summary = protocol.summary_for("franka")
    assert "check_count" in summary
    assert "families_supported" in summary
    assert "recipe_count" in summary


def test_summary_for_franka_values(mod, protocol):
    summary = protocol.summary_for("franka")
    assert summary["check_count"] >= 6
    assert summary["families_supported"] >= 6
    assert summary["recipe_count"] >= 8


def test_summary_for_unknown_family(protocol):
    """Unknown family should return zero checks but still have the other keys."""
    summary = protocol.summary_for("unknown_robot")
    assert "check_count" in summary
    assert summary["check_count"] == 0
