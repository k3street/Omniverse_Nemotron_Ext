# ChromaDB Test-Suite Strategy

**Date:** 2026-05-16
**Status:** spec, awaiting trigger condition
**Owner:** Anton + autonomous agents
**Trigger:** execute when Anthropic ships Claude Code bundling Bun 1.3.15+
(or another release that no longer crashes on ChromaDB pytest workloads)

---

## 1. Problem

Anton's Claude Code session (currently 2.1.143, bundled Bun 1.3.14) has
a known GC SlotVisitor::drain crash that triggers on pytest runs against
ChromaDB-touching test files. 3+ confirmed correlations 2026-05-15/16.
See `feedback_bun_chromadb_pytest_avoid.md` memory and
`.claude-session-guardrails.md` in the repo root.

While this bug is upstream-Bun and we wait for Anthropic to ship Bun
1.3.15+, Claude's main process cannot safely run heavy ChromaDB pytest.
Agent processes (separate Bun instance) CAN safely run them — this is
the workaround we've been using.

This spec defines the standing strategy for ChromaDB test coverage
during the Bun-crash window AND after it lifts.

## 2. Why ChromaDB tests are critical

ChromaDB is the **production retrieval backend** for every user prompt:
prompt → embedding → ChromaDB top-K → template match → response.

Without continuous ChromaDB test coverage we cannot verify:
- Retrieval-related code changes don't silently regress
- Soft-filter behavior remains correct after structural changes
- Cache rehydration (R3-B) still populates correctly
- Motion-controllers filter (R11/R11b) actually filters end-to-end
- Embeddings produce expected ranking on the corpus
- ChromaDB persistence + index reload remain consistent

Lint and parameter-logic tests catch many bugs but DO NOT cover any of
the above.

## 3. ChromaDB-touching test files (in scope for this strategy)

Confirmed via grep for chromadb / template_retriever / template_cache /
retrieve_with_intent / _rehydrate_cache imports:

```
tests/test_motion_controller_retrieval_filter.py
tests/test_qa_stats.py
tests/test_retrieval_benchmark.py
tests/test_retrieval_r17_100prompts.py
tests/test_retrieval_struct_filter.py
tests/test_soft_filter_retrieval.py
tests/test_struct_filter_query_construction.py
tests/test_template_cache_rehydration.py
tests/test_text_modality_integration.py
tests/test_mc_filter_orchestrator_wire.py  (added R11b)
```

Plus the R17 100-prompt benchmark harness:
```
tests/test_retrieval_r17_100prompts.py  (also a script)
workspace/benchmarks/retrieval_100prompts.json (input)
workspace/benchmarks/retrieval_100prompts_*.json (frozen output snapshots)
```

## 4. Strategy by Bun status

### 4.A — While Bun 1.3.14 is bundled (NOW)

Three operational modes for running these tests:

**Mode 1: Agent-driven (recommended for ad-hoc)**

Dispatch a sonnet agent with explicit brief to run the full suite +
benchmark INSIDE its own process. Agent's Bun is separate from Claude's
main process — safe.

Reference precedent: commit 7db94e2 "Test-suite validation: GREEN
(265/265 tests, ratchet baselines met) + harness fix" used this approach
and successfully:
- Ran 12 ChromaDB-touching test files (265 tests, 0 failures)
- Re-ran R17 100-prompt benchmark in both soft + baseline modes
- Found and fixed a corpus-shape harness bug

Cadence: after each batch of retrieval-affecting commits (R-series, A-series,
R11-family). Estimated ~weekly during active development.

**Mode 2: System-cron (recommended for autonomous)**

Schedule via `crontab -e`:
```cron
# Nightly ChromaDB test validation — runs outside Claude session
30 02 * * * cd /home/anton/projects/Omniverse_Nemotron_Ext && \
  /home/anton/miniconda3/bin/python -m pytest tests/test_template_cache_rehydration.py \
    tests/test_motion_controller_retrieval_filter.py \
    tests/test_struct_filter_query_construction.py \
    tests/test_soft_filter_retrieval.py \
    tests/test_mc_filter_orchestrator_wire.py \
    --override-ini="addopts=" -q --no-header \
    > /home/anton/.claude/diag/chromadb_test_nightly.log 2>&1
```

Runs at 02:30 AM local time, writes log to persistent diag dir. Anton
reviews log in morning if curious. Runs OUTSIDE Claude entirely — no
Bun crash risk.

**Mode 3: Manual (recommended for one-off)**

Anton runs `pytest` directly from a fresh terminal outside Claude
when verifying a specific change. Enklast, mest selektiv.

### 4.B — When Bun 1.3.15+ ships (FUTURE)

Restore normal pytest-in-Claude discipline:
1. Delete `.claude-session-guardrails.md` from repo root
2. Remove the memory note `feedback_bun_chromadb_pytest_avoid.md`
3. Remove the batched-sleep mitigation from `_rehydrate_cache` +
   `_build_index` (or keep it — overhead is ~10ms, harmless)
4. Resume running `python -m pytest tests/` freely from Claude's Bash

Verification step: run the full suite once via Claude's Bash. If no
crash → mitigations can be lifted.

## 5. Ratchet baselines

Per `docs/research/2026-05-16-migration-phase-closeout.md` §8 — these
are the floors. Any future change that breaks them must be investigated:

- Lint ERRORs ≤ 0
- R1_MISSING_INTENT ≤ 24
- Equivalence test suite ≥ 85 passing
- 100-prompt benchmark hit@1 ≥ 0.84 (soft-filter, production default)
- 100-prompt benchmark hit@3 ≥ 0.94 (soft-filter)
- 100-prompt benchmark hit@1 ≥ 0.82 (legacy embedding-only baseline)
- 100-prompt benchmark hit@3 ≥ 0.95 (baseline sanity)
- Full ChromaDB suite ≥ 265 tests passing

## 6. Trigger conditions

Execute Section 4.B (lift mitigations + restore normal pytest) when:
- `strings $(readlink -f $(which claude)) | grep -i "^bun v1.3"` shows
  v1.3.15 OR v1.3.16 OR newer
- AND a verification run of `pytest tests/test_template_cache_rehydration.py`
  from Claude's Bash completes without crashing Claude

Until both true: continue using Mode 1 (agent-driven) for active dev
+ optionally Mode 2 (system-cron) for regression coverage.

## 7. When to point at this spec

- During active development if questions arise about how/when to run
  ChromaDB tests
- Before lifting any Layer-1 or Layer-2 Bun mitigation
- When designing a new ChromaDB-touching test file (consider whether
  it should be in the "heavy" set or whether logic-only equivalent
  exists)
- Periodic check: is Bun upstream version current? Does Anthropic's
  Claude Code now bundle a fixed Bun?

## 8. Out of scope

- Fixing Bun itself (upstream Anthropic + Bun team responsibility)
- Replacing ChromaDB with a different vector store (separate decision)
- Reducing total ChromaDB test surface (the tests exist for good reasons)

---

End of spec. Update when Bun version moves, or strategy changes.
