from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from .models import PlanGenerationRequest
from .swarm_generator import SwarmPlanGenerator
from ..knowledge.knowledge_base import KnowledgeBase
from ..retrieval.context_retriever import detect_isaac_version
import logging

logger = logging.getLogger(__name__)

router = APIRouter()
generator = SwarmPlanGenerator()
_kb = KnowledgeBase()

@router.post("/generate")
async def generate_plan(req: PlanGenerationRequest, mock_findings: List[Dict[str, Any]] = []):
    """
    Consumes findings and outputs a structured Patch Plan mapping out the USD 
    writes to fix the scene, now routed through the full Critic/QA agent swarm!
    """
    try:
        plan = await generator.generate_plan_async(req, mock_findings)
        return plan.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PlanOutcomeRequest(BaseModel):
    plan_id: str
    success: bool
    error_output: str = ""
    code: str = ""
    user_message: str = ""
    steps: List[Dict[str, Any]] = []


@router.post("/{plan_id}/apply")
def notify_applied(plan_id: str, req: Optional[PlanOutcomeRequest] = None):
    """
    Called by the UI extension once it finishes translating the patch to pxr limits.
    Now also captures the outcome in the knowledge base for learning.
    """
    if req and req.user_message:
        version = detect_isaac_version()
        try:
            _kb.capture_plan_outcome(
                version=version,
                user_message=req.user_message,
                plan_steps=req.steps,
                success=req.success,
                error_output=req.error_output,
                code=req.code,
            )
            logger.info(
                f"[planner] Plan {plan_id} outcome captured: "
                f"{'SUCCESS' if req.success else 'FAIL'}"
            )
        except Exception as e:
            logger.warning(f"[planner] Failed to capture plan outcome: {e}")

    return {"status": "success", "snapshot_id": "auto_cached"}
