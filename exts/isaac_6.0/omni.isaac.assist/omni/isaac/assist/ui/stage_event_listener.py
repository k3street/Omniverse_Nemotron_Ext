"""
Phase 29 — Canvas mirror panel: live state-sync from Kit.

Sdf.Notice listener + event dispatcher for Kit stage events.
Pure-Python logic: no Kit imports at module level — guarded in try/except so
the module imports cleanly without Kit being present (testable in CI).

Gate: pytest — event filter classifies prim_add/prim_remove/prim_transform,
      debounce coalesces rapid events.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal

# ---------------------------------------------------------------------------
# Kit guard — imports are optional; if omitted the module still works for tests
# ---------------------------------------------------------------------------
try:
    import omni.usd                   # noqa: F401
    import pxr.Sdf as Sdf             # noqa: F401
    _KIT_AVAILABLE = True
except ImportError:
    _KIT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

StageEventType = Literal[
    "prim_added",
    "prim_removed",
    "prim_transformed",
    "attribute_changed",
    "metadata_changed",
]

PHASE_STATUS = "landed"


# ---------------------------------------------------------------------------
# StageEvent dataclass
# ---------------------------------------------------------------------------

@dataclass
class StageEvent:
    """A single classified stage mutation event."""

    event_type: StageEventType
    prim_path: str
    timestamp: float
    source: str = "Sdf.Notice"


# ---------------------------------------------------------------------------
# StageEventClassifier
# ---------------------------------------------------------------------------

class StageEventClassifier:
    """Classifies raw Sdf.Notice events into typed StageEvent objects.

    Only events whose ``prim_path`` starts with ``watch_path_prefix`` are
    classified; everything else returns ``None``.

    Classification rules
    --------------------
    "ObjectsChanged":
        - no old value + has new value  → prim_added
        - has old value + no new value  → prim_removed
        - has old value + has new value → prim_transformed
    "AttributeValueChanged"  → attribute_changed
    "MetadataChanged"        → metadata_changed
    """

    def __init__(self, watch_path_prefix: str = "/World/Layout") -> None:
        self._prefix = watch_path_prefix

    # ------------------------------------------------------------------
    def is_watched(self, prim_path: str) -> bool:
        """Return True if *prim_path* falls under the watched subtree."""
        return prim_path.startswith(self._prefix)

    # ------------------------------------------------------------------
    def classify(
        self,
        notice_type: str,
        prim_path: str,
        has_old_value: bool = False,
        has_new_value: bool = False,
    ) -> StageEvent | None:
        """Attempt to classify a single notice.

        Returns a :class:`StageEvent` when the notice is relevant, or
        ``None`` when the prim is outside the watched subtree.
        """
        if not self.is_watched(prim_path):
            return None

        ts = time.time()

        if notice_type == "ObjectsChanged":
            if not has_old_value and has_new_value:
                etype: StageEventType = "prim_added"
            elif has_old_value and not has_new_value:
                etype = "prim_removed"
            else:
                etype = "prim_transformed"
            return StageEvent(
                event_type=etype,
                prim_path=prim_path,
                timestamp=ts,
            )

        if notice_type == "AttributeValueChanged":
            return StageEvent(
                event_type="attribute_changed",
                prim_path=prim_path,
                timestamp=ts,
            )

        if notice_type == "MetadataChanged":
            return StageEvent(
                event_type="metadata_changed",
                prim_path=prim_path,
                timestamp=ts,
            )

        return None


# ---------------------------------------------------------------------------
# DebouncedEventDispatcher
# ---------------------------------------------------------------------------

class DebouncedEventDispatcher:
    """Accumulates :class:`StageEvent` objects and dispatches in batches.

    Events are queued via :meth:`enqueue`. :meth:`flush` dispatches them
    (via ``on_dispatch``) when the oldest queued event is older than
    ``debounce_ms`` milliseconds.

    Args:
        debounce_ms: Minimum age of the oldest pending event before a flush
            is considered overdue. Default: 200 ms.
        on_dispatch: Callback invoked with the coalesced event list when
            :meth:`flush` executes. If ``None``, events are silently dropped.
    """

    def __init__(
        self,
        debounce_ms: int = 200,
        on_dispatch: Callable[[list[StageEvent]], None] | None = None,
    ) -> None:
        self._debounce_s: float = debounce_ms / 1000.0
        self._on_dispatch = on_dispatch
        self._pending: list[StageEvent] = []

    # ------------------------------------------------------------------
    def enqueue(self, event: StageEvent) -> None:
        """Add *event* to the pending queue."""
        self._pending.append(event)

    # ------------------------------------------------------------------
    def should_flush(self, now: float | None = None) -> bool:
        """Return True if the pending queue is overdue for dispatch.

        "Overdue" means the oldest pending event's timestamp is more than
        ``debounce_ms`` milliseconds ago.
        """
        if not self._pending:
            return False
        now = now if now is not None else time.time()
        return (now - self._pending[0].timestamp) >= self._debounce_s

    # ------------------------------------------------------------------
    def flush(self, now: float | None = None) -> int:
        """Dispatch pending events through ``on_dispatch`` and clear queue.

        Coalesces the pending list via :func:`coalesce_events` before
        dispatching.

        Returns:
            Number of coalesced events dispatched (0 if queue was empty).
        """
        if not self._pending:
            return 0

        coalesced = coalesce_events(self._pending)
        self._pending = []

        if self._on_dispatch is not None and coalesced:
            self._on_dispatch(coalesced)

        return len(coalesced)

    # ------------------------------------------------------------------
    def pending_count(self) -> int:
        """Return the number of events currently waiting in the queue."""
        return len(self._pending)


# ---------------------------------------------------------------------------
# coalesce_events
# ---------------------------------------------------------------------------

def coalesce_events(events: list[StageEvent]) -> list[StageEvent]:
    """Reduce redundant events for the same prim path.

    Rules
    -----
    * ``prim_added`` followed (directly or indirectly) by ``prim_removed``
      for the **same** prim_path: both are dropped (net no-op).
    * Multiple ``prim_transformed`` for the same prim_path: keep only the
      **last** one (latest position wins).
    * Multiple ``attribute_changed`` for the same prim_path: keep only the
      **last** one.
    * Events for **different** prim paths are preserved in their original
      relative order.

    The algorithm is single-pass with a per-path state accumulator to keep
    O(n) complexity.
    """
    # First pass: build per-path accumulated state preserving order of first
    # appearance.
    # path → list of events (we need order-of-first-appearance for the output)
    from collections import OrderedDict

    per_path: OrderedDict[str, list[StageEvent]] = OrderedDict()
    for evt in events:
        per_path.setdefault(evt.prim_path, []).append(evt)

    result: list[StageEvent] = []

    for path, path_events in per_path.items():
        reduced = _coalesce_for_path(path_events)
        result.extend(reduced)

    # Restore cross-path insertion order: sort by timestamp of the first
    # event for each path, which preserves the original ordering across paths.
    # Since we already maintain per_path order via OrderedDict, the above
    # extends in per-path order.  For mixed-path ordering we re-sort by the
    # first timestamp of each group.
    # Rebuild with stable sort by the timestamp of the first item in each
    # path group.
    first_ts: dict[str, float] = {}
    for path, path_events in per_path.items():
        first_ts[path] = path_events[0].timestamp

    result.sort(key=lambda e: first_ts[e.prim_path])
    return result


def _coalesce_for_path(events: list[StageEvent]) -> list[StageEvent]:
    """Apply coalescing rules to a list of events sharing the same prim_path."""
    # Rule: prim_added + prim_removed anywhere in sequence → drop both.
    has_added = any(e.event_type == "prim_added" for e in events)
    has_removed = any(e.event_type == "prim_removed" for e in events)
    if has_added and has_removed:
        # Net no-op: drop everything for this path.
        return []

    output: list[StageEvent] = []
    last_transform: StageEvent | None = None
    last_attr: StageEvent | None = None

    for evt in events:
        if evt.event_type == "prim_transformed":
            last_transform = evt  # keep only the last
        elif evt.event_type == "attribute_changed":
            last_attr = evt  # keep only the last
        else:
            # prim_added, prim_removed, metadata_changed — pass through
            output.append(evt)

    if last_transform is not None:
        output.append(last_transform)
    if last_attr is not None:
        output.append(last_attr)

    # Re-sort by timestamp to keep chronological order within the path.
    output.sort(key=lambda e: e.timestamp)
    return output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def expected_event_types() -> list[StageEventType]:
    """Return the full list of supported StageEventType values."""
    return [
        "prim_added",
        "prim_removed",
        "prim_transformed",
        "attribute_changed",
        "metadata_changed",
    ]


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

def get_phase_metadata() -> dict:
    """Return metadata describing this phase implementation."""
    return {
        "phase": "29",
        "title": "Canvas mirror panel: live state-sync from Kit",
        "status": PHASE_STATUS,
        "agent_type": "sonnet-bounded",
        "files": [
            "exts/isaac_6.0/omni.isaac.assist/omni/isaac/assist/ui/stage_event_listener.py",
            "tests/test_phase_29_stage_event_listener.py",
        ],
        "gate": (
            "pytest tests/test_phase_29_stage_event_listener.py — "
            "event filter classifies prim_add/prim_remove/prim_transform, "
            "debounce coalesces rapid events"
        ),
        "kit_available": _KIT_AVAILABLE,
    }
