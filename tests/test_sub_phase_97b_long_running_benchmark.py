"""Phase 97b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_97b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_97b_long_running_benchmark import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "97b"
    assert md["status"] == "landed"


def test_long_run_checkpoints_and_resumes(tmp_path):
    from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import CPTestCase
    from service.isaac_assist_service.multimodal.sub_phase_97b_long_running_benchmark import (
        LongRunConfig,
        LongRunningBenchmark,
    )

    cases = [CPTestCase("CP-1", "one", "CW", "test", [])]
    checkpoint = tmp_path / "runs.jsonl"
    benchmark = LongRunningBenchmark(cases)
    first = benchmark.run(LongRunConfig(2, 60, checkpoint))
    second = benchmark.run(LongRunConfig(1, 60, checkpoint))
    assert first.completed_runs == 2
    assert second.completed_runs == 3
    assert '"run_idx": 2' in checkpoint.read_text(encoding="utf-8")


def test_long_run_honors_fail_fast(tmp_path):
    from service.isaac_assist_service.multimodal.sub_phase_97b_fast_sweep_cp_regression import CPRunResult, CPTestCase
    from service.isaac_assist_service.multimodal.sub_phase_97b_long_running_benchmark import LongRunConfig, LongRunningBenchmark

    case = CPTestCase("CP-X", "x", "CW", "test", [])
    result = LongRunningBenchmark([case]).run(
        LongRunConfig(5, 60, tmp_path / "failed.jsonl", fail_fast=True),
        runner=lambda c, i: CPRunResult(c.cp_id, i, False, 0.0, 0.1, [], ["failed"]),
    )
    assert result.stopped_reason == "failure"
    assert result.completed_runs == 1
