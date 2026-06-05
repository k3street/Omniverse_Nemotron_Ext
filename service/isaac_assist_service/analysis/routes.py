"""Stage analysis API routes.

Exposes two endpoints under the ``/analysis`` prefix:

- ``POST /run`` — accept serialized stage data from the UI extension and return
  a full ``StageAnalysisResult`` with all validator findings.
- ``GET /packs`` — list the currently enabled validation packs and their rule counts.
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from .orchestrator import AnalysisOrchestrator

router = APIRouter()
orchestrator = AnalysisOrchestrator()

@router.post("/run")
def run_analysis(stage_data: Dict[str, Any]):
    """Run all registered validators against the supplied stage data.

    Receives JSON-serialized viewport stage data directly from the native
    Omniverse process and returns the aggregated ``StageAnalysisResult``.

    Args:
        stage_data (dict): Serialized stage data including ``prims``,
            ``sublayer_count``, ``omnigraph_nodes``, etc.

    Returns:
        dict: Serialized ``StageAnalysisResult`` with findings, severity counts,
        prim type histogram, and wall-clock duration.

    Raises:
        HTTPException: 500 if any validator raises an unhandled exception.
    """
    try:
        # Run validations
        result = orchestrator.run_analysis(stage_data)
        return result.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/packs")
def list_packs():
    """List all currently enabled validator packs.

    Returns:
        dict: ``{"packs": [{name, rule_count, enabled}]}`` for each registered pack.
    """
    packs = {}
    for rule in orchestrator.rules:
        if rule.pack not in packs:
            packs[rule.pack] = 0
        packs[rule.pack] += 1

    return {
        "packs": [{"name": p, "rule_count": c, "enabled": True} for p, c in packs.items()]
    }
