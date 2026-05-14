"""Phase 45 — replace CriticAgent.critique with deterministic scoring.

LLM is feature extractor (returns structured booleans/scores). The
final critique is computed deterministically from those features.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 45.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CritiqueFeatures:
    """Feature flags extracted from generated code for deterministic critique scoring.

    Attributes:
        has_required_imports: True when all mandatory Isaac Sim imports are present.
        follows_safe_xform: True when the code uses safe Xform-op patterns.
        no_deprecated_apis: True when no deprecated USD / Isaac Sim APIs are detected.
        sensible_prim_paths: True when generated prim paths follow project conventions.
        extras: Additional feature flags from rule extensions.
    """

    has_required_imports: bool = False
    follows_safe_xform: bool = False
    no_deprecated_apis: bool = False
    sensible_prim_paths: bool = False
    extras: Dict[str, Any] = field(default_factory=dict)


def deterministic_critique(features: CritiqueFeatures) -> Dict[str, Any]:
    """Score features deterministically. Returns critique dict."""
    score = 0.0
    weights = {
        "has_required_imports": 0.25,
        "follows_safe_xform": 0.25,
        "no_deprecated_apis": 0.25,
        "sensible_prim_paths": 0.25,
    }
    for key, weight in weights.items():
        if getattr(features, key, False):
            score += weight
    return {
        "score": score,
        "passed": score >= 0.75,
        "weights": weights,
        "features": features.__dict__,
    }
