import os
import glob
import uuid
import json
import logging
from datetime import datetime
from typing import Dict, Any, List
from .models import Snapshot, SnapshotInitRequest

logger = logging.getLogger(__name__)

# We use the local workspace/snapshots structure to prevent 
# littering the user's hidden OS files, and keeping the repo cleanly manageable.
SNAPSHOT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "workspace", "snapshots")
MAX_SNAPSHOTS = 50

class SnapshotManager:
    def __init__(self):
        os.makedirs(SNAPSHOT_ROOT, exist_ok=True)
    
    def _prune_old_snapshots(self):
        """ Hard caps the directory to MAX_SNAPSHOTS by age. """
        dirs = sorted([os.path.join(SNAPSHOT_ROOT, d) for d in os.listdir(SNAPSHOT_ROOT) 
                      if os.path.isdir(os.path.join(SNAPSHOT_ROOT, d))], key=os.path.getctime)
        while len(dirs) >= MAX_SNAPSHOTS:
            oldest = dirs.pop(0)
            logger.info(f"Pruning old snapshot to save disk space: {oldest}")
            import shutil
            shutil.rmtree(oldest, ignore_errors=True)

    def create_snapshot(self, req: SnapshotInitRequest, fingerprint_summary: Dict[str, str]) -> Snapshot:
        self._prune_old_snapshots()
        
        short_id = uuid.uuid4().hex[:8]
        ts_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dir_name = f"{ts_str}_{short_id}"
        snap_path = os.path.join(SNAPSHOT_ROOT, dir_name)
        
        os.makedirs(snap_path, exist_ok=True)
        os.makedirs(os.path.join(snap_path, "layers"), exist_ok=True)
        
        # 1. Drain the Raw USD strings passed from the UI
        layer_models = []
        if req.raw_usd_data:
            for layer_name, usda_string in req.raw_usd_data.items():
                safe_name = os.path.basename(layer_name)
                if not safe_name.endswith(".usda"):
                    safe_name += ".usda"
                    
                full_layer_path = os.path.join(snap_path, "layers", safe_name)
                with open(full_layer_path, "w") as f:
                    f.write(usda_string)
                    
                layer_models.append({
                    "layer_identifier": layer_name,
                    "layer_path": layer_name,
                    "snapshot_path": full_layer_path,
                    "format": "usda"
                })
        
        # 2. Build the Model
        snap = Snapshot(
            snapshot_id=short_id,
            created_at=datetime.utcnow(),
            trigger=req.trigger,
            action_context=req.action_context,
            patch_plan_id=req.patch_plan_id,
            user_note=req.user_note,
            fingerprint_summary=fingerprint_summary,
            layers=layer_models,
            storage_path=snap_path,
            size_bytes=0 # Skipping size calculation for MVP
        )
        
        # 3. Write manifest
        manifest_path = os.path.join(snap_path, "manifest.json")
        with open(manifest_path, "w") as f:
            f.write(snap.model_dump_json(indent=2))
            
        return snap
        
    def list_snapshots(self) -> List[Dict[str, Any]]:
        """ Retrieves all valid snapshots from disk """
        results = []
        for d in os.listdir(SNAPSHOT_ROOT):
            manifest_path = os.path.join(SNAPSHOT_ROOT, d, "manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r") as f:
                        results.append(json.load(f))
                except Exception as e:
                    logger.error(f"Corrupted manifest at {manifest_path}: {e}")
        # Sort descending by date
        return sorted(results, key=lambda x: x.get("created_at", ""), reverse=True)
