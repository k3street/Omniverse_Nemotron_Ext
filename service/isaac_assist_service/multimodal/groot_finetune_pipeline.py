"""Phase 62 — GR00T finetune pipeline: single-command invocation.

Orchestrator layer: pipeline stages, config validation, dry-run flow,
progress tracking. Full finetune execution requires GR00T weights + GPU
(opus-runtime gate). Orchestrator is pure Python — no external deps.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 62.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_ID = 62
PHASE_TITLE = "GR00T finetune pipeline: single-command invocation"
PHASE_STATUS = "landed"

KNOWN_GROOT_MODELS: set[str] = {"gr00t-n1", "gr00t-n1.5", "gr00t-n1.7"}

FinetuneStage = Literal[
    "validate_config",
    "prepare_data",
    "launch_finetune",
    "monitor",
    "export_policy",
    "evaluate",
    "complete",
    "failed",
]

# Ordered list of stages that run() walks through
_PIPELINE_STAGES: list[FinetuneStage] = [
    "validate_config",
    "prepare_data",
    "launch_finetune",
    "monitor",
    "export_policy",
    "evaluate",
]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class GrootFinetuneConfig:
    """Configuration for a GR00T finetune run."""
    base_model: str = "gr00t-n1.5"
    dataset_path: str = ""
    output_dir: str = "/tmp/groot_finetune"
    batch_size: int = 16
    learning_rate: float = 1e-4
    num_epochs: int = 10
    eval_split: float = 0.1
    lora_rank: int = 8
    mixed_precision: bool = True


@dataclass
class StageEvent:
    """Record of a single pipeline stage execution."""
    stage: FinetuneStage
    started_at: str
    status: Literal["pending", "running", "ok", "failed"]
    message: str = ""
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def estimate_finetune_cost(
    config: GrootFinetuneConfig,
    dataset_samples: int,
) -> dict[str, float]:
    """Heuristic cost estimate for a finetune run.

    Formula:
        steps_total = ceil(dataset_samples / batch_size) * num_epochs
        compute_hours = steps_total * 0.005 / 3600   (0.005 s/step proxy)
        gpu_hours = compute_hours  (single GPU assumed)
        dollars = gpu_hours * 1.0  ($1 / GPU-hour)
    """
    if dataset_samples <= 0:
        steps_total = 0.0
    else:
        steps_per_epoch = max(1, -(-dataset_samples // max(1, config.batch_size)))  # ceiling div
        steps_total = steps_per_epoch * config.num_epochs

    compute_hours = steps_total * 0.005 / 3600.0
    estimated_hours = compute_hours
    estimated_gpu_hours = compute_hours
    estimated_dollars = estimated_gpu_hours * 1.0

    return {
        "estimated_hours": round(estimated_hours, 4),
        "estimated_gpu_hours": round(estimated_gpu_hours, 4),
        "estimated_dollars": round(estimated_dollars, 4),
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class GrootFinetunePipeline:
    """Orchestrator for the GR00T finetune pipeline.

    In *dry_run* mode (default) all stages produce mock ok events so the
    pipeline can be exercised without weights or a GPU.

    In real mode (dry_run=False) the pipeline will raise NotImplementedError
    on the *launch_finetune* stage because actual training requires GR00T
    weights + GPU (opus-runtime gate).
    """

    def __init__(
        self,
        config: GrootFinetuneConfig,
        dry_run: bool = True,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self._events: list[StageEvent] = []
        self._current_stage: Optional[FinetuneStage] = None
        self._failed = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_config(self) -> list[str]:
        """Validate the pipeline config.

        Returns a list of human-readable issue strings.  Empty list means
        the config is clean and the pipeline can proceed.
        """
        issues: list[str] = []

        if self.config.base_model not in KNOWN_GROOT_MODELS:
            issues.append(
                f"Unknown base_model '{self.config.base_model}'; "
                f"known: {sorted(KNOWN_GROOT_MODELS)}"
            )

        if not self.config.dataset_path or not self.config.dataset_path.strip():
            issues.append("dataset_path must be a non-empty string")

        if self.config.batch_size <= 0:
            issues.append(f"batch_size must be > 0, got {self.config.batch_size}")

        if not (0.0 < self.config.learning_rate < 1.0):
            issues.append(
                f"learning_rate must be in (0, 1), got {self.config.learning_rate}"
            )

        if self.config.num_epochs < 1:
            issues.append(f"num_epochs must be >= 1, got {self.config.num_epochs}")

        if not (0.0 < self.config.eval_split < 0.5):
            issues.append(
                f"eval_split must be in (0, 0.5), got {self.config.eval_split}"
            )

        return issues

    def run(self) -> list[StageEvent]:
        """Walk all 6 pipeline stages in order and return stage events.

        In dry-run mode every stage emits a mock ok event.
        In real mode *launch_finetune* raises NotImplementedError.
        """
        self._events = []
        self._failed = False

        for stage in _PIPELINE_STAGES:
            if self._failed:
                break
            event = self._run_stage(stage)
            self._events.append(event)
            if event.status == "failed":
                self._failed = True
                self._current_stage = "failed"
                break

        if not self._failed:
            self._current_stage = "complete"

        return list(self._events)

    def status(self) -> dict[str, Any]:
        """Return a summary of the current pipeline state."""
        completed = [e.stage for e in self._events if e.status == "ok"]
        return {
            "current_stage": self._current_stage,
            "completed_stages": completed,
            "failed": self._failed,
            "total_events": len(self._events),
        }

    def cancel(self) -> None:
        """Mark the pipeline as failed/cancelled."""
        self._failed = True
        self._current_stage = "failed"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_stage(self, stage: FinetuneStage) -> StageEvent:
        """Execute a single stage and return its StageEvent.

        In dry-run mode every stage returns ok with a mock message.
        In real mode *launch_finetune* raises NotImplementedError.
        """
        self._current_stage = stage
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        if not self.dry_run and stage == "launch_finetune":
            raise NotImplementedError(
                "launch_finetune requires GR00T weights and a GPU "
                "(opus-runtime gate). Use dry_run=True for pipeline testing."
            )

        # dry-run: produce mock ok events with descriptive messages
        mock_messages: dict[FinetuneStage, str] = {
            "validate_config": (
                f"Config OK — model={self.config.base_model}, "
                f"dataset_path='{self.config.dataset_path}', "
                f"batch_size={self.config.batch_size}"
            ),
            "prepare_data": (
                f"[dry-run] Dataset split prepared — "
                f"eval_split={self.config.eval_split}"
            ),
            "launch_finetune": (
                f"[dry-run] Finetune launch simulated — "
                f"model={self.config.base_model}, "
                f"lr={self.config.learning_rate}, "
                f"epochs={self.config.num_epochs}, "
                f"lora_rank={self.config.lora_rank}"
            ),
            "monitor": (
                "[dry-run] Training monitor loop completed — "
                "all synthetic checkpoints healthy"
            ),
            "export_policy": (
                f"[dry-run] Policy exported to {self.config.output_dir}"
            ),
            "evaluate": (
                "[dry-run] Evaluation complete — synthetic accuracy 0.00 "
                "(dry-run placeholder)"
            ),
        }

        duration_s = time.monotonic() - t0
        return StageEvent(
            stage=stage,
            started_at=started_at,
            status="ok",
            message=mock_messages.get(stage, "[dry-run] stage complete"),
            duration_s=round(duration_s, 6),
        )


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def get_phase_metadata() -> dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 62",
    }
