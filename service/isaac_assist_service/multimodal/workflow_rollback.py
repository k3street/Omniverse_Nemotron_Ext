"""Phase 41 — workflow rollback via stage snapshot.

When a workflow checkpoint is rejected, the corresponding stage
snapshot is restored, putting Kit back in the known-good state.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 41.
"""
from typing import Any, Dict, Optional


def rollback_workflow(workflow_record: Dict[str, Any],
                       checkpoint_id: str,
                       snapshot_loader=None) -> Dict[str, Any]:
    """Restore workflow to a checkpoint state. Optionally restore stage."""
    checkpoints = workflow_record.get("checkpoint_decisions", [])
    cp: Optional[Dict[str, Any]] = None
    for c in checkpoints:
        if c.get("checkpoint_id") == checkpoint_id:
            cp = c
            break
    if cp is None:
        return {"rolled_back": False, "reason": f"checkpoint {checkpoint_id} not found"}
    snapshot_id = cp.get("snapshot_id")
    snapshot_status = "no_snapshot"
    if snapshot_id and snapshot_loader is not None:
        snapshot_status = "restored" if snapshot_loader(snapshot_id) else "load_failed"
    return {
        "rolled_back": True,
        "checkpoint_id": checkpoint_id,
        "snapshot_id": snapshot_id,
        "snapshot_status": snapshot_status,
    }
