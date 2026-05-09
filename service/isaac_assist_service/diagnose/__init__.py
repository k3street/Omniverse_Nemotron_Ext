"""Pre-flight constraint validator for scene-design feasibility.

Implements `diagnose_scene_feasibility` (Master Plan Phase 1).

Sub-modules:
- `messages` — canonical Swedish/English violation message templates (no LLM paraphrase)
- `cache` — scene-graph-hash keyed result cache (TTL 60s + mutation-tracked invalidation)
- `schema` — Constraint / Violation / verdict types (mirrors robotics_lab)
- `metrics` — per-axis metric computation (ik_feasible, manipulability, etc.)
- `tool` — handler that orchestrates metric computation + violation classification
"""
