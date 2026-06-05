"""Phase 62 — GR00T finetune pipeline orchestrator tests.

Gate: orchestrator walks 4+ stages in order, validates config,
      dry-run produces stage events.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _import():
    from service.isaac_assist_service.multimodal.groot_finetune_pipeline import (
        KNOWN_GROOT_MODELS,
        PHASE_STATUS,
        GrootFinetuneConfig,
        GrootFinetunePipeline,
        StageEvent,
        estimate_finetune_cost,
        get_phase_metadata,
    )
    return (
        KNOWN_GROOT_MODELS,
        PHASE_STATUS,
        GrootFinetuneConfig,
        GrootFinetunePipeline,
        StageEvent,
        estimate_finetune_cost,
        get_phase_metadata,
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_phase_62_metadata():
    (_, _, _, _, _, _, get_phase_metadata) = _import()
    md = get_phase_metadata()
    assert md["phase"] == 62
    assert md["status"] == "landed"
    assert "GR00T" in md["title"] or "gr00t" in md["title"].lower()
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# GrootFinetuneConfig defaults
# ---------------------------------------------------------------------------


def test_groot_finetune_config_defaults():
    (_, _, GrootFinetuneConfig, _, _, _, _) = _import()
    cfg = GrootFinetuneConfig()
    assert cfg.base_model == "gr00t-n1.5"
    assert cfg.dataset_path == ""
    assert cfg.output_dir == "/tmp/groot_finetune"
    assert cfg.batch_size == 16
    assert cfg.learning_rate == pytest.approx(1e-4)
    assert cfg.num_epochs == 10
    assert cfg.eval_split == pytest.approx(0.1)
    assert cfg.lora_rank == 8
    assert cfg.mixed_precision is True


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def test_validate_config_clean():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg)
    issues = pipeline.validate_config()
    assert issues == [], f"Expected no issues but got: {issues}"


def test_validate_config_unknown_base_model():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(base_model="unknown-model-xyz", dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg)
    issues = pipeline.validate_config()
    assert any("base_model" in i or "Unknown" in i for i in issues), issues


def test_validate_config_empty_dataset_path():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="")
    pipeline = GrootFinetunePipeline(cfg)
    issues = pipeline.validate_config()
    assert any("dataset_path" in i for i in issues), issues


def test_validate_config_zero_batch_size():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos", batch_size=0)
    pipeline = GrootFinetunePipeline(cfg)
    issues = pipeline.validate_config()
    assert any("batch_size" in i for i in issues), issues


def test_validate_config_negative_batch_size():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos", batch_size=-4)
    pipeline = GrootFinetunePipeline(cfg)
    issues = pipeline.validate_config()
    assert any("batch_size" in i for i in issues), issues


def test_validate_config_bad_learning_rate():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos", learning_rate=2.0)
    pipeline = GrootFinetunePipeline(cfg)
    issues = pipeline.validate_config()
    assert any("learning_rate" in i for i in issues), issues


def test_validate_config_bad_eval_split():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos", eval_split=0.9)
    pipeline = GrootFinetunePipeline(cfg)
    issues = pipeline.validate_config()
    assert any("eval_split" in i for i in issues), issues


# ---------------------------------------------------------------------------
# run() dry-run
# ---------------------------------------------------------------------------


def test_run_dry_run_walks_all_stages():
    """Dry-run run() must produce exactly 6 stage events."""
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg, dry_run=True)
    events = pipeline.run()
    assert len(events) == 6, f"Expected 6 stages, got {len(events)}: {[e.stage for e in events]}"

    expected_order = [
        "validate_config",
        "prepare_data",
        "launch_finetune",
        "monitor",
        "export_policy",
        "evaluate",
    ]
    for i, (event, expected_stage) in enumerate(zip(events, expected_order)):
        assert event.stage == expected_stage, (
            f"Stage {i}: expected '{expected_stage}', got '{event.stage}'"
        )


def test_run_dry_run_all_events_ok():
    """Every StageEvent returned in dry-run must have status='ok'."""
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, StageEvent, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg, dry_run=True)
    events = pipeline.run()
    for event in events:
        assert isinstance(event, StageEvent)
        assert event.status == "ok", f"Stage '{event.stage}' has status '{event.status}'"


def test_run_dry_run_events_have_timestamps():
    """StageEvents must carry non-empty started_at strings."""
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg, dry_run=True)
    events = pipeline.run()
    for event in events:
        assert event.started_at, f"Stage '{event.stage}' missing started_at"


# ---------------------------------------------------------------------------
# run() non-dry-run raises NotImplementedError on launch_finetune
# ---------------------------------------------------------------------------


def test_run_non_dry_run_raises_not_implemented():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg, dry_run=False)
    with pytest.raises(NotImplementedError):
        pipeline.run()


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


def test_status_reflects_completed_stages():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg, dry_run=True)
    pipeline.run()
    s = pipeline.status()
    assert "completed_stages" in s
    assert "current_stage" in s
    assert "failed" in s
    assert "total_events" in s
    assert s["total_events"] == 6
    assert len(s["completed_stages"]) == 6
    assert s["failed"] is False
    assert s["current_stage"] == "complete"


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------


def test_cancel_sets_failed():
    (_, _, GrootFinetuneConfig, GrootFinetunePipeline, _, _, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    pipeline = GrootFinetunePipeline(cfg)
    assert pipeline.status()["failed"] is False
    pipeline.cancel()
    assert pipeline.status()["failed"] is True


# ---------------------------------------------------------------------------
# KNOWN_GROOT_MODELS
# ---------------------------------------------------------------------------


def test_known_groot_models_has_three_entries():
    (KNOWN_GROOT_MODELS, _, _, _, _, _, _) = _import()
    assert len(KNOWN_GROOT_MODELS) == 3
    assert "gr00t-n1" in KNOWN_GROOT_MODELS
    assert "gr00t-n1.5" in KNOWN_GROOT_MODELS
    assert "gr00t-n1.7" in KNOWN_GROOT_MODELS


# ---------------------------------------------------------------------------
# estimate_finetune_cost
# ---------------------------------------------------------------------------


def test_estimate_finetune_cost_returns_required_keys():
    (_, _, GrootFinetuneConfig, _, _, estimate_finetune_cost, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos", batch_size=16, num_epochs=5)
    result = estimate_finetune_cost(cfg, dataset_samples=1000)
    assert "estimated_hours" in result
    assert "estimated_gpu_hours" in result
    assert "estimated_dollars" in result


def test_estimate_finetune_cost_positive_for_nonzero_samples():
    (_, _, GrootFinetuneConfig, _, _, estimate_finetune_cost, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos", batch_size=16, num_epochs=10)
    result = estimate_finetune_cost(cfg, dataset_samples=100)
    assert result["estimated_hours"] > 0
    assert result["estimated_gpu_hours"] > 0
    assert result["estimated_dollars"] > 0


def test_estimate_finetune_cost_zero_samples():
    (_, _, GrootFinetuneConfig, _, _, estimate_finetune_cost, _) = _import()
    cfg = GrootFinetuneConfig(dataset_path="/data/demos")
    result = estimate_finetune_cost(cfg, dataset_samples=0)
    assert result["estimated_hours"] == 0.0
    assert result["estimated_dollars"] == 0.0


# ---------------------------------------------------------------------------
# PHASE_STATUS constant
# ---------------------------------------------------------------------------


def test_phase_status_is_landed():
    (_, PHASE_STATUS, _, _, _, _, _) = _import()
    assert PHASE_STATUS == "landed"
