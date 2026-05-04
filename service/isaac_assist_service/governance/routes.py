import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .models import GovernanceConfig, AuditEntry
from .policy_engine import PolicyEngine
from .secret_redactor import SecretRedactor
from .audit_log import AuditLogger
from ..planner.models import PatchAction

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
    """
    Evaluates a list of PatchActions to determine risk level and if human approval is required.
    """
    try:
        evaluation = policy_engine.evaluate_plan(req.actions)
        return {"status": "success", "evaluation": evaluation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/audit")
async def log_audit(req: AuditRequest):
    """
    Permanently logs an audit entry.
    """
    success = audit_logger.log_entry(req.entry)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to write to audit log.")
    return {"status": "success"}

@router.get("/audit_logs")
async def get_audit_logs(limit: int = 100, event_type: str = None):
    """
    Retrieves recent audit logs.
    """
    entries = audit_logger.query_logs(limit=limit, event_type=event_type)
    return {"status": "success", "logs": entries}

class TextRedactRequest(BaseModel):
    text: str

@router.post("/redact")
async def redact_text(req: TextRedactRequest):
    """
    Redacts secrets from a given text.
    """
    redacted = redactor.redact_text(req.text)
    return {"status": "success", "redacted_text": redacted}
