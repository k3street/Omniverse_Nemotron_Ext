from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from .models import SnapshotInitRequest
from .manager import SnapshotManager
from ..fingerprint.collector import collect_fingerprint

router = APIRouter()
snap_manager = SnapshotManager()

@router.post("")
def create_snapshot(req: SnapshotInitRequest):
    """
    Called by the UI Extension right before it allows the LLM 
    to execute a python patch against the active Isaac Scene.
    """
    try:
        # 1. Grab current environment state so we know exactly 
        # what build generated the faulty USD file.
        fp = collect_fingerprint()
        
        # 2. Serialize and drop to disk
        snapshot = snap_manager.create_snapshot(req, fingerprint_summary=fp)
        return snapshot.model_dump()
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("")
def list_snapshots():
    """ Returns chronology of all saved states for UI history window. """
    try:
        return {"snapshots": snap_manager.list_snapshots(), "total": len(snap_manager.list_snapshots())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{snapshot_id}/rollback")
def execute_rollback(snapshot_id: str):
    """ 
    MVP Rollback: Tell the Frontend to reload the USDA texts natively.
    True disk-based `pxr` layer splicing requires deep OMni Kit runtime.
    For now, returning the cached layer payloads lets the UI inject them back.
    """
    # Look up snapshot
    manifests = snap_manager.list_snapshots()
    target = next((m for m in manifests if m.get("snapshot_id") == snapshot_id), None)
    
    if not target:
        raise HTTPException(status_code=404, detail="Snapshot not found on disk or pruned.")
        
    # Read the layer contents back off disk
    restored_layers = {}
    for layer in target.get("layers", []):
        try:
            with open(layer["snapshot_path"], "r") as f:
                restored_layers[layer["layer_identifier"]] = f.read()
        except:
            pass
            
    return {
        "status": "success",
        "action": "instruct_ui_to_reload", 
        "usd_payloads": restored_layers
    }
