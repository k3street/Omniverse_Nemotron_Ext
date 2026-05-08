# Multimodal Foundation — Working Draft

Authored 2026-05-08. This document captures the design principle and proposed
intermediate representation (IR) for Isaac Assist's multimodal layout-input
architecture. It is the starting point for three Opus agent investigations into
specific deep questions.

This is **not a spec**. It is a position document for sharper analysis.

---

## Context

The 2D Floor Plan Tool spec (`docs/specs/2026-05-08-floor-plan-tool-spec.md`)
plus its critique (`docs/specs/2026-05-08-floor-plan-tool-opus-critique.md`)
revealed that the floor plan tool is one modality among several that should
converge into a single canonical-pipeline. The honest framing is **multimodal
layout-input architecture**, not "floor plan tool."

Modalities (existing or planned):
- Text prompt (current, primary)
- Sketch upload via VLM parsing (deferred; Robotics-ER 1.6 viability uncertain)
- Structured drag-drop (was the floor plan tool)
- Photo of real environment (future)
- Voice (LiveKit infrastructure exists)
- 3D viewport direct manipulation (future)

All must converge on the existing canonical-pipeline (hard-instantiate +
verify_pickplace + simulate_traversal + free-form fallback).

---

## The Design Principle

**Regex-family fragility lives at every lossy translation point.**

The prior critique identified five surfaces in the floor plan spec where
pattern-matching sneaks back in: auto-query-NL-synthesis, layout-intent regex,
free-text notes parsed by LLM, `_patch_verify_args` string substitution,
auto-suggest classifier. These are not architecturally required — they are
path-of-least-resistance choices when translating between structured input and
natural-language retrieval.

The principle:

> **Every modality produces structure. Every transformation preserves structure.
> Translation between modalities never goes through natural language as an
> intermediate. Roles are first-class; names are display-only.**

If this principle holds throughout, regex doesn't have a place to live.

---

## Proposed IR — `LayoutSpec`

A common JSON structure that all modalities target and the canonical pipeline
consumes:

```typescript
interface LayoutSpec {
  version: "1.0";

  intent: {
    pattern_hint:    PatternHint;       // enum, see below
    counts:          {
      robots:    number;
      conveyors: number;
      bins:      number;
      cubes:     number;
      sensors:   number;
      // ... fixed, additive
    };
    structural_tags: StructuralTag[];   // controlled vocabulary
  };

  objects?:     TypedObject[];          // present when modality has positions
  constraints?: Constraint[];           // present when known
  parameters:   Record<string, JSONValue>;  // T2 substitution

  source: {
    modality:     Modality;
    confidence:   number;               // 0..1
    raw_input?:   unknown;              // optional original — text, image, audio
    metadata:     Record<string, unknown>;
  };
}

type PatternHint =
  | "pick_place"
  | "sort"
  | "constraint"
  | "reorient"
  | "navigate"
  | "custom";

type StructuralTag = string;   // intentionally not enumerated here; see Question A

type Modality =
  | "text"
  | "sketch"
  | "drag_drop"
  | "photo"
  | "voice"
  | "viewport";
```

Three critical design choices:

1. **`intent` is structured, not natural language.** No "two-robot assembly
   line" string. `counts` is integers. `pattern_hint` is enum. `structural_tags`
   is controlled vocabulary. Retrieval can match this directly without NL
   synthesis.

2. **`objects` and `constraints` are optional.** Text-prompt modality may not
   produce positions. Sketch/drag-drop do. Canonical pipeline parameterizes
   from positions when present (T2 path); falls to T5 free-form when absent or
   low-confidence.

3. **`source` carries provenance.** Sketch from VLM with confidence 0.7 vs
   drag-drop from user with confidence 1.0 vs text-prompt from LLM with varying
   confidence. Downstream can give more or less trust to positions; can re-derive
   when confidence is low.

---

## Role-based canonical templates

Templates declare named roles instead of hardcoded prim paths:

```json
{
  "id": "CP-01",
  "roles": {
    "primary_robot": {
      "constraints": ["franka_panda", "ur5e", "kinova_gen3"],
      "required": true,
      "expected_count": 1
    },
    "primary_conveyor": {
      "constraints": ["conveyor"],
      "required": true
    },
    "primary_destination": {
      "constraints": ["bin"],
      "required": true
    },
    "workpieces": {
      "constraints": ["cube"],
      "min": 1,
      "max": 8,
      "param_name": "n_cubes"
    }
  },
  "code_template": "...uses {{primary_robot.path}} not /World/Franka literal...",
  "verify_args_template": "...also role-based...",
  "settle_state_template": "..."
}
```

Modality producer's job: bind objects to roles. Floor plan UI: user explicitly
selects "this is the primary_robot." Sketch VLM: prompt-driven role
identification. Text-prompt LLM: role binding from prompt context.

Role bindings are a **structured field in LayoutSpec**, not runtime string
substitution. No regex.

---

## Sequencing implication

Right ordering for building this:

1. **Define LayoutSpec IR.** Pure data format. Schema, validation, examples.
2. **Refactor canonical templates to role-based.** CP-01..CP-05 get `roles` +
   `code_template` + `verify_args_template`. Tested via existing
   `hard_instantiate_smoke_tests.py`.
3. **Refactor text-prompt modality to produce LayoutSpec.** Today goes direct to
   tool-calls or canonical-match. Insert LayoutSpec as intermediate.
4. **Add sketch modality** via VLM (when viable).
5. **Add structured drag-drop** as native Kit panel (smaller scope once IR exists).
6. **Remaining modalities** (photo, voice, viewport-edit) as new producers
   against stable IR.

The win: after step 3, the text-prompt path runs against the new IR as a pure
internal refactor — measurable before any UI building begins.

---

## The three deep questions

These do not have obvious answers and are the focus of the three Opus
investigations:

### Question A — IR expressivity vs strictness

How fixed should `pattern_hint` and `structural_tags` be?

- Fully closed enum: future patterns (REORIENT, MULTI_FLOOR, COLLABORATIVE)
  cannot be expressed without schema bumps. Risk: schema churn.
- Fully open string: tags become free text, regex-family sneaks back in via
  the back door. Risk: fragility class restored.
- Layered controlled vocabulary: enum + explicit-extension mechanism. How does
  this work without devolving into NL?

### Question B — Role binding protocol

How does a modality bind objects to template roles?

- Drag-drop: user explicitly selects roles via UI.
- Sketch VLM: prompt-driven inference; how reliable; conflict if VLM
  disagrees with subsequent user edits.
- Text-prompt: LLM proposes roles from context; how to gate against
  hallucination; what's the fallback when binding is ambiguous.
- Photo: similar to sketch but with real-world objects.

What is the protocol when the modality cannot fully bind? What's the
deterministic gate vs the LLM-mediated decision?

### Question C — Native Kit panel feasibility

Is `omni.ui.scene` sufficient for CAD-grade canvas (snap, drag, smart guides,
dimension lines, multi-select transformer)? Or is hybrid webview required?

If native is feasible: how does it work concretely?
If not: what is the actual cost and risk profile of hybrid webview?

---

## Reference

- Critique doc: `docs/specs/2026-05-08-floor-plan-tool-opus-critique.md`
- Original spec: `docs/specs/2026-05-08-floor-plan-tool-spec.md`
- Canonical instantiator: `service/isaac_assist_service/chat/canonical_instantiator.py`
- CP-01 template: `workspace/templates/CP-01.json`
- Vision-Gemini provider: `service/isaac_assist_service/chat/vision_gemini.py`
- Chat view (UI reference): `exts/isaac_5.1/omni.isaac.assist/ui/chat_view.py`
