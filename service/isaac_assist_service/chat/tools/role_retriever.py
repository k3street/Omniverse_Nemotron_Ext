"""Phase 20 — role-based template retrieval.

Extends template_retriever to filter by LayoutSpec.intent.pattern_hint
+ structural_features. Templates without a `roles` field fall through
to legacy similarity-only matching.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 20.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class MatchScore:
    score: float
    reason: str = ""


@dataclass
class RatifyResult:
    status: str = "unknown"
    issues: List[str] = field(default_factory=list)


def retrieve_with_roles(spec: Any, templates: Optional[List[Dict]] = None
                        ) -> List[Tuple[Dict, MatchScore, RatifyResult]]:
    """Return templates ranked by ratifier success.

    Phase 20 scaffold: defers to legacy template_retriever for now.
    Full role-overlap scoring + ratify integration is daytime work.
    """
    if templates is None:
        templates = []
    out = []
    for tpl in templates:
        roles = tpl.get("roles")
        if roles is None:
            score = MatchScore(score=0.5, reason="legacy_no_roles")
            ratify = RatifyResult(status="ok")
        else:
            # TODO Phase 20 full: score structural overlap between
            # spec.intent + spec.structural_features and template roles.
            score = MatchScore(score=0.7, reason="role_based_stub")
            ratify = RatifyResult(status="ok")
        out.append((tpl, score, ratify))
    out.sort(key=lambda r: -r[1].score)
    return out
