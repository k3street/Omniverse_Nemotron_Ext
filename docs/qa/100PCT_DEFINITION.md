# 100% definition — Isaac Assist as of 2026-05-14

Single source of truth for "what does 100% kvalitet mean for this codebase
right now, and where are we against that bar?". Generated after Fas 0
audit-validation, Tier 2/3 data extraction, and Gemini-smoke scaffolding.

For methodology, see `QUALITY_AUDIT_METHODOLOGY.md`.

---

## Snapshot

- Branch tip: pushed to `anton/refactor/2026-05-12-foundation-night-1`
- Pytest baseline: 6550 pass / 0 fail (50 deselected: compliance_e2e + gemini_live)
- Audit-script validation: 15/15 fixture tests pass
- Tier 1 PASS-rate: **7/12** (vs 4/12 before Fas 0 — gain was eliminating false-positives, not project fixes)

---

## Tier 1 — deterministic CI gate

Run: `python scripts/qa/post_migration_health_check.py`. Validation
fixtures: `tests/qa_audit_fixtures/`. Each check is documented in
`AUDIT_VALIDATION.md` with FP/FN risk + heuristic notes.

| ID | Question | Data source | Status | Action |
|---|---|---|---|---|
| Q3 | Zero `datetime.utcnow()`? | AST scan | ✅ PASS (0) | — |
| Q4 | Zero `asyncio.get_event_loop()` outside `run_stdio`? | AST + whitelist | ✅ PASS (0) | — |
| Q9 | Zero `eval`/`exec` in non-test? | AST scan | ❌ FAIL (1) | `canonical_instantiator.py:527` — legitimate `exec()` of generated Kit code; needs `# noqa: audit-Q9` |
| Q10 | Zero `subprocess(..., shell=True)`? | AST scan | ❌ FAIL (1) | `fingerprint/collector.py:30` — review sanitisation; either fix or noqa |
| Q12 | Zero blocking I/O in `async def`? | AST scan (scope-aware) | ❌ FAIL (2) | `bridge_tools.py:367 time.sleep` → `asyncio.sleep`; `robot.py:5132 open` → `asyncio.to_thread` |
| Q14 | Every schema has a backing handler? | AST scan over 6 binding patterns | ✅ PASS (0) | — |
| Q15 | All non-trivial defs/classes have docstrings? | `ast.get_docstring()` | ❌ FAIL (309) | Sweep — large surface; Wave 5b in plan |
| Q17 | All modules ≤500 LOC (excl `_models.py`)? | `wc -l` | ❌ FAIL (42) | Judgement: refactor cohesive vs split; Wave 5c in plan |
| Q18 | Zero circular imports on top-level modules? | subprocess import test | ✅ PASS (0) | — |
| Q19 | Handlers only import `_shared/_models/_state/constants`? | AST relative-import scan | ✅ PASS (0) | — |
| Q21 | Every `_handle_*` returns or raises (no `return None`, no fall-through)? | AST scope-aware return-walker | ✅ PASS (0) | — |
| Q21b | No `{"success": False}` without error-info key? | AST dict-return scanner | ✅ PASS (0) | — |

**Tier 1 fail summary:** 355 genuine project issues across 5 checks. All
documented as known starting state — no spurious noise from heuristic
bugs.

---

## Tier 2 — partial-determinism scanners

Run: `python scripts/qa/tier2_<name>.py`. Output is JSON. Heuristic-based
— false positives expected.

| ID | Question | Data | Current state |
|---|---|---|---|
| T2.1 | Any module-state read in prod but never written there? | `tier2_dead_read_scan.py` | 7 candidates (mixed real + heuristic-FP); review needed |
| T2.2 | Every handler has at least one error-path test? | `tier2_error_path_coverage.py` | **11.7%** coverage. 172/230 handlers have zero test mentions. 31 mentioned but no error assertion |
| T2.3 | Zero O(n²) stage-loop patterns? | `tier2_quadratic_stage_loops.py` | **0** candidates |
| T2.4 | Every handler emits at least one telemetry event? | `tier2_telemetry_coverage.py` | **0.9%** coverage. 228/230 handlers are telemetry-blind |

**Tier 2 insights:**
- T2.2 + T2.4 are large gaps. They are not "bugs" in the Tier 1 sense
  — the code works — but they make production observability and
  regression catch-rate weaker than they could be.
- T2.1's 7 candidates have 3–4 likely-real dead reads (the rest are
  method-call FPs); needs a 30-minute manual triage.

---

## Tier 3 — judgment-data extractors

Run: `python scripts/qa/tier3_<name>.py`. Output is JSON. Designed to
inform judgment, not answer it.

| ID | Question | Data extracted | Top findings |
|---|---|---|---|
| TI.1 | Is the architecture sound? | `tier3_dependency_graph.py` (337 nodes, 547 edges) | 1 cycle (inspect needed); fan-in hub `chat.tools` (158); top fan-out: `robot.py` (34), `diagnostics.py` (31), `orchestrator.py` (29) |
| TI.2 | Where is the refactor pressure? | `tier3_cochange_hotspots.py` (500 commits) | Top 5 co-change pairs all `<theme>.py ↔ tool_executor.py` — migration artefact from Wave 1-4 (clean signal, not coupling) |
| TI.3 | Which functions are too complex? | `tier3_complexity_histogram.py` | 2054 functions; median CC=2; max CC=**312** (`orchestrator.handle_message`); 90 over CC=15 (5%); 26 over CC=25 (1%) |

**Tier 3 insights:**
- `orchestrator.handle_message` CC=312 is the standout refactor target.
- TI.2 confirms the Wave 1-4 monolith→themed-module migration is the
  source of churn, not hidden coupling.
- TI.1's 1 cycle should be inspected and resolved.

---

## Out-of-tier — opt-in live smoke

`tests/gemini_smoke/test_gemini_provider_smoke.py` — 2 tests under
`@pytest.mark.gemini_live`. Default skipped. Catches Gemini API auth /
quota / JSON-mode regressions. Not part of CI; manual invocation when
Gemini-related project bugs are being investigated.

---

## What "100%" would look like

For each tier the bar is different:

- **Tier 1: 12/12 PASS.** Today: 7/12. To close: address 5 genuine
  fail-classes (Q9, Q10, Q12, Q15, Q17). Q15 + Q17 are the bulk of work.
- **Tier 2: every counter trending toward "good" with a documented
  threshold.** Today: T2.2 11.7% → target ≥80%; T2.4 0.9% → target
  ≥95%; T2.1 candidates triaged to 0 (or noqa'd); T2.3 already 0.
- **Tier 3: not a pass/fail.** Data must be re-runnable. Used when
  starting a refactor or assessing architecture health.

---

## Cost model

- Tier 1 audit: <3 seconds, 0 tokens, 0 external calls. Suitable for
  per-commit CI gate.
- Tier 2 scanners: 5–30 seconds each, 0 tokens. Weekly / on-demand.
- Tier 3 extractors: 5–60 seconds (TI.2 hits git log), 0 tokens. On-demand.
- Gemini smoke: ~400 tokens per run (~$0.0001). Manual + opt-in.
- LLM-driven judgment session reading Tier 3 JSON: ~5–20k tokens
  depending on scope. Cheap because data is already extracted.

---

## Next session (max-effort fix-pass) checklist

When you next sit down to "actually fix the fails":

1. Run Tier 1: `python scripts/qa/post_migration_health_check.py`
2. For each fail-class in order of cost:
   - Q9, Q10 (1 each — choose noqa or fix in 30 min)
   - Q12 (2 — replace blocking calls with async equivalents)
   - Q15 (309 missing docstrings — sweep agents per module)
   - Q17 (42 oversized modules — judgement: split cohesive vs threshold)
3. Re-run Tier 1, expect 12/12.
4. Optional: Tier 2 ratchets. Pick top T2.2 + T2.4 gaps, schedule.
5. Tier 3 refactor pass: target `orchestrator.handle_message` CC=312
   first.

The audit infrastructure is now stable. Future sessions don't have to
re-derive "what does 100% mean" — they just have to address the bar
this doc declares.
