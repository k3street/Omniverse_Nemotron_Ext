# Proposal: Deprecations / cite-facts **INDEX** (not RAG)

**Status:** proposal, not implemented. Written 2026-04-19 as a placeholder for when keyword-rule coverage (option 1 below) saturates.

**Important terminology correction:** this is an **index**, not RAG. Difference matters:

|  | **Index** (what we want) | **RAG** (what we don't want here) |
|---|---|---|
| Corpus | 50–200 curated rows | MB of docs |
| Retrieval | Keyword/tag lookup | Embedding-similarity |
| Output to LLM | Structured exact facts | Text chunks to synthesize |
| Right when | Agent must cite EXACT names | Agent needs conceptual context |

For T-13-style cite tasks, we need `enable_deterministic_mode` written verbatim. Index returns `{tool_5x: "enable_deterministic_mode"}` — no paraphrase possible. RAG would embedding-match "deterministic replay" and hand the LLM text-chunks from which the LLM may or may not extract the exact name (and on Gemini 3 Flash, empirically, often doesn't).

The rest of this doc uses "index" throughout.

## Problem

Tasks like T-13 (Thomas: "give me a cite-able statement about deterministic replay — name the specific 5.x tool, flag the deprecated 4.x API") fail even on Gemini 3 Flash multi-turn. The model does not internalize Isaac Sim 5.x API details + 4.x deprecations. Similar shape:
- T-13: deterministic replay → `enable_deterministic_mode`, `SimulationContext.set_deterministic` deprecated
- Y-04-style: render API migration (OmniKit.render → isaacsim.replicator)
- S-04-style: LIDAR config migration
- ROS2 bridge namespace changes (`omni.isaac.ros2_bridge` → `isaacsim.ros2.bridge` → `isaacsim.ros2.nodes`)
- MotionCommander constructor changes across 5.x minor versions
- Any deprecated 4.x import path

Common thread: the agent needs a *specific cite* (tool name, deprecated API name, migration path) that the LLM doesn't know deterministically.

## Option 1 (implemented 2026-04-19) — keyword-triggered rule blocks

`context_distiller.RULE_*` blocks injected on keyword match. Cheap, model-agnostic, deterministic. Scales to maybe 10-20 domains before the RULE_BASE length becomes a problem.

Limit: each new domain needs a hand-written rule. Facts go stale as Isaac Sim evolves.

## Option 2 — deprecations / cite-facts RAG index (this doc)

### Shape

A retrievable table of (query_keywords, cite_fact) rows, exposed via `lookup_knowledge` or a new dedicated tool `lookup_api_deprecation`. Each row:

```json
{
  "id": "det_replay_5x",
  "keywords": ["deterministic", "repeatable", "bit-identical", "replay", "CI regression"],
  "domain": "physics",
  "isaac_version": "5.x",
  "tool_5x": "enable_deterministic_mode",
  "deprecated_4x": ["SimulationContext.set_deterministic",
                    "SimulationContext.is_deterministic"],
  "cite": "Deterministic replay in Isaac Sim 5.x is set up via enable_deterministic_mode(seed=...); it authors TGS solver + fixed timestep + CPU dynamics onto /World/PhysicsScene. The 4.x SimulationContext.set_deterministic method was removed in 5.0. PhysX float ordering is still hardware-dependent, so CI runners must be pinned (same GPU model + driver) for bit-identical replay.",
  "archive_protocol": "Pin Kit build hash, solver config, and seed alongside the USD stage in CI artifacts.",
  "references": ["https://docs.omniverse.nvidia.com/isaacsim/latest/migration/v4_v5.html"]
}
```

### Storage + retrieval

- **Storage:** `service/isaac_assist_service/data/api_deprecations.jsonl`, one row per fact. Keep in source (not a separate DB) so it ships with the service.
- **Index:** on service startup, build a Whoosh or ChromaDB index keyed on `keywords`. Small corpus — 50-200 rows — so a naive keyword scan also works.
- **Retrieval:** new tool `lookup_api_deprecation(query="deterministic replay")` returns the top-k matching rows. Alternatively extend `lookup_knowledge` to search this corpus when the query mentions version-migration terminology.

### Integration with orchestrator

On intent classification, if the user message matches deprecation-sensitive patterns (version words, "deprecated", "5.x", "replaced", "cite", "safety case", "CI"), automatically inject the top-1 matching cite-fact into the distilled context — same slot as `RAG_TEXT`. Makes the agent's reply carry the canonical phrasing verbatim.

### Ownership / refresh

- Who curates: release-coordinator adds a row for every 4.x→5.x API migration discovered during an upgrade cycle.
- CI guard: lint that `tool_5x` names actually exist in `tool_schemas.py`.
- Stale-fact guard: reject rows whose `isaac_version` is older than the current-supported minimum.

### Scope boundary

This is **cite-fact retrieval**, NOT:
- Scene-state memory (that's `ConversationKnowledge`)
- Tool-schema docs (that's `tool_schemas.py`)
- Long-form concept docs (that's user-facing documentation)

A row in the deprecations index should be ~3 sentences, quotable verbatim, version-stamped.

### Validation suite

When built, run these as pass-targets:
- T-13 (deterministic replay) — must mention `enable_deterministic_mode` + `SimulationContext.set_deterministic` deprecation
- A new T-15 (ROS2 bridge namespace migration) — must mention `isaacsim.ros2.nodes` vs `isaacsim.ros2.bridge`
- A new task for 4.x→5.x import-path migration spreading over multiple domains

If each cites the right fact verbatim, RAG is working. If judge still says "missing specific API", the retrieval is underpowered or the fact row needs sharpening.

### Effort estimate

- Data: ~50 rows authored over a day of deep Isaac Sim 5.x documentation reading (human work, not agent)
- Code: ~200 lines for loader + retriever + optional new tool
- Test: existing cite-task harness

Blocked on: deciding who curates + where to source the ground-truth migration list. Not a technical blocker; an editorial one.

### When to build it

**Not now.** Option 1 (keyword rules) covers T-13 today. Build the RAG index when:
1. We hit 5+ domain-specific cite tasks that all need different migration facts
2. The `_KEYWORD_RULES` map in context_distiller.py has grown past 15-20 entries and feels unscalable
3. A release-coordinator takes ownership of maintaining the deprecations corpus

Until then, this doc is the north star.
