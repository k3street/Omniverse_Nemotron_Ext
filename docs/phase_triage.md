# Phase scaffold triage — 2026-05-13

Honest classification of the remaining metadata-only scaffolds.

## Method

A module is "landed" if it has real working logic (data registry,
SQLite schema, regex patterns, deterministic computation). A module
is "scaffold" if it has only the metadata pattern (`PHASE_STATUS =
"scaffold"` + `get_phase_metadata()`).

## Current status (after Phase 8d + 10 + 14 + 16 + 6 upgrades)

| Status | Count |
|---|---:|
| landed (real implementation) | 57 |
| scaffold (metadata only) | 70 |
| **TOTAL** | **127** |

## Triage of remaining 70 scaffolds

### Group A — convertible without runtime (target: convert next)

Phases that can be real implementations using only pure Python:

- Phase 61 `sdg_correlated_dr` — multivariate normal sampling for sensor-camera correlation
- Phase 67-69 spawn validators (`_joint`, `_schema`, `_contact`) — USD-prim shape inspectors
- Phase 70 `assemble_robot` — sub-assembly composition logic
- Phase 73 sensor catalog expansion — large data dict
- Phase 74 sensor catalog query — filter helpers
- Phase 78 IsaacLab arena leaderboard — JSON registry
- Phase 81 multi-rate physics — config dataclass
- Phase 83 governance overnight patches — policy_engine wrapper
- Phase 84 per-session QA logging — log buffer + ring-flush
- Phase 85 MCPResult type discrimination — Literal-typed dataclass
- Phase 86 settings exposure MCP — settings registry
- Phase 91-92 audit log retention / workflow snapshot retention — TTL policies
- Phase 93 proactive_check deepening — trigger ladder rules
- Phase 94 KB feedback loop — feedback writer
- Phase 96 quarterly audit — script of the audit
- Phase 97 performance regression CI — benchmark spec
- Phase 98 documentation polish — meta-task
- Phase 100 arena benchmark spec
- Phase 103 onboarding tutorial — markdown content
- Phase 105 public release announcement — markdown content
- Phase 106 post-release retrospective — template

Plus sub-letters: 24b, 25b, 31b, 47b, 56b, 56c, 60b, 62b, 70b, 70d,
72b, 72c, 78b, 78c, 80b-c, 81b-c, 85b, 88b, 94b, 96b, 97b.

### Group B — blocked on runtime (keep as scaffold with explicit note)

Phases that genuinely need Kit RPC, GR00T weights, GPU, external APIs:

- Phase 62 GR00T finetune pipeline — needs GR00T-N1 weights
- Phase 63 + 63b-d execute_contact_sequence_plan — needs Kit RPC + scene
- Phase 64 Eureka run state persisted — needs running Eureka workers
- Phase 66 spawn_validation_usd_ref — needs Kit (USD reference resolution)
- Phase 71 Yaskawa GP25 onboarding — needs Nucleus assets
- Phase 72 setup_assembly_constraint runtime — needs Kit RPC
- Phase 76 vision real Gemini — needs Gemini API key
- Phase 77 vision viewport-hash cache — needs viewport data
- Phase 79 + 79b whole-body control — needs MuJoCo or Isaac Lab GPU
- Phase 80 surface gripper + suction — needs Isaac Sim physics
- Phase 87 stdio MCP shim hardening — needs MCP client integration
- Phase 88 + 88b Linux/Windows CI — needs CI infrastructure
- Phase 89 ROCm + Intel Arc + DirectML — needs that hardware
- Phase 95 RAG NVIDIA scraping — needs network + NVIDIA docs
- Phase 99 pick-hold-weld scenario — needs Kit + multi-arm setup
- Phase 101-102 binary releases — needs build infra
- Phase 63c contact-seq pose estimation — needs Kit + vision

### Group C — TypeScript / web (Python touchpoint not applicable)

Phase 23 (snap engine), 24 (confirm bar), 29 (canvas mirror panel).
Documented in `docs/phase_scaffolds/phase_23_24_29_web_only.md`.

## Honest verdict

The scaffold-first approach gave 100% spec coverage in form. But the
real coverage is:

- ~57 phases truly landed
- ~30 phases convertible to real implementations without runtime
  (Group A — next target)
- ~30 phases blocked on runtime (Group B — daytime work)
- 3 phases TypeScript-only

Realistic Python-side ceiling: ~87/145 = 60% truly-landed coverage,
assuming all of Group A is converted. The remaining 40% requires
runtime work.
