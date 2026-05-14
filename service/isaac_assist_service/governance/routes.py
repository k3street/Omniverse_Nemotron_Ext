"""Governance API routes — risk evaluation, audit log, and secret redaction.

Exposes four endpoints under the ``/governance`` prefix:

- ``POST /evaluate`` — assess risk level of a list of ``PatchAction`` objects
  and return per-action risk tiers plus an overall ``requires_approval`` flag.
- ``POST /audit`` — append a single ``AuditEntry`` to the JSONL audit trail.
- ``GET /audit_logs`` — return recent audit entries, optionally filtered by
  ``event_type``.
- ``POST /redact`` — scrub credential-shaped strings from arbitrary text using
  the full ``SecretRedactor`` pattern set.
"""
import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .models import GovernanceConfig, AuditEntry
from .policy_engine import PolicyEngine
from .secret_redactor import SecretRedactor
from .audit_log import AuditLogger
from service.isaac_assist_service.planner.models import PatchAction

router = APIRouter()

# Global instances based on default config for MVP
config = GovernanceConfig()
policy_engine = PolicyEngine(config)
redactor = SecretRedactor(config)
audit_logger = AuditLogger(config.audit_log_path)

class EvaluationRequest(BaseModel):
    actions: List[PatchAction]

class AuditRequest(BaseModel):
    entry: AuditEntry

@router.post("/evaluate")
async def evaluate_actions(req: EvaluationRequest):
    """Evaluate a list of PatchActions and return aggregated risk assessment.

    Args:
        req (EvaluationRequest): Request body containing ``actions``.

    Returns:
        dict: ``{status, evaluation}`` where ``evaluation`` contains
        ``overall_risk``, ``requires_approval``, and per-action
        ``action_evaluations``.
    """
    try:
        evaluation = policy_engine.evaluate_plan(req.actions)
        return {"status": "success", "evaluation": evaluation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/audit")
async def log_audit(req: AuditRequest):
    """Append one governance event to the append-only JSONL audit file.

    Args:
        req (AuditRequest): Request body containing the ``AuditEntry`` to persist.

    Returns:
        dict: ``{status: "success"}`` on success; raises HTTP 500 on write failure.
    """
    success = audit_logger.log_entry(req.entry)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to write to audit log.")
    return {"status": "success"}

@router.get("/audit_logs")
async def get_audit_logs(limit: int = 100, event_type: str = None):
    """Return recent audit log entries, newest first.

    Args:
        limit (int, optional): Maximum number of entries to return. Defaults to 100.
        event_type (str, optional): Filter to a specific event type string,
            e.g. ``"patch_approved"``.  Returns all types when omitted.

    Returns:
        dict: ``{status, logs}`` where ``logs`` is a list of ``AuditEntry`` dicts.
    """
    entries = audit_logger.query_logs(limit=limit, event_type=event_type)
    return {"status": "success", "logs": entries}

class TextRedactRequest(BaseModel):
    text: str

@router.post("/redact")
async def redact_text(req: TextRedactRequest):
    """Scrub credential patterns from the provided text string.

    Runs the full ``SecretRedactor`` pattern set (config-driven +
    always-on extended set) against ``req.text``.

    Args:
        req (TextRedactRequest): Request body with a ``text`` field.

    Returns:
        dict: ``{status, redacted_text}`` with every matched secret replaced by
        ``[REDACTED_SECRET]``.
    """
    redacted = redactor.redact_text(req.text)
    return {"status": "success", "redacted_text": redacted}
