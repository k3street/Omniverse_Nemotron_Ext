"""
Multimodal foundation module.

Provides the LayoutSpec intermediate representation, structural-tags vocabulary
registry, validation, ratify component, persistence layer, and supporting
machinery that lets multiple input modalities (text, drag-drop canvas, sketch,
voice, photo, viewport-edit) converge into the canonical-pipeline.

Spec: docs/specs/2026-05-08-multimodal-foundation-spec.md
Coordination: docs/specs/2026-05-09-multi-session-coordination.md

Design principle: every modality produces structure; every transformation
preserves structure; translation between modalities never goes through
natural language as an intermediate. Roles are first-class; names are
display-only.
"""

from .types import (
    LayoutSpec,
    Intent,
    Counts,
    StructuralFeatures,
    StructuralTag,
    PatternHint,
    Modality,
    BindingSource,
    Source,
    TypedObject,
    RoleBinding,
)
from .vocabulary import (
    StructuralTagRegistry,
    load_default_registry,
)
from .validate import (
    validate_layout_spec,
    LayoutSpecValidationError,
)
from .text_modality import (
    extract_intent_rules,
    extract_intent_llm,
    produce_layout_spec_from_text,
)
from .voice_modality import produce_layout_spec_from_voice
from .vlm_modality import (
    produce_layout_spec_from_sketch,
    produce_layout_spec_from_photo,
)
from .stage_to_spec import (
    classify_prim,
    prims_to_layout_spec,
    sync_from_stage,
)

__all__ = [
    # Types
    "LayoutSpec",
    "Intent",
    "Counts",
    "StructuralFeatures",
    "StructuralTag",
    "PatternHint",
    "Modality",
    "BindingSource",
    "Source",
    "TypedObject",
    "RoleBinding",
    # Vocabulary
    "StructuralTagRegistry",
    "load_default_registry",
    # Validation
    "validate_layout_spec",
    "LayoutSpecValidationError",
    # Text modality (Block 2)
    "extract_intent_rules",
    "extract_intent_llm",
    "produce_layout_spec_from_text",
    # Voice modality (Block 3)
    "produce_layout_spec_from_voice",
    # VLM modalities — sketch + photo (Block 3)
    "produce_layout_spec_from_sketch",
    "produce_layout_spec_from_photo",
    # Viewport / sync-from-stage (Block 3 + IA Phase 22)
    "classify_prim",
    "prims_to_layout_spec",
    "sync_from_stage",
]
