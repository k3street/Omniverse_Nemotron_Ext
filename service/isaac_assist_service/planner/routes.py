from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from .models import PlanGenerationRequest
from .generator import PlanGenerator

router = APIRouter()
generator = PlanGenerator()

@router.post("/generate")
def generate_plan(req: PlanGenerationRequest, mock_findings: List[Dict[str, Any]] = []):
    """
    Consumes findings and outputs a structured Patch Plan mapping out the USD 
    writes to fix the scene. Contains `action_type` payloads.
    """
    try:
        plan = generator.generate_plan(req, mock_findings)
        return plan.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{plan_id}/apply")
def notify_applied(plan_id: str):
    """ Called by the UI extension once it finishes translating the patch to pxr limits """
    return {"status": "success", "snapshot_id": "auto_cached"}
