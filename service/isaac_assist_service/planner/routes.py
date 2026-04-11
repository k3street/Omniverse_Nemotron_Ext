from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from .models import PlanGenerationRequest
from .swarm_generator import SwarmPlanGenerator

router = APIRouter()
generator = SwarmPlanGenerator()

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

@router.post("/{plan_id}/apply")
def notify_applied(plan_id: str):
    """ Called by the UI extension once it finishes translating the patch to pxr limits """
    return {"status": "success", "snapshot_id": "auto_cached"}
