"""Pydantic models for the snapshot subsystem."""
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime


class SnapshotLayer(BaseModel):
    """A single USD layer captured within a snapshot.

    Attributes:
        layer_identifier: Original layer identifier as seen by the USD stage.
        layer_path: Relative path within the stage layer stack.
        snapshot_path: Absolute path on disk where the USDA text was written.
        format: ``"usda"`` | ``"usdc"`` — always ``"usda"`` for text captures.
    """
    layer_identifier: str
    layer_path: str
    snapshot_path: str
    format: str

class SnapshotFile(BaseModel):
    """A non-USD auxiliary file captured alongside the stage snapshot.

    Attributes:
        original_path: Path on disk at capture time.
        snapshot_path: Path where the file was copied inside the snapshot directory.
        file_type: MIME type or extension tag.
        checksum: SHA-256 hex digest for integrity verification.
    """
    original_path: str
    snapshot_path: str
    file_type: str
    checksum: str

class Snapshot(BaseModel):
    """Complete snapshot of a USD stage at a point in time.

    Attributes:
        snapshot_id: Short (8-hex) unique identifier.
        created_at: UTC timestamp of snapshot creation.
        trigger: What caused the snapshot (e.g. ``"pre_patch"``).
        action_context: Human-readable description of the pending action.
        patch_plan_id: ID of the patch plan this snapshot precedes.
        user_note: Optional operator comment.
        tags: Free-form labels for filtering in the UI.
        fingerprint_summary: Machine fingerprint dict at capture time.
        layers: USD layers captured.
        files: Auxiliary files captured.
        settings: Config key-value pairs at capture time.
        validation_baseline: Validation findings at capture time.
        storage_path: Absolute path of the snapshot directory on disk.
        size_bytes: Total disk usage (0 when not computed).
    """
    snapshot_id: str
    created_at: datetime
    trigger: str
    action_context: Optional[str] = None
    patch_plan_id: Optional[str] = None
    user_note: Optional[str] = None
    tags: List[str] = []
    
    fingerprint_summary: Dict[str, str] = {}
    
    layers: List[SnapshotLayer] = []
    files: List[SnapshotFile] = []
    settings: Dict[str, Any] = {}
    validation_baseline: Optional[Dict[str, Any]] = None
    
    storage_path: str
    size_bytes: int

class SnapshotInitRequest(BaseModel):
    """Request body for ``POST /snapshots`` — creates a new snapshot.

    ``raw_usd_data`` carries USD layer text from the UI extension because
    the FastAPI service cannot access the Omniverse Kit SDK directly.
    """
    trigger: str
    action_context: str
    patch_plan_id: Optional[str] = None
    user_note: Optional[str] = None
    
    # Passing raw file streams instead of paths because the 
    # FastAPI service cannot access Omniverse SDK natively yet.
    raw_usd_data: Optional[Dict[str, str]] = None 
    include_settings: bool = False
    include_validation: bool = False
