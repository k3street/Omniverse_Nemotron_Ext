"""Remote scale provider capability notices.

These helpers are intentionally side-effect free. They do not launch cloud
capacity or touch IsaacAutomator; they only report whether the user has
configured a provider that Isaac Assist may suggest for expensive jobs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


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


def _clean_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def cosmos_reasoner_status(config: Any) -> Dict[str, Any]:
    """Describe the configured Cosmos 3 reasoner endpoint.

    This is intentionally a static configuration readback. It does not probe
    the endpoint, start NIM, or imply permission to use remote capacity.
    """

    dgx_url = _clean_base_url(str(getattr(config, "dgx_spark_cosmos_base_url", "") or ""))
    reasoner_url = _clean_base_url(str(getattr(config, "cosmos3_reasoner_base_url", "") or ""))
    model = str(getattr(config, "cosmos3_reasoner_model", "") or "nvidia/cosmos3-nano-reasoner")
    mode = str(getattr(config, "cosmos3_mode", "") or "disabled")
    gemini_fallback_configured = bool(
        getattr(config, "gemini_robotics_er_fallback", False)
        and str(getattr(config, "api_key_gemini", "") or "").strip()
        and str(getattr(config, "gemini_robotics_er_model", "") or "").strip()
    )

    provider = ""
    base_url = ""
    if dgx_url:
        provider = "dgx_spark"
        base_url = dgx_url
    elif reasoner_url:
        provider = "cosmos3_reasoner"
        base_url = reasoner_url

    configured = bool(base_url or gemini_fallback_configured)
    if configured:
        if base_url:
            locality = "local workstation" if _is_loopback_url(base_url) else "remote endpoint"
            message = f"Cosmos 3 Reasoner is configured via {provider} on a {locality}."
        else:
            provider = "gemini_robotics_er"
            model = str(getattr(config, "gemini_robotics_er_model", "") or model)
            message = (
                "Cosmos 3 Reasoner endpoint is not configured; Gemini Robotics-ER "
                "fallback is configured for scene observation."
            )
    elif mode and mode != "disabled":
        message = "Cosmos 3 mode is enabled, but no reasoner endpoint is configured."
    else:
        message = "Cosmos 3 Reasoner is not configured."

    return {
        "configured": configured,
        "provider": provider,
        "mode": mode,
        "base_url": base_url,
        "model": model,
        "is_loopback": _is_loopback_url(base_url) if base_url else False,
        "health_url": f"{base_url}/v1/health/live" if base_url else "",
        "models_url": f"{base_url}/v1/models" if base_url else "",
        "fallback_provider": "gemini_robotics_er",
        "fallback_configured": gemini_fallback_configured,
        "requires_user_approval": True,
        "message": message,
    }


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
    "cosmos_reasoner_status",
    "dgx_spark_configured",
    "isaac_automator_configured",
    "scale_provider_notice",
]
