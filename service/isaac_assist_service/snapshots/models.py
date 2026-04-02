from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class SnapshotLayer(BaseModel):
    layer_identifier: str
    layer_path: str
    snapshot_path: str
    format: str

class SnapshotFile(BaseModel):
    original_path: str
    snapshot_path: str
    file_type: str
    checksum: str

class Snapshot(BaseModel):
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
    """ Used by the UI to request a new snapshot creation """
    trigger: str
    action_context: str
    patch_plan_id: Optional[str] = None
    user_note: Optional[str] = None
    
    # Passing raw file streams instead of paths because the 
    # FastAPI service cannot access Omniverse SDK natively yet.
    raw_usd_data: Optional[Dict[str, str]] = None 
    include_settings: bool = False
    include_validation: bool = False
