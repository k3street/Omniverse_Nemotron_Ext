"""Phase 20 — role-based template retrieval (SPEC/LOGIC layer).

Implements RoleRetriever: a pure-Python retriever that ranks role-based
scene-template matches ABOVE legacy (role-free) templates.  No Kit RPC
dependency — full end-to-end application of the retrieved template is
deferred to Phase 19 (Kit RPC).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 20.

Also hosts CRM-C2 `autopick_compliance_mode` per
`docs/specs/2026-05-11-contact-rich-manipulation-spec.md` §4.1 — the
auto-pick algorithm that resolves an embodiment-appropriate
compliance_mode when a template doesn't supply one explicitly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Mapping

from service.isaac_assist_service.multimodal.role_template_index import (
    ROLE_TEMPLATE_INDEX,
    RoleTemplateIndex,
)

if TYPE_CHECKING:
    from service.isaac_assist_service.multimodal.types import LayoutSpec

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_STATUS: Literal["landed"] = "landed"


def get_phase_metadata() -> dict:
    """Return Phase 20 status metadata for health checks and CI reporting."""
    return {
        "phase": "20",
        "title": "Role-based template refactor",
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 20",
        "gate": "pytest tests/test_role_based_templates.py",
    }


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """Lowercase and split on non-alphanumeric boundaries."""
    return set(t for t in re.split(r"[^a-z0-9]+", text.lower()) if t)


def fuzzy_score(query: str, text: str) -> float:
    """Jaccard similarity between the token-sets of *query* and *text*.

    Returns a float in [0.0, 1.0].  Empty strings → 0.0.
    """
    q_tokens = _tokenize(query)
    t_tokens = _tokenize(text)
    if not q_tokens or not t_tokens:
        return 0.0
    intersection = q_tokens & t_tokens
    union = q_tokens | t_tokens
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# TemplateMatch dataclass
# ---------------------------------------------------------------------------

@dataclass
class TemplateMatch:
    """A single ranked template result from RoleRetriever.

    Attributes:
        template_id: identifier of the matched template
        source: "role_based", "legacy", or "wizard"
        match_score: similarity in [0.0, 1.0]; higher is better
        matched_role: role name that produced the match, or None for legacy
        matched_tags: query tokens that overlapped with the entry's tags
        notes: human-readable annotation from the template entry
    """

    template_id: str
    source: Literal["role_based", "legacy", "wizard"]
    match_score: float
    matched_role: str | None
    matched_tags: list[str] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# RoleRetriever
# ---------------------------------------------------------------------------

class RoleRetriever:
    """Rank scene templates for a natural-language query.

    Role-based matches (templates from *role_index*) are always ranked
    ahead of legacy (role-free dict) matches.  Within each tier the entries
    are sorted by descending *match_score*.
    """

    def __init__(
        self,
        role_index: RoleTemplateIndex | None = None,
        legacy_templates: list[dict] | None = None,
    ) -> None:
        """Initialise the retriever with an optional role index and legacy template list.

        Args:
            role_index: pre-built RoleTemplateIndex; defaults to the module-level
                ROLE_TEMPLATE_INDEX singleton if None.
            legacy_templates: flat list of legacy template dicts (role-free).
                Defaults to empty if None.
        """
        self._role_index: RoleTemplateIndex = (
            role_index
            if role_index is not None
            else RoleTemplateIndex(ROLE_TEMPLATE_INDEX)
        )
        self._legacy: list[dict] = list(legacy_templates) if legacy_templates else []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve_with_roles(
        self,
        query: str,
        role_hints: list[str] | None = None,
        max_results: int = 10,
    ) -> list[TemplateMatch]:
        """Return up to *max_results* TemplateMatch objects, role-based first.

        Scoring rules:
        1. If any *role_hint* exactly matches a known role → all entries for
           that role get score=1.0.
        2. Otherwise fuzzy-match *query* tokens against role names, sub-roles
           and tags.  score = count(matching_tokens) / count(query_tokens).
        3. Legacy templates are appended with score < lowest role-based score
           (or 0.0 if no role-based matches).
        """
        role_matches = self._score_role_based(query, role_hints)
        legacy_matches = self._score_legacy(query, role_matches)

        combined = role_matches + legacy_matches
        combined.sort(key=lambda m: -m.match_score)
        return combined[:max_results]

    def retrieve_legacy(
        self,
        query: str,
        max_results: int = 5,
    ) -> list[TemplateMatch]:
        """Return legacy-only matches, scored and sorted."""
        scored = []
        for tpl in self._legacy:
            tpl_id = str(tpl.get("task_id") or tpl.get("template_id") or id(tpl))
            haystack = " ".join(
                str(v) for v in tpl.values() if isinstance(v, (str, int, float))
            )
            score = self.score_template_against_query(tpl_id, query)
            if score == 0.0:
                score = fuzzy_score(query, haystack)
            scored.append(
                TemplateMatch(
                    template_id=tpl_id,
                    source="legacy",
                    match_score=score,
                    matched_role=None,
                    matched_tags=[],
                    notes=str(tpl.get("notes", "")),
                )
            )
        scored.sort(key=lambda m: -m.match_score)
        return scored[:max_results]

    def score_template_against_query(self, template_id: str, query: str) -> float:
        """Word-overlap score between *template_id* tokens and *query* tokens.

        Returns count(shared_words) / count(query_words).  Zero if query is
        empty or no words match.
        """
        q_tokens = _tokenize(query)
        t_tokens = _tokenize(template_id)
        if not q_tokens:
            return 0.0
        shared = q_tokens & t_tokens
        return len(shared) / len(q_tokens)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_role_based(
        self,
        query: str,
        role_hints: list[str] | None,
    ) -> list[TemplateMatch]:
        """Produce scored TemplateMatch list from the role index."""
        entries = self._role_index._entries  # type: ignore[attr-defined]
        known_roles = {e.role for e in entries}
        results: list[TemplateMatch] = []

        # --- exact hint match ---
        if role_hints:
            for hint in role_hints:
                if hint in known_roles:
                    for entry in self._role_index.by_role(hint):
                        results.append(
                            TemplateMatch(
                                template_id=entry.template_id,
                                source="role_based",
                                match_score=1.0,
                                matched_role=entry.role,
                                matched_tags=list(entry.tags),
                                notes=entry.notes,
                            )
                        )
            if results:
                results.sort(key=lambda m: -m.match_score)
                return results

        # --- fuzzy match against role name / sub_role / tags ---
        q_tokens = _tokenize(query)
        if not q_tokens:
            return results

        for entry in entries:
            candidate_text = " ".join(
                filter(
                    None,
                    [
                        entry.role,
                        entry.sub_role or "",
                        " ".join(entry.tags),
                        entry.notes,
                    ],
                )
            )
            c_tokens = _tokenize(candidate_text)
            matched = q_tokens & c_tokens
            score = len(matched) / len(q_tokens) if q_tokens else 0.0
            if score > 0.0:
                results.append(
                    TemplateMatch(
                        template_id=entry.template_id,
                        source="role_based",
                        match_score=score,
                        matched_role=entry.role,
                        matched_tags=[t for t in entry.tags if t in q_tokens],
                        notes=entry.notes,
                    )
                )

        results.sort(key=lambda m: -m.match_score)
        return results

    def _score_legacy(
        self,
        query: str,
        role_matches: list[TemplateMatch],
    ) -> list[TemplateMatch]:
        """Score legacy templates, capping scores below role-based tier."""
        if not self._legacy:
            return []

        # Ceiling for legacy scores: just below the worst role-based match
        role_floor = min((m.match_score for m in role_matches), default=0.0)
        legacy_ceiling = max(role_floor - 1e-6, 0.0)

        out: list[TemplateMatch] = []
        for tpl in self._legacy:
            tpl_id = str(tpl.get("task_id") or tpl.get("template_id") or id(tpl))
            haystack = " ".join(
                str(v) for v in tpl.values() if isinstance(v, (str, int, float))
            )
            raw_score = fuzzy_score(query, haystack)
            # Clamp: legacy must not exceed the lowest role-based score
            capped = min(raw_score, legacy_ceiling) if role_matches else raw_score
            out.append(
                TemplateMatch(
                    template_id=tpl_id,
                    source="legacy",
                    match_score=capped,
                    matched_role=None,
                    matched_tags=[],
                    notes=str(tpl.get("notes", "")),
                )
            )

        out.sort(key=lambda m: -m.match_score)
        return out


# ---------------------------------------------------------------------------
# CRM-C2 — Compliance auto-pick
#
# Spec: docs/specs/2026-05-11-contact-rich-manipulation-spec.md §4.1
#
# Design notes:
#
# * The decision table is encoded as `_COMPLIANCE_TABLE` — a list of
#   (predicate, mode) rules consulted in order.  Adding a new robot class
#   (e.g. "yaskawa_gp25", "h1") is a single-line edit: append one
#   ``_ComplianceRule`` row to the table.
#
# * The "real_robot_deployment" tag is matched both as a bare identifier
#   AND as a namespaced ``user:`` / ``isaac:`` variant — multimodal types
#   require namespaced tag formats (regex), but template authors and the
#   spec example use the bare form interchangeably.
#
# * All input access is defensive: missing intent, missing
#   structural_features, missing role_bindings, missing primary_robot,
#   non-string class — every degenerate case folds back to one of two
#   safe defaults (None when no contact phase confirmed, "admittance"
#   when contact is confirmed but the robot is unknown).
#
# * Returns the bare string mode (e.g. "admittance") — matches the
#   ``compliance_mode`` field type on ``LayoutSpec`` and the enum in
#   ``COMPLIANCE_MODE_ENUM``.  Returns ``None`` when no compliance is
#   required (free-space task, rigid baseline).
# ---------------------------------------------------------------------------

_REAL_ROBOT_DEPLOYMENT_TAG = "real_robot_deployment"
"""Tag indicating the layout will be deployed to a physical robot — flips
Franka selection from sim-safe ``admittance`` to vendor-tuned
``franka_cartesian_impedance`` per spec §4.1."""

_DEFAULT_FRANKA_SIM_MODE = "admittance"
_FRANKA_REAL_DEPLOY_MODE = "franka_cartesian_impedance"
_DEFAULT_POSITION_MODE = "admittance"
"""Position-mode robots default to admittance per spec §3 (mainline
ros2_controllers, position-mode + external F/T sensor)."""


@dataclass(frozen=True)
class _ComplianceRule:
    """Single row in the compliance auto-pick table.

    ``robot_classes`` lists the exact ``role_bindings["primary_robot"]["class"]``
    string(s) the row matches.  Adding a new robot is a one-line edit:
    append a new ``_ComplianceRule`` to ``_COMPLIANCE_TABLE``.

    ``mode_for_sim`` is the mode picked when the layout is NOT tagged
    ``real_robot_deployment``; ``mode_for_real`` is the mode picked when
    the tag IS present.  Most rows use the same value for both because
    only Franka has a vendor-tuned real-robot variant.
    """

    robot_classes: tuple[str, ...]
    mode_for_sim: str
    mode_for_real: str
    notes: str = ""


_COMPLIANCE_TABLE: tuple[_ComplianceRule, ...] = (
    # Franka — only embodiment with a vendor-tuned real-robot variant.
    _ComplianceRule(
        robot_classes=("franka_panda",),
        mode_for_sim=_DEFAULT_FRANKA_SIM_MODE,
        mode_for_real=_FRANKA_REAL_DEPLOY_MODE,
        notes="Franka FCI provides Cartesian impedance for sim-to-real transfer.",
    ),
    # Universal Robots family — position-mode only; admittance via F/T sensor.
    _ComplianceRule(
        robot_classes=("ur10e", "ur5e", "ur3e"),
        mode_for_sim=_DEFAULT_POSITION_MODE,
        mode_for_real=_DEFAULT_POSITION_MODE,
        notes="UR robots are position-mode only.",
    ),
    # Kinova Gen3 — position-mode, admittance default.
    _ComplianceRule(
        robot_classes=("kinova_gen3",),
        mode_for_sim=_DEFAULT_POSITION_MODE,
        mode_for_real=_DEFAULT_POSITION_MODE,
        notes="Kinova Gen3 is position-mode at the ros2_control surface.",
    ),
    # Add new robots here.  Single-line edit: append one _ComplianceRule.
    # Example future entries:
    #   _ComplianceRule(("yaskawa_gp25",), "admittance", "admittance",
    #                   "Yaskawa GP25 — position-mode, palletizing scale."),
    #   _ComplianceRule(("h1",), "admittance", "admittance",
    #                   "Unitree H1 humanoid — manipulation arm only."),
)


_UNKNOWN_ROBOT_MODE = "admittance"
"""Safe fallback for unrecognised robot classes per spec §4.1 last
clause — admittance is mainline ros2_controllers, position-mode agnostic,
works with any externally-mounted F/T sensor."""


def _tag_matches_real_deployment(tag: str) -> bool:
    """Match ``real_robot_deployment`` whether bare or namespaced.

    Multimodal ``structural_tags`` follow the regex
    ``^(isaac|cad|user):[a-z0-9_]+(\\.[a-z0-9_]+)*$`` — but the spec
    pseudo-code and template authors use the bare ``real_robot_deployment``
    form.  Accept both to be robust to either convention.
    """
    if not isinstance(tag, str):
        return False
    if tag == _REAL_ROBOT_DEPLOYMENT_TAG:
        return True
    # Strip optional namespace prefix ("user:", "isaac:", "cad:") then
    # accept the remainder as the bare tag.
    if ":" in tag:
        _, _, body = tag.partition(":")
        return body == _REAL_ROBOT_DEPLOYMENT_TAG
    return False


def _has_real_robot_deployment(structural_tags: Any) -> bool:
    """Defensive check for the real-robot-deployment tag in a tag list.

    Accepts any iterable of strings (or non-strings — ignored).  Returns
    False if the input is None / non-iterable / empty.
    """
    if not structural_tags:
        return False
    try:
        return any(_tag_matches_real_deployment(t) for t in structural_tags)
    except TypeError:
        # structural_tags wasn't actually iterable — degenerate input.
        return False


def _resolve_robot_class(role_bindings: Any) -> str | None:
    """Pull the primary_robot.class string from a role_bindings mapping.

    Tolerates:
    * ``role_bindings`` is ``None`` or not a mapping → returns ``None``
    * ``primary_robot`` missing or not a mapping → returns ``None``
    * ``"class"`` missing or not a string → returns ``None``

    The actual robot-class string is NOT lowercased / normalised here —
    the auto-pick table holds the canonical class names as listed in
    ``role_template_index.py`` (e.g. ``"franka_panda"``).  Matching is
    case-sensitive on purpose: if a caller passes a mangled class
    name, we want it to fall through to the unknown-robot branch with
    a safe default rather than silently coerce.
    """
    if not isinstance(role_bindings, Mapping):
        return None
    primary = role_bindings.get("primary_robot")
    if not isinstance(primary, Mapping):
        return None
    cls = primary.get("class")
    if not isinstance(cls, str):
        return None
    return cls


def _lookup_compliance_rule(robot_class: str) -> _ComplianceRule | None:
    """Find the auto-pick row matching ``robot_class``, or ``None``."""
    for rule in _COMPLIANCE_TABLE:
        if robot_class in rule.robot_classes:
            return rule
    return None


def autopick_compliance_mode(
    layout_spec: "LayoutSpec | Any",
    role_bindings: Mapping[str, Any] | None,
) -> str | None:
    """Auto-pick a compliance_mode for a LayoutSpec + role_bindings pair.

    Per spec §4.1, the user never sees this choice unless they explicitly
    override.  Free-space tasks → None (rigid baseline).  Contact-rich
    tasks → embodiment-appropriate compliance mode.

    Args:
        layout_spec: The LayoutSpec for which to resolve compliance.
            Accessed defensively via ``getattr`` so partially-formed
            specs (e.g. missing intent, missing structural_features)
            collapse to the safe "no contact" branch.
        role_bindings: A mapping shaped like
            ``{"primary_robot": {"class": "<robot_class>", ...}, ...}``.
            May be ``None`` or missing the ``primary_robot`` entry — in
            both cases falls back to safe defaults.

    Returns:
        One of:
        * ``None`` — no contact phase detected; rigid baseline is fine.
          Free-space pick-and-place, navigation, simple inspection.
        * ``"admittance"`` — DEFAULT for position-mode robots with a
          contact phase.  UR, Kinova, unknown robots all map here.
          Also the sim-default for Franka (no real-robot tag).
        * ``"franka_cartesian_impedance"`` — Franka + real-robot tag.
          Vendor-tuned for sim-to-real transfer.

    Returns combinations by input:
        +-------------------+-----------------+--------------------+-----------------------------+
        | has_contact_phase | robot_class     | real_robot_deploy  | returned mode               |
        +===================+=================+====================+=============================+
        | False (or absent) | any             | any                | None                        |
        | True              | franka_panda    | False              | "admittance"                |
        | True              | franka_panda    | True               | "franka_cartesian_impedance"|
        | True              | ur10e/ur5e/ur3e | any                | "admittance"                |
        | True              | kinova_gen3     | any                | "admittance"                |
        | True              | unknown / None  | any                | "admittance"                |
        +-------------------+-----------------+--------------------+-----------------------------+

    Notes:
        * NOT validated against ``COMPLIANCE_MODE_ENUM`` here — the
          ``LayoutSpec.compliance_mode`` field is validated separately in
          ``multimodal/validate.py``.  Every value this function can
          return is already a member of the enum by construction.
        * Hard-incompatibility checks for explicit overrides live in
          CRM-C3's ``validate_compliance_override`` — this function only
          produces sim-safe choices, so no validation is needed here.
        * To add a new robot class, append one row to
          ``_COMPLIANCE_TABLE`` above — a single-line edit.
    """
    # ----- 1. Defensive read of intent.structural_features ----------------
    intent = getattr(layout_spec, "intent", None)
    structural_features = getattr(intent, "structural_features", None)

    # has_contact_phase is read defensively; if absent OR not truthy
    # we treat the task as free-space (no compliance needed).
    has_contact_phase = bool(
        getattr(structural_features, "has_contact_phase", False)
    )
    if not has_contact_phase:
        return None

    # ----- 2. Resolve robot class + deployment tag ------------------------
    robot_class = _resolve_robot_class(role_bindings)
    structural_tags = getattr(intent, "structural_tags", None)
    real_deployment = _has_real_robot_deployment(structural_tags)

    # ----- 3. Look up the rule, with unknown-robot fallback ---------------
    rule = (
        _lookup_compliance_rule(robot_class) if robot_class else None
    )
    if rule is None:
        # Unknown robot OR missing primary_robot binding → safe default.
        # ``real_deployment`` is IGNORED here because there's no
        # vendor-tuned mode without a known embodiment.
        return _UNKNOWN_ROBOT_MODE

    return rule.mode_for_real if real_deployment else rule.mode_for_sim
