"""Phase 106 — Post-release retrospective + roadmap.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 106.

Provides:
- Dataclasses for structured retrospective data.
- ``classify_priority`` — deterministic keyword-based priority classifier.
- ``RetrospectiveBuilder`` — fills ``docs/templates/retrospective_template.md``
  with runtime data and returns a rendered markdown string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE_ID = 106
PHASE_TITLE = "Post-release retrospective + roadmap"
PHASE_STATUS = "landed"

_DEFAULT_TEMPLATE_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "docs"
    / "templates"
    / "retrospective_template.md"
)

_DEFAULT_URGENCY_KEYWORDS: List[str] = [
    "security",
    "outage",
    "data_loss",
    "regression",
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ActionItem:
    """A single follow-up task with ownership, priority, and due date."""

    owner: str
    action: str
    priority: Literal["P0", "P1", "P2", "P3"]
    due: str  # ISO-8601 date, e.g. "2026-06-01"


@dataclass
class ReleaseMetrics:
    """Quantitative indicators captured at release time."""

    tests_passed: int
    tests_failed: int
    phases_landed_pct: float
    p95_latency_ms: float
    error_rate_pct: float


@dataclass
class RetrospectiveData:
    """All inputs required to render a retrospective document."""

    # --- Release summary ---
    release_version: str
    release_date: str  # ISO-8601
    primary_goals: List[str]
    success_metrics: List[str]

    # --- Qualitative sections ---
    went_well: List[str]
    didnt_go_well: List[str]
    surprises: List[str]

    # --- Quantitative section ---
    metrics: ReleaseMetrics

    # --- Action items ---
    action_items: List[ActionItem] = field(default_factory=list)

    # --- Roadmap ---
    next_quarter: List[str] = field(default_factory=list)
    backlog: List[str] = field(default_factory=list)

    # --- Acknowledgments ---
    acknowledgments: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Priority classifier
# ---------------------------------------------------------------------------


def classify_priority(
    action: str,
    urgency_keywords: Optional[List[str]] = None,
) -> Literal["P0", "P1", "P2", "P3"]:
    """Return a priority label for *action* based on keyword matching.

    Classification rules (evaluated top-to-bottom, first match wins):

    * **P0** — any urgency keyword present (default: "security", "outage",
      "data_loss", "regression").
    * **P1** — "blocker" or "bug" present in the action text.
    * **P2** — "improvement" or "refactor" present.
    * **P3** — fallback for everything else.

    Parameters
    ----------
    action:
        Free-text description of the action item.
    urgency_keywords:
        Override the default P0 trigger keywords.  Pass an empty list to
        disable P0 matching entirely.
    """
    if urgency_keywords is None:
        urgency_keywords = _DEFAULT_URGENCY_KEYWORDS

    lower = action.lower()

    # P0 — security / outage / data loss / regression
    for kw in urgency_keywords:
        if kw.lower() in lower:
            return "P0"

    # P1 — blockers and confirmed bugs
    if "blocker" in lower or "bug" in lower:
        return "P1"

    # P2 — improvements and refactors
    if "improvement" in lower or "refactor" in lower:
        return "P2"

    # P3 — everything else (polish, docs, nice-to-have)
    return "P3"


# ---------------------------------------------------------------------------
# Template renderer
# ---------------------------------------------------------------------------


class RetrospectiveBuilder:
    """Renders a ``RetrospectiveData`` instance into a markdown string.

    The builder loads ``docs/templates/retrospective_template.md`` (or a
    custom path), then replaces ``{placeholder}`` tokens section by section.

    Usage::

        builder = RetrospectiveBuilder()
        markdown = builder.render(data)
    """

    def __init__(self, template_path: Optional[Path] = None) -> None:
        """Initialise with an optional custom template path; defaults to the module-level default."""
        self._template_path: Path = (
            template_path if template_path is not None else _DEFAULT_TEMPLATE_PATH
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(self, data: RetrospectiveData) -> str:
        """Return the template with all placeholders filled from *data*."""
        text = self._template_path.read_text(encoding="utf-8")

        # Scalar string replacements
        text = text.replace("{release_version}", data.release_version)
        text = text.replace("{release_date}", data.release_date)

        # List -> bulleted markdown
        text = text.replace("{primary_goals}", self._to_bullets(data.primary_goals))
        text = text.replace("{success_metrics}", self._to_bullets(data.success_metrics))
        text = text.replace("{went_well}", self._to_bullets(data.went_well))
        text = text.replace("{didnt_go_well}", self._to_bullets(data.didnt_go_well))
        text = text.replace("{surprises}", self._to_bullets(data.surprises))
        text = text.replace("{next_quarter}", self._to_bullets(data.next_quarter))
        text = text.replace("{backlog}", self._to_bullets(data.backlog))
        text = text.replace("{acknowledgments}", self._to_bullets(data.acknowledgments))

        # ReleaseMetrics -> sub-bullets
        text = text.replace("{metrics}", self._metrics_to_bullets(data.metrics))

        # ActionItems -> markdown table rows (no header — template already has it)
        text = text.replace("{action_items}", self._action_items_to_rows(data.action_items))

        return text

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_bullets(items: List[str]) -> str:
        """Convert a list of strings into a bulleted markdown block."""
        if not items:
            return "_None recorded._"
        return "".join(f"- {item}\n" for item in items)

    @staticmethod
    def _metrics_to_bullets(m: ReleaseMetrics) -> str:
        """Format ReleaseMetrics as a sub-bulleted list."""
        return (
            f"- **tests_passed**: {m.tests_passed}\n"
            f"- **tests_failed**: {m.tests_failed}\n"
            f"- **phases_landed_pct**: {m.phases_landed_pct:.1f} %\n"
            f"- **p95_latency_ms**: {m.p95_latency_ms:.1f} ms\n"
            f"- **error_rate_pct**: {m.error_rate_pct:.2f} %\n"
        )

    @staticmethod
    def _action_items_to_rows(items: List[ActionItem]) -> str:
        """Format a list of ActionItems as markdown table body rows."""
        if not items:
            return "| - | No action items recorded. | - | - |"
        rows = []
        for item in items:
            rows.append(
                f"| {item.owner} | {item.action} | {item.priority} | {item.due} |"
            )
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 106",
        "template_path": str(_DEFAULT_TEMPLATE_PATH),
    }
