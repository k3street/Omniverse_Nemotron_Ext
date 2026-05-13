"""Phase 29 — tests for stage_event_listener.

Gate: event filter classifies prim_add/prim_remove/prim_transform,
      debounce coalesces rapid events.
"""
from __future__ import annotations

import sys
import time
from dataclasses import fields
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Path injection — exts/isaac_6.0/ uses a dot in the dir name, so the
# standard package import path won't resolve it.  We insert the ui/ directory
# directly so `import stage_event_listener` works without Kit present.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_UI_PATH = (
    _REPO_ROOT
    / "exts"
    / "isaac_6.0"
    / "omni.isaac.assist"
    / "omni"
    / "isaac"
    / "assist"
    / "ui"
)
if str(_UI_PATH) not in sys.path:
    sys.path.insert(0, str(_UI_PATH))

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from stage_event_listener import (  # type: ignore[import]
    PHASE_STATUS,
    DebouncedEventDispatcher,
    StageEvent,
    StageEventClassifier,
    coalesce_events,
    expected_event_types,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# 1. Phase metadata
# ---------------------------------------------------------------------------

def test_phase_metadata_shape():
    meta = get_phase_metadata()
    assert meta["phase"] == "29"
    assert meta["status"] == "landed"
    assert meta["agent_type"] == "sonnet-bounded"
    assert "gate" in meta
    assert isinstance(meta["files"], list)
    assert len(meta["files"]) == 2


def test_phase_status_constant():
    assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# 2. expected_event_types
# ---------------------------------------------------------------------------

def test_expected_event_types_count():
    types = expected_event_types()
    assert len(types) >= 5


def test_expected_event_types_contains_all():
    types = expected_event_types()
    for required in (
        "prim_added",
        "prim_removed",
        "prim_transformed",
        "attribute_changed",
        "metadata_changed",
    ):
        assert required in types, f"Missing event type: {required}"


# ---------------------------------------------------------------------------
# 3. StageEvent dataclass
# ---------------------------------------------------------------------------

def test_stage_event_fields():
    field_names = {f.name for f in fields(StageEvent)}
    assert {"event_type", "prim_path", "timestamp", "source"} <= field_names


def test_stage_event_default_source():
    evt = StageEvent(
        event_type="prim_added",
        prim_path="/World/Layout/Box",
        timestamp=0.0,
    )
    assert evt.source == "Sdf.Notice"


def test_stage_event_custom_source():
    evt = StageEvent(
        event_type="attribute_changed",
        prim_path="/World/Layout/Box",
        timestamp=1.0,
        source="custom_source",
    )
    assert evt.source == "custom_source"


# ---------------------------------------------------------------------------
# 4. StageEventClassifier — watch filter
# ---------------------------------------------------------------------------

def test_classifier_ignores_prims_outside_watch_path():
    clf = StageEventClassifier(watch_path_prefix="/World/Layout")
    result = clf.classify("ObjectsChanged", "/World/Other/Box", has_new_value=True)
    assert result is None


def test_classifier_is_watched_true():
    clf = StageEventClassifier(watch_path_prefix="/World/Layout")
    assert clf.is_watched("/World/Layout/Robot") is True


def test_classifier_is_watched_false():
    clf = StageEventClassifier(watch_path_prefix="/World/Layout")
    assert clf.is_watched("/World/Other/Box") is False


def test_classifier_custom_prefix():
    clf = StageEventClassifier(watch_path_prefix="/Env/Robots")
    # Inside prefix → classify
    assert clf.is_watched("/Env/Robots/Arm") is True
    # Root prefix itself
    assert clf.is_watched("/Env/Robots") is True
    # Outside prefix
    assert clf.is_watched("/World/Layout/Box") is False


# ---------------------------------------------------------------------------
# 5. StageEventClassifier — ObjectsChanged classification
# ---------------------------------------------------------------------------

def test_classifier_prim_added_no_old_has_new():
    clf = StageEventClassifier()
    evt = clf.classify(
        "ObjectsChanged",
        "/World/Layout/NewBox",
        has_old_value=False,
        has_new_value=True,
    )
    assert evt is not None
    assert evt.event_type == "prim_added"
    assert evt.prim_path == "/World/Layout/NewBox"


def test_classifier_prim_removed_has_old_no_new():
    clf = StageEventClassifier()
    evt = clf.classify(
        "ObjectsChanged",
        "/World/Layout/OldBox",
        has_old_value=True,
        has_new_value=False,
    )
    assert evt is not None
    assert evt.event_type == "prim_removed"


def test_classifier_prim_transformed_has_old_has_new():
    clf = StageEventClassifier()
    evt = clf.classify(
        "ObjectsChanged",
        "/World/Layout/MovedBox",
        has_old_value=True,
        has_new_value=True,
    )
    assert evt is not None
    assert evt.event_type == "prim_transformed"


def test_classifier_objects_changed_neither_old_nor_new_is_transform():
    """Both False → prim_transformed (catch-all branch)."""
    clf = StageEventClassifier()
    evt = clf.classify(
        "ObjectsChanged",
        "/World/Layout/Box",
        has_old_value=False,
        has_new_value=False,
    )
    assert evt is not None
    assert evt.event_type == "prim_transformed"


# ---------------------------------------------------------------------------
# 6. StageEventClassifier — named notice types
# ---------------------------------------------------------------------------

def test_classifier_attribute_changed():
    clf = StageEventClassifier()
    evt = clf.classify("AttributeValueChanged", "/World/Layout/Box")
    assert evt is not None
    assert evt.event_type == "attribute_changed"


def test_classifier_metadata_changed():
    clf = StageEventClassifier()
    evt = clf.classify("MetadataChanged", "/World/Layout/Box")
    assert evt is not None
    assert evt.event_type == "metadata_changed"


def test_classifier_unknown_notice_type_returns_none():
    clf = StageEventClassifier()
    evt = clf.classify("SomeUnknownNotice", "/World/Layout/Box")
    assert evt is None


# ---------------------------------------------------------------------------
# 7. DebouncedEventDispatcher — basic queue mechanics
# ---------------------------------------------------------------------------

def _make_event(etype="prim_added", path="/World/Layout/Box", ts=None) -> StageEvent:
    return StageEvent(
        event_type=etype,
        prim_path=path,
        timestamp=ts if ts is not None else time.time(),
    )


def test_dispatcher_enqueue_pending_count():
    d = DebouncedEventDispatcher(debounce_ms=200)
    assert d.pending_count() == 0
    d.enqueue(_make_event())
    assert d.pending_count() == 1
    d.enqueue(_make_event())
    assert d.pending_count() == 2


def test_dispatcher_flush_clears_queue():
    dispatched: list = []
    d = DebouncedEventDispatcher(
        debounce_ms=200,
        on_dispatch=dispatched.extend,
    )
    d.enqueue(_make_event())
    d.flush()
    assert d.pending_count() == 0


def test_dispatcher_flush_calls_on_dispatch():
    received: list = []
    d = DebouncedEventDispatcher(
        debounce_ms=0,
        on_dispatch=lambda evts: received.extend(evts),
    )
    d.enqueue(_make_event(path="/World/Layout/A"))
    d.enqueue(_make_event(path="/World/Layout/B"))
    count = d.flush()
    assert count == 2  # 2 distinct paths → no coalescing
    assert len(received) == 2


def test_dispatcher_flush_empty_returns_zero():
    d = DebouncedEventDispatcher(debounce_ms=200)
    assert d.flush() == 0


# ---------------------------------------------------------------------------
# 8. DebouncedEventDispatcher — should_flush debounce logic
# ---------------------------------------------------------------------------

def test_dispatcher_should_flush_false_when_empty():
    d = DebouncedEventDispatcher(debounce_ms=200)
    assert d.should_flush() is False


def test_dispatcher_should_flush_false_when_too_recent():
    d = DebouncedEventDispatcher(debounce_ms=5000)  # 5 s
    d.enqueue(_make_event(ts=time.time()))
    # Immediately after — oldest event is fresh
    assert d.should_flush(now=time.time()) is False


def test_dispatcher_should_flush_true_when_old():
    d = DebouncedEventDispatcher(debounce_ms=200)
    # Event timestamped 1 second ago
    old_ts = time.time() - 1.0
    d.enqueue(_make_event(ts=old_ts))
    assert d.should_flush(now=time.time()) is True


# ---------------------------------------------------------------------------
# 9. coalesce_events — add + remove sequence → net no-op
# ---------------------------------------------------------------------------

def test_coalesce_add_then_remove_drops_both():
    events = [
        StageEvent("prim_added",   "/World/Layout/X", timestamp=1.0),
        StageEvent("prim_removed", "/World/Layout/X", timestamp=2.0),
    ]
    result = coalesce_events(events)
    paths = [e.prim_path for e in result]
    assert "/World/Layout/X" not in paths


def test_coalesce_remove_then_add_drops_both():
    """Add before remove OR remove before add — both patterns are dropped."""
    events = [
        StageEvent("prim_removed", "/World/Layout/X", timestamp=1.0),
        StageEvent("prim_added",   "/World/Layout/X", timestamp=2.0),
    ]
    result = coalesce_events(events)
    paths = [e.prim_path for e in result]
    assert "/World/Layout/X" not in paths


# ---------------------------------------------------------------------------
# 10. coalesce_events — multiple transforms → keep only last
# ---------------------------------------------------------------------------

def test_coalesce_multiple_transforms_keeps_last():
    events = [
        StageEvent("prim_transformed", "/World/Layout/Box", timestamp=1.0),
        StageEvent("prim_transformed", "/World/Layout/Box", timestamp=2.0),
        StageEvent("prim_transformed", "/World/Layout/Box", timestamp=3.0),
    ]
    result = coalesce_events(events)
    box_events = [e for e in result if e.prim_path == "/World/Layout/Box"]
    assert len(box_events) == 1
    assert box_events[0].timestamp == 3.0


# ---------------------------------------------------------------------------
# 11. coalesce_events — multiple attr_changed → keep only last
# ---------------------------------------------------------------------------

def test_coalesce_multiple_attr_changed_keeps_last():
    events = [
        StageEvent("attribute_changed", "/World/Layout/Robot", timestamp=1.0),
        StageEvent("attribute_changed", "/World/Layout/Robot", timestamp=2.0),
    ]
    result = coalesce_events(events)
    robot_events = [e for e in result if e.prim_path == "/World/Layout/Robot"]
    assert len(robot_events) == 1
    assert robot_events[0].timestamp == 2.0


# ---------------------------------------------------------------------------
# 12. coalesce_events — preserves order across different prims
# ---------------------------------------------------------------------------

def test_coalesce_preserves_cross_prim_order():
    events = [
        StageEvent("prim_added",       "/World/Layout/A", timestamp=1.0),
        StageEvent("prim_transformed", "/World/Layout/B", timestamp=2.0),
        StageEvent("attribute_changed","/World/Layout/C", timestamp=3.0),
    ]
    result = coalesce_events(events)
    # All 3 prims present, in timestamp order
    assert len(result) == 3
    assert result[0].prim_path == "/World/Layout/A"
    assert result[1].prim_path == "/World/Layout/B"
    assert result[2].prim_path == "/World/Layout/C"


# ---------------------------------------------------------------------------
# 13. coalesce_events — unaffected paths pass through unchanged
# ---------------------------------------------------------------------------

def test_coalesce_passthrough_add_only():
    events = [
        StageEvent("prim_added", "/World/Layout/NewPrim", timestamp=1.0),
    ]
    result = coalesce_events(events)
    assert len(result) == 1
    assert result[0].event_type == "prim_added"


def test_coalesce_mixed_paths_cancel_only_matching():
    """Cancel pair on path X; path Y should survive."""
    events = [
        StageEvent("prim_added",   "/World/Layout/X", timestamp=1.0),
        StageEvent("prim_added",   "/World/Layout/Y", timestamp=2.0),
        StageEvent("prim_removed", "/World/Layout/X", timestamp=3.0),
    ]
    result = coalesce_events(events)
    paths = [e.prim_path for e in result]
    assert "/World/Layout/X" not in paths
    assert "/World/Layout/Y" in paths
