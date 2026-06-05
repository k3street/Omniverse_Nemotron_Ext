"""Phase 20 — role-based template retrieval (SPEC/LOGIC layer).

Implements RoleRetriever: a pure-Python retriever that ranks role-based
scene-template matches ABOVE legacy (role-free) templates.  No Kit RPC
dependency — full end-to-end application of the retrieved template is
deferred to Phase 19 (Kit RPC).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 20.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from service.isaac_assist_service.multimodal.role_template_index import (
    ROLE_TEMPLATE_INDEX,
    RoleTemplateIndex,
)

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_STATUS: Literal["landed"] = "landed"


def get_phase_metadata() -> dict:
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
