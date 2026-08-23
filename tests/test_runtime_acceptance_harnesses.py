import pytest

from service.isaac_assist_service.multimodal.arena_benchmark_hand_vs_ia import (
    ArenaRun,
    compare_cohorts,
)
from service.isaac_assist_service.multimodal.pick_hold_weld_e2e import (
    PickHoldWeldRun,
    evaluate_run,
)
from service.isaac_assist_service.multimodal.whole_body_control_humanoid import (
    WBCReplayEvidence,
    evaluate_replay,
)


def test_phase79_requires_live_g1_and_strictly_less_than_5cm_rms():
    passed = evaluate_replay(WBCReplayEvidence("Unitree G1", "demo", [0.03, 0.04], True, True, True))
    boundary = evaluate_replay(WBCReplayEvidence("G1", "demo", [0.05], True, True, True))
    assert passed["accepted"] is True
    assert passed["rms_error_m"] == pytest.approx(0.035355339)
    assert boundary["accepted"] is False


def test_phase99_reports_failed_runtime_gates_and_numeric_gaps():
    result = evaluate_run(PickHoldWeldRun(
        True, True, True, 2, True, True, 60.0, True, True,
        4.2, 4.0, 7000.0, 7560.0, True,
    ))
    assert result["accepted"] is True
    assert result["cycle_time_gap"] == pytest.approx(2 / 42)
    assert result["energy_gap"] == pytest.approx(0.08)


def test_phase100_compares_all_required_metrics():
    hand = [ArenaRun("s1", "hand", [0.01, 0.02], True, 0.9, 120.0)]
    ia = [ArenaRun("s1", "ia", [0.02, 0.02], True, 0.8, 80.0)]
    result = compare_cohorts(hand, ia)
    assert set(result["ia_minus_hand_crafted"]) == {
        "placement_rms_error_m", "scene_spawn_success_rate",
        "intent_fidelity", "mean_time_to_build_s",
    }
    assert result["ia_minus_hand_crafted"]["mean_time_to_build_s"] == -40.0
