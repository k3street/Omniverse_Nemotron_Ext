"""Remote scale provider capability notices.

These helpers are intentionally side-effect free. They do not launch cloud
capacity or touch IsaacAutomator; they only report whether the user has
configured a provider that Isaac Assist may suggest for expensive jobs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


HEAVY_JOB_KINDS = {
    "cosmos_reasoner",
    "cosmos_generator",
    "isaac_remote_qa",
    "isaaclab_training",
    "sdg_batch",
    "groot_finetune",
    "remote_scene_replay",
}


def isaac_automator_configured(config: Any) -> bool:
    """Return True when IsaacAutomator has enough config to be offered."""

    root = str(getattr(config, "isaac_automator_root", "") or "").strip()
    deployment = str(getattr(config, "isaac_automator_deployment", "") or "").strip()
    if not root:
        return False

    root_path = Path(root).expanduser()
    has_known_command = any(
        (root_path / name).exists()
        for name in (
            "run",
            "deploy-aws",
            "deploy-gcp",
            "deploy-azure",
            "deploy-alicloud",
        )
    )
    return bool(deployment or has_known_command)


def brev_configured(config: Any) -> bool:
    """Return True when Brev appears configured enough to mention."""

    return bool(
        str(getattr(config, "brev_api_key", "") or "").strip()
        and str(getattr(config, "brev_project_id", "") or "").strip()
    )


def dgx_spark_configured(config: Any) -> bool:
    """Return True when a DGX Spark endpoint is configured."""

    return bool(str(getattr(config, "dgx_spark_cosmos_base_url", "") or "").strip())


def scale_provider_notice(
    config: Any,
    *,
    job_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a user-facing advisory for remote capacity.

    The notice is designed for chat, settings, and extension UI surfaces. It is
    not a permission grant and it never implies Isaac Assist will launch remote
    capacity without explicit user approval.
    """

    configured: List[str] = []
    if dgx_spark_configured(config):
        configured.append("dgx_spark")
    if brev_configured(config):
        configured.append("brev")
    if isaac_automator_configured(config):
        configured.append("isaac_automator")

    preferred = str(getattr(config, "scale_provider", "local") or "local").strip()
    heavy_job = job_kind in HEAVY_JOB_KINDS if job_kind else False

    should_notify = bool(configured) and (heavy_job or preferred != "local")
    if not configured:
        message = "Remote scale providers are not configured."
    elif should_notify:
        message = (
            "Remote scale capacity is configured. For this kind of job, Isaac "
            "Assist may suggest DGX Spark, Brev, or IsaacAutomator before "
            "running expensive local work."
        )
    else:
        message = (
            "Remote scale capacity is configured and available if a heavier "
            "Cosmos, Isaac Lab, SDG, or remote QA job needs it."
        )

    return {
        "configured": configured,
        "preferred_provider": preferred,
        "job_kind": job_kind,
        "should_notify": should_notify,
        "requires_user_approval": True,
        "message": message,
    }


__all__ = [
    "HEAVY_JOB_KINDS",
    "brev_configured",
    "dgx_spark_configured",
    "isaac_automator_configured",
    "scale_provider_notice",
]
