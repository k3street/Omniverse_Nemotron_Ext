from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from .orchestrator import AnalysisOrchestrator

router = APIRouter()
orchestrator = AnalysisOrchestrator()

@router.post("/run")
def run_analysis(stage_data: Dict[str, Any]):
    """
    Receives JSON-serialized viewport stage sublayers directly from 
    the native Omniverse process and runs structural heuristics against it.
    """
    try:
        # Run validations
        result = orchestrator.run_analysis(stage_data)
        return result.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
@router.get("/packs")
def list_packs():
    """ Enumerate loaded validation packs """
    packs = {}
    for rule in orchestrator.rules:
        if rule.pack not in packs:
            packs[rule.pack] = 0
        packs[rule.pack] += 1
        
    return {
        "packs": [{"name": p, "rule_count": c, "enabled": True} for p, c in packs.items()]
    }
