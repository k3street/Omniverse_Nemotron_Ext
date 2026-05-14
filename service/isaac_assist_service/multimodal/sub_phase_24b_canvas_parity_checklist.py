"""Phase 24b — Canvas SPA interactive editing parity checklist.

Registry of 12 frontend parity items with status tracking and
acceptance-criteria verification.  Actual Vue/TypeScript implementations
stay scaffold; this module provides the spec/checklist layer.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 24b.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "24b"
PHASE_TITLE = "Canvas SPA interactive editing parity"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 24b",
        "parity_items": len(PARITY_ITEMS),
        "categories": expected_categories(),
    }


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ItemCategory = Literal["interaction", "persistence", "ui_polish", "integration"]
ItemStatus = Literal["scaffold", "wip", "landed", "verified"]

# ---------------------------------------------------------------------------
# ParityItem dataclass
# ---------------------------------------------------------------------------


@dataclass
class ParityItem:
    """A single canvas-parity work item with status tracking."""

    item_id: int
    name: str
    description: str
    category: ItemCategory
    acceptance_criteria: List[str]
    status: ItemStatus = "scaffold"
    landed_in_phase: Optional[str] = None
    notes: str = ""


# ---------------------------------------------------------------------------
# 12-item registry
# ---------------------------------------------------------------------------

PARITY_ITEMS: List[ParityItem] = [
    ParityItem(
        item_id=1,
        name="Object palette drag-drop",
        description=(
            "Drag an object class from the palette panel onto the canvas to "
            "instantiate it at the drop position."
        ),
        category="interaction",
        acceptance_criteria=[
            "Dropping a palette item creates a new prim at the pointer coordinates.",
            "Drag ghost image follows the cursor during the drag operation.",
            "Dropping outside the canvas bounds is a no-op with no console error.",
        ],
    ),
    ParityItem(
        item_id=2,
        name="Konva Transformer multi-select",
        description=(
            "Shift-click or rubber-band to select multiple canvas objects; "
            "Konva Transformer wraps all selected nodes."
        ),
        category="interaction",
        acceptance_criteria=[
            "Transformer bounding box encompasses all selected nodes.",
            "Moving the transformer moves all selected nodes together.",
        ],
    ),
    ParityItem(
        item_id=3,
        name="Smart guides — 5 marker types",
        description=(
            "While dragging, display alignment guide lines for: left, right, "
            "top, bottom, and center-center coincidence with nearby objects."
        ),
        category="interaction",
        acceptance_criteria=[
            "All five guide marker types (left, right, top, bottom, center) are rendered.",
            "Guides disappear immediately when the drag ends.",
        ],
    ),
    ParityItem(
        item_id=4,
        name="Dimension lines",
        description=(
            "Selected object shows width and height dimension annotations "
            "rendered as SVG/Konva overlays."
        ),
        category="ui_polish",
        acceptance_criteria=[
            "Dimension line labels update live as the object is resized.",
            "Dimension labels use scene-unit suffix (e.g. m or cm) not raw pixels.",
        ],
    ),
    ParityItem(
        item_id=5,
        name="Undo/redo 100-step",
        description=(
            "Ctrl+Z / Ctrl+Y navigate a 100-step command history.  "
            "History survives minor SSE reconnects."
        ),
        category="interaction",
        acceptance_criteria=[
            "At least 100 discrete steps are maintained in the history stack.",
            "Redo stack is cleared when a new action is applied mid-history.",
        ],
    ),
    ParityItem(
        item_id=6,
        name="localStorage WAL",
        description=(
            "Every canvas mutation is written to a localStorage write-ahead log "
            "so that a page reload can recover the last unsaved state."
        ),
        category="persistence",
        acceptance_criteria=[
            "WAL entry is written synchronously before the mutation is applied.",
            "On reload, staged WAL entries are replayed to restore canvas state.",
        ],
    ),
    ParityItem(
        item_id=7,
        name="sendBeacon flush on tab unload",
        description=(
            "On `visibilitychange`/`beforeunload` the pending WAL is flushed to "
            "the server via `navigator.sendBeacon`."
        ),
        category="persistence",
        acceptance_criteria=[
            "sendBeacon is called with the serialized WAL payload on tab close.",
            "Flush is a no-op if WAL is already empty.",
        ],
    ),
    ParityItem(
        item_id=8,
        name="SSE listener stability",
        description=(
            "The EventSource connection reconnects automatically with exponential "
            "backoff and does not produce stale-state divergence after a gap."
        ),
        category="integration",
        acceptance_criteria=[
            "Reconnect is attempted within 2s of connection drop.",
            "After reconnect a full-state sync message reconciles the canvas.",
        ],
    ),
    ParityItem(
        item_id=9,
        name="Optimistic UI updates",
        description=(
            "User actions are applied immediately to the local canvas model; "
            "server confirmation either commits or rolls back."
        ),
        category="interaction",
        acceptance_criteria=[
            "Local state reflects the action within one frame of user input.",
            "On server rejection the optimistic change is rolled back with a toast.",
        ],
    ),
    ParityItem(
        item_id=10,
        name="Object rotation handle",
        description=(
            "A circular rotation handle is rendered above the Transformer; "
            "dragging it rotates the selected object(s)."
        ),
        category="interaction",
        acceptance_criteria=[
            "Rotation handle is visible and interactive when exactly one object is selected.",
            "Rotation value is snapped to 15-degree increments when snap is enabled.",
        ],
    ),
    ParityItem(
        item_id=11,
        name="Snap-toggle keyboard shortcut",
        description=(
            "Pressing `S` toggles snap-to-grid/guide mode; the toolbar snap "
            "button reflects the current state."
        ),
        category="ui_polish",
        acceptance_criteria=[
            "Pressing S toggles snapping and updates the toolbar button aria-pressed attribute.",
            "Snap state persists in localStorage across page reloads.",
        ],
    ),
    ParityItem(
        item_id=12,
        name="Canvas zoom + pan with bounds",
        description=(
            "Pinch/scroll to zoom (0.25x–4x) and drag-to-pan; pan is clamped "
            "so the content never fully scrolls off screen."
        ),
        category="ui_polish",
        acceptance_criteria=[
            "Zoom level is clamped to [0.25, 4.0] exclusive of those bounds.",
            "Pan position is clamped so at least 10% of canvas content is always visible.",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def expected_categories() -> List[str]:
    """Return the canonical list of item categories."""
    return ["interaction", "persistence", "ui_polish", "integration"]


# ---------------------------------------------------------------------------
# CanvasParityTracker
# ---------------------------------------------------------------------------


class CanvasParityTracker:
    """Tracks status and per-criterion verification for all parity items."""

    def __init__(self, items: Optional[List[ParityItem]] = None) -> None:
        # Deep-copy items so mutations don't affect the module-level list
        src = items if items is not None else PARITY_ITEMS
        self._items: Dict[int, ParityItem] = {}
        for item in src:
            self._items[item.item_id] = ParityItem(
                item_id=item.item_id,
                name=item.name,
                description=item.description,
                category=item.category,
                acceptance_criteria=list(item.acceptance_criteria),
                status=item.status,
                landed_in_phase=item.landed_in_phase,
                notes=item.notes,
            )
        # verification_results: {item_id: {criterion_idx: bool}}
        self._verifications: Dict[int, Dict[int, bool]] = {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def mark_status(
        self,
        item_id: int,
        status: str,
        landed_in_phase: Optional[str] = None,
        notes: str = "",
    ) -> None:
        """Advance the status of *item_id* to *status*."""
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"No parity item with id={item_id}")
        item.status = status  # type: ignore[assignment]
        if landed_in_phase is not None:
            item.landed_in_phase = landed_in_phase
        if notes:
            item.notes = notes

    def verify(self, item_id: int, criterion_idx: int, passed: bool) -> None:
        """Record the result of a single acceptance criterion check."""
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"No parity item with id={item_id}")
        if criterion_idx < 0 or criterion_idx >= len(item.acceptance_criteria):
            raise IndexError(
                f"criterion_idx={criterion_idx} out of range for item {item_id} "
                f"({len(item.acceptance_criteria)} criteria)"
            )
        if item_id not in self._verifications:
            self._verifications[item_id] = {}
        self._verifications[item_id][criterion_idx] = passed

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_item(self, item_id: int) -> Optional[ParityItem]:
        """Return the ParityItem for *item_id*, or None if not found."""
        return self._items.get(item_id)

    def by_status(self, status: str) -> List[ParityItem]:
        """Return all items whose status matches *status*."""
        return [i for i in self._items.values() if i.status == status]

    def by_category(self, category: str) -> List[ParityItem]:
        """Return all items whose category matches *category*."""
        return [i for i in self._items.values() if i.category == category]

    def progress(self) -> Dict[str, Any]:
        """Return a summary progress dict."""
        items = list(self._items.values())
        total = len(items)
        counts: Dict[str, int] = {s: 0 for s in ("scaffold", "wip", "landed", "verified")}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        landed_or_verified = counts["landed"] + counts["verified"]
        pct_landed = round(landed_or_verified / total * 100, 1) if total else 0.0
        return {
            "total": total,
            "scaffold": counts["scaffold"],
            "wip": counts["wip"],
            "landed": counts["landed"],
            "verified": counts["verified"],
            "pct_landed": pct_landed,
        }

    def report(self) -> str:
        """Return a markdown table of all parity items."""
        header = (
            "| ID | Name | Category | Status | Landed in | Notes |\n"
            "|-----|------|----------|--------|-----------|-------|\n"
        )
        rows: List[str] = []
        for item in sorted(self._items.values(), key=lambda x: x.item_id):
            rows.append(
                f"| {item.item_id} "
                f"| {item.name} "
                f"| {item.category} "
                f"| {item.status} "
                f"| {item.landed_in_phase or ''} "
                f"| {item.notes} |"
            )
        return header + "\n".join(rows)

    def verification_status(self, item_id: int) -> Dict[int, bool]:
        """Return per-criterion verification results for *item_id*."""
        return dict(self._verifications.get(item_id, {}))
