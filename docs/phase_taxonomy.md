# Phase taxonomy — agent-type tagging for parallelization

`specs/phase_metadata.yaml` is the sidecar that tags each of the 145
spec phases by `agent_type` and `status`. Spec body stays clean; tags
evolve independently.

## Agent types

| Tag | Meaning | Concurrency | Examples |
|---|---|---|---|
| `sonnet-mechanical` | Data registries, regex patterns, file moves, scaffolding, doc writing. Body is mostly typing, judgment is minimal. | safe to parallelize (multiple Sonnet agents) | Phase 13 (archive markers), Phase 25 (60 class palette), Phase 47 (rule names list), Phase 73 (sensor catalog), Phase 90 (10 redactor regexes), Phase 103/105/106 (markdown content) |
| `sonnet-bounded` | Single-module refactors, small typed APIs, contract tests where shape is obvious. Judgment exists but is narrow. | safe to parallelize if file paths disjoint (use `scripts/safe_batch.py`) | Phase 39 (SQLite store), Phase 40 (query API), Phase 50 (sensor coverage estimator), Phase 65 (training store), Phase 75 (user registry) |
| `opus-judgment` | Cross-module refactors, design decisions, novel algorithms, dispatch swaps, validator pipelines, stateful migrations. | sequential — Opus handles one at a time | Phase 8 (29 waves), Phase 9 (dispatch swap), Phase 11 (validator pipeline), Phase 15 (workflow stateful), Phase 53 (AVM-1 dual contract), Phase 70 (assemble_robot algorithm) |
| `opus-runtime` | Requires live Kit RPC, GPU, GR00T weights, external APIs (Gemini, MCP clients), Nucleus assets. Can't parallelize at LLM level — needs a runtime slot. | serialized through the Kit RPC singleton | Phase 19 (instantiator), Phase 22 (sync_from_stage), Phase 62 (GR00T finetune), Phase 71 (Yaskawa GP25), Phase 76 (Gemini Vision), Phase 79 (WBC) |
| `TS-only` | Lands in `web/floor-plan-ui/` TypeScript. Python touchpoint N/A. | independent of Python work | Phase 23 (snap.ts), Phase 24 (ConfirmBar.vue), Phase 24b, Phase 29 (MirrorPanel.vue) |

## Current distribution (145 phases)

| agent_type | count | status: landed | status: scaffold |
|---|---:|---:|---:|
| sonnet-mechanical | 27 | 15 | 12 |
| sonnet-bounded | 58 | 30 | 28 |
| opus-judgment | 20 | 19 | 1 |
| opus-runtime | 36 | 0 | 36 |
| TS-only | 4 | 0 | 4 |
| **TOTAL** | **145** | **64** | **81** |

(numbers may drift as work lands)

## Parallelization plan

**Now (this session)**: Opus continues with `opus-judgment` phases that
remain (mainly Phase 70 assemble_robot algorithm) + drives sonnet-style
batches when there's a clean disjoint-file-set window.

**Daytime (recommended next)**:

1. **Sonnet swarm (3-4 agents in parallel)** picks up `sonnet-mechanical`
   phases from the "scaffold" pool. Each agent claims a phase, lands
   it, commits, moves to the next. Use `scripts/safe_batch.py` to
   check file-write conflicts before launching.
2. **Sonnet pair (2 agents)** picks `sonnet-bounded` phases. Slightly
   slower because shape requires more design.
3. **Opus solo** handles `opus-judgment` phases sequentially. These
   are the dispatch-affecting, design-heavy ones.
4. **Kit RPC slot** handles `opus-runtime` phases one at a time. Needs
   a live Isaac Sim session, so parallelism is limited.

## Dispatch contract

A dispatch helper script (`scripts/spawn_phase_worker.py`, TODO) reads
`specs/phase_metadata.yaml`, picks the next unblocked phase matching
the requested `agent_type`, and emits a self-contained brief the
worker agent uses.

Brief shape:

```
Phase {id} — {title}
agent_type: {tag}
spec_ref: specs/IA_FULL_SPEC_2026-05-10.md Phase {id}
status before: {scaffold|missing}
status after: landed (your job)

Required deliverables (from spec body):
{Files (changes)}
{Files (new)}
{LOC estimate}
{Test contract}

Dependencies satisfied: {list}
Parallel-safe with: {list of other in-flight phase IDs}
Conflicts: {file-path conflicts to avoid}

When done:
1. pytest tests/ — must match baseline (2 failed / N passed)
2. Update specs/phase_metadata.yaml to status='landed'
3. git commit with [phase-{id}-LANDED] prefix
4. git push
```

## Building this taxonomy

The user explicitly asked for this after seeing 76 scaffolds vs 64 real
landed phases. Honesty principle: scaffold ≠ done. Taggning gör det
synligt vad som är riktigt klart vs bara form.
