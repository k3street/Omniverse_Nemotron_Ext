# Canonical Migration Phase — Closeout

**Date:** 2026-05-16
**Session span:** 2026-05-15 ~14:00 → 2026-05-16 ~11:30 (with reboots)
**Branch:** refactor/2026-05-12-foundation-night-1

---

## 1. Coverage achieved

| Metric | Start (pre-session) | End (post-R18 + cleanup) |
|---|---:|---:|
| T1 templates total | 109 | **108** (CP-NEW-peg-in-hole-single deleted as confirmed duplicate of CP-58) |
| T1 role-migrated | 5 (CP-01..05) | **84** |
| **Coverage** | 4.6% | **77.8%** |
| Deferred (with explicit reason) | 0 | 24 |
| Lint ERRORs | 17 | 0 |
| Lint OK templates | 212 | 266 |

## 2. Deferred remaining (24 templates, all with specific reason)

| Reason | Count | Why structurally unmigratable |
|---|---:|---|
| `draft` | 17 | Wilson lower-bound < 0.5; insufficient run evidence. Resolves when Kit RPC runs the templates and they cross threshold. |
| `asset_blocked` | 3 | Requires NVIDIA Nucleus assets (G1, etc.) not available locally. |
| `train_pattern_*` (3 variants) | 3 | No robot to bind / no delivery success-criterion / diagnostic-only — structurally don't fit role-based shape even with `pattern_hint=other`. |
| `blocked` | 1 | CP-06 PickPlaceController FixedJoint integration bug; needs upstream fix. |

All 24 deferred with specific reason in `migration_deferred.reason` — no template in undefined limbo.

## 3. Architectural decisions landed

### Schema
- `pattern_hint` enum extended: original 4 + `insert, train, other` (R12)
- `routing_axis` extended: original 4 + `semantic_class` (R12)
- New fields: `motion_controllers`, `migration_deferred`, `qa_status`
- Lint script + 30+ rules + CI gate

### Retrieval
- R3-A: wired `code_template` dispatch in `execute_template_canonical`
- R3-B: `_rehydrate_cache()` populates cache on persistent-index load
- R15d: soft-filter hybrid (full corpus + boost matching templates ×1.15)
- R17b: production default is now `MULTIMODAL_TEXT_INTENT=soft`
- R11: `motion_controllers` filter param (not yet wired into orchestrator entry-point)
- R13: loop_substitution in `substitute_role_placeholders` for >12-cube templates

### Quality
- Wilson-based `verified_status` on 15 CPs with structured `verified_runs={passes, n, lower, upper, evidence}`
- Ghost-corpus fix: 30 stale TP-* IDs remapped to real CPs
- 3 workflow types registered (Track G)

## 4. Benchmark progression (100-prompt corpus)

| Mode | hit@1 | hit@3 | latency p50 |
|---|---:|---:|---:|
| Embedding-only baseline | 0.820 | 0.950 | 102ms |
| Hard-filter (R17, regression) | 0.790 | 0.890 | 105ms |
| **Soft-filter hybrid (production default)** | **0.840** | 0.940 | **98ms** |

## 5. Operational — Bun 1.3.14 crash mitigations

Two layers (both committed; real fix awaits Bun 1.3.15 upstream):
- **L1**: batched-sleep in `_rehydrate_cache` + `_build_index` (1ms/32 templates)
- **L2**: `.claude-session-guardrails.md` — explicit no-pytest-on-ChromaDB list for Claude's Bash
- **Diag**: `~/.claude/diag/log_op.sh` captures mem/load/processes; persistent log at `~/.claude/diag/session-log.txt`

## 6. F.0 backlog status

100 canonical candidates in `config/canonical_backlog.yaml`. ~55 locally-runnable.
Distribution: 30 yrkesroll / 20 industrial / 15 research / 15 RL / 10 GR00T / 10 ROS2.

## 7. Recommended next-phase options

### A — Canonical CREATION pipeline (toward 1000 target)
Pick tier-1 backlog items, draft → equivalence-gate → function-gate.
Realistic rate 35-50/week if Kit RPC available.

### B — Draft drain (Wilson threshold)
17 deferred drafts need more QA runs. Requires Kit RPC.

### C — CP-06 blocker investigation
PickPlaceController FixedJoint integration. Unknown scope.

### D — Retrieval improvements
- Wire motion_controllers filter into orchestrator entry-point (R11b)
- Add prefer_verified ranking boost
- 200-prompt benchmark

### E — Continuous autonomous loop (cron)
Every 30 min: pick highest-leverage task, dispatch 1 agent, commit, return.

## 8. Ratchet baselines (CI should fail if regressed)

- Lint ERRORs ≤ 0
- R1_MISSING_INTENT ≤ 24
- Equivalence test suite ≥ 85 passing
- 100-prompt benchmark hit@1 ≥ 0.84
- 100-prompt benchmark hit@3 ≥ 0.94

---

End of migration phase. Migration done for everything we can do without external resources. Cron loop will pick from §7 options.
