"""Snapshot API routes — create, list, and rollback USD stage snapshots.

Exposes three endpoints under the ``/snapshots`` prefix:

- ``POST /`` — capture the current USD layer state to disk (called by the Kit
  extension UI before any LLM-generated code patch is executed).
- ``GET /`` — return a reverse-chronological list of all persisted snapshots.
- ``POST /{snapshot_id}/rollback`` — retrieve a snapshot's stored USDA payloads
  so the UI can reinject them into the live stage.
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from .models import SnapshotInitRequest
from .manager import SnapshotManager
from ..fingerprint.collector import collect_fingerprint

router = APIRouter()
snap_manager = SnapshotManager()

@router.post("")
def create_snapshot(req: SnapshotInitRequest):
    """Persist the current USD layer state to disk as a named snapshot.

    Collects an environment fingerprint via ``collect_fingerprint()`` and
    writes all raw USD layer strings from ``req`` to
    ``workspace/snapshots/{ts}_{id}/layers/``, along with a
    ``manifest.json``.  Called by the Kit extension UI before the LLM is
    allowed to execute a patch.

    Args:
        req (SnapshotInitRequest): Snapshot request containing ``trigger``,
            ``action_context``, ``raw_usd_data``, and optional metadata.

    Returns:
        dict: Serialized ``Snapshot`` model as a flat dict.
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
    """Return all persisted snapshot manifests, newest first.

    Returns:
        dict: ``{snapshots, total}`` where ``snapshots`` is a list of manifest
        dicts and ``total`` is the count.
    """
    try:
        return {"snapshots": snap_manager.list_snapshots(), "total": len(snap_manager.list_snapshots())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{snapshot_id}/rollback")
def execute_rollback(snapshot_id: str):
    """Return the stored USDA layer payloads for a snapshot so the UI can restore them.

    MVP implementation: reads the USDA files back from disk and instructs
    the frontend to reload them.  True pxr-level layer splicing requires
    the Omni Kit runtime and is deferred to a future release.

    Args:
        snapshot_id (str): Short hex ID of the snapshot to restore.

    Returns:
        dict: ``{status, action, usd_payloads}`` where ``usd_payloads`` is a
        ``{layer_identifier: usda_text}`` dict; raises HTTP 404 if the snapshot
        has been pruned or the ID is unknown.
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
