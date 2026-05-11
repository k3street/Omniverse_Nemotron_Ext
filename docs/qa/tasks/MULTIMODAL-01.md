# Task MULTIMODAL-01 [DEFERRED] — Sketch input

**Status:** Deferred. Modality-parser does not exist yet. Build
SORT-01, CONSTRAINT-01, REORIENT-01 first; MULTIMODAL-01 is purely
additive on top of working canonical+task pattern.

**Persona:** common workflow

**Eventual goal:** User uploads a 2D sketch (PNG/JPG) showing the
intended robot cell layout — robot positions, conveyor paths, bin
locations, station markers. The system parses the sketch into a
structured spec (the same kind that canonicals like CP-02 use), and
the existing canonical+task pipeline executes from that spec.

## Why this task exists

MULTIMODAL-01 probes input-pipeline breadth — can the system accept
a different input modality (image instead of text) and route it
through the same execution path? It's a generalization probe, not
a fundamentally new pattern.

## Required infrastructure (currently absent)

1. **Sketch parser** — a vision pipeline that:
   - Detects shapes representing robots, conveyors, bins, cubes
   - Reads coordinate annotations (e.g., "x=−3", "x=+3" labels)
   - Identifies orientation arrows / motion arrows
   - Outputs structured JSON spec

2. **Spec → canonical retrieval bridge** — the structured spec from
   the sketch should match against the same template index, so
   CP-02 etc. become eligible canonicals when the sketch describes
   a multi-station assembly line.

3. **Validation tool** — verify the parsed spec is well-formed
   before passing to the canonical+task pipeline.

## Once available

Same form + function dual-gate success criterion as VR-19, scoped to
whatever the parsed sketch specified. Plus a parser-quality criterion:
the parsed spec's `goal` field should match the user's intent (judged
by a small LLM call).

## Out of scope for current roadmap

This task is a placeholder. The first four canonicals (CP-01..05) plus
the four working tasks (VR-19, SORT-01, CONSTRAINT-01, REORIENT-01)
must be solid before the multi-modal generalization is worth building.
