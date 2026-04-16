"""
L0 tests for the SnapshotManager.
Uses tmp_path via the snapshot_manager fixture.
"""
import os
import json
import pytest
from datetime import datetime

pytestmark = pytest.mark.l0

from service.isaac_assist_service.snapshots.models import SnapshotInitRequest
from service.isaac_assist_service.snapshots.manager import MAX_SNAPSHOTS


class TestCreateSnapshot:

    def test_creates_manifest_on_disk(self, snapshot_manager, tmp_path):
        req = SnapshotInitRequest(
            trigger="pre_patch",
            action_context="test action",
        )
        snap = snapshot_manager.create_snapshot(req, fingerprint_summary={"os": "linux"})
        assert snap.snapshot_id
        assert snap.trigger == "pre_patch"
        assert os.path.exists(os.path.join(snap.storage_path, "manifest.json"))

    def test_stores_usd_layers(self, snapshot_manager):
        req = SnapshotInitRequest(
            trigger="pre_patch",
            action_context="test",
            raw_usd_data={"root.usda": "#usda 1.0\ndef Cube 'MyCube' {}"},
        )
        snap = snapshot_manager.create_snapshot(req, fingerprint_summary={})
        assert len(snap.layers) == 1
        layer = snap.layers[0]
        assert layer.format == "usda"
        assert os.path.exists(layer.snapshot_path)
        with open(layer.snapshot_path) as f:
            content = f.read()
        assert "MyCube" in content

    def test_snapshot_id_is_unique(self, snapshot_manager):
        req = SnapshotInitRequest(trigger="test", action_context="ctx")
        ids = set()
        for _ in range(10):
            snap = snapshot_manager.create_snapshot(req, fingerprint_summary={})
            ids.add(snap.snapshot_id)
        assert len(ids) == 10


class TestListSnapshots:

    def test_list_empty(self, snapshot_manager):
        result = snapshot_manager.list_snapshots()
        assert result == []

    def test_list_returns_created(self, snapshot_manager):
        req = SnapshotInitRequest(trigger="test", action_context="ctx")
        snapshot_manager.create_snapshot(req, fingerprint_summary={})
        snapshot_manager.create_snapshot(req, fingerprint_summary={})
        result = snapshot_manager.list_snapshots()
        assert len(result) == 2

    def test_list_sorted_descending(self, snapshot_manager):
        req = SnapshotInitRequest(trigger="test", action_context="first")
        snapshot_manager.create_snapshot(req, fingerprint_summary={})
        req2 = SnapshotInitRequest(trigger="test", action_context="second")
        snapshot_manager.create_snapshot(req2, fingerprint_summary={})
        result = snapshot_manager.list_snapshots()
        # Most recent first
        assert result[0]["action_context"] == "second"


class TestPruning:

    def test_prune_at_max(self, snapshot_manager, monkeypatch):
        """When MAX_SNAPSHOTS is reached, the oldest should be pruned."""
        import service.isaac_assist_service.snapshots.manager as sm
        monkeypatch.setattr(sm, "MAX_SNAPSHOTS", 5)

        req = SnapshotInitRequest(trigger="test", action_context="ctx")
        for _ in range(6):
            snapshot_manager.create_snapshot(req, fingerprint_summary={})

        result = snapshot_manager.list_snapshots()
        assert len(result) <= 5


class TestFingerprint:

    def test_fingerprint_stored(self, snapshot_manager):
        req = SnapshotInitRequest(trigger="test", action_context="ctx")
        fp = {"os": "linux", "isaac_version": "5.1"}
        snap = snapshot_manager.create_snapshot(req, fingerprint_summary=fp)
        assert snap.fingerprint_summary["os"] == "linux"
        assert snap.fingerprint_summary["isaac_version"] == "5.1"

        # Verify it persists to manifest
        manifest_path = os.path.join(snap.storage_path, "manifest.json")
        with open(manifest_path) as f:
            data = json.load(f)
        assert data["fingerprint_summary"]["os"] == "linux"
