# Path to 100% — Tier 1 audit + plan

**Mål:** Branch `refactor/2026-05-12-foundation-night-1` ska vara
"behave-as-expected, stable, evolvable" enligt deterministisk
audit-definition i `scripts/qa/post_migration_health_check.py`.

**Definition av klar:** `python scripts/qa/post_migration_health_check.py --strict` exit 0.

**Process:**
- Cron fyrar regelbundet. Vid varje wake-up:
  1. Läs denna plan
  2. Hitta första `[ ]` (unchecked) under "Action steps"
  3. Utför stegets fix
  4. Markera `[x]`, commit + push
  5. Stop (vänta på nästa wake-up)
- När alla `[ ]` är `[x]`: kör Tier 1 audit, generera slutrapport, deletera cron.

---

## Baseline 2026-05-14

Senaste audit-resultat — uppdateras manuellt vid varje större fix-pass:

| Check | Status | Count |
|---|---|---|
| Q3 datetime.utcnow | ✅ PASS | 0 |
| Q4 asyncio.get_event_loop | ✅ PASS | 0 |
| Q9 eval/exec | ❌ FAIL | 1 |
| Q10 shell=True | ❌ FAIL | 1 |
| Q12 blocking I/O in async | ❌ FAIL | 2 |
| Q14 schema/handler drift | ❌ FAIL | 35 orphan schemas |
| Q15 missing docstrings | ❌ FAIL | 309 |
| Q17 module size ≤500 LOC | ❌ FAIL | 42 |
| Q18 no circular imports | ✅ PASS | 0 |
| Q19 handlers layer isolation | ✅ PASS | 0 |
| Q21 Section 19 honesty | ❌ FAIL | 1 |
| Q21b silent failures | ❌ FAIL | 3 |
| Total fails | | 360 |

Handler count ratchet: 230 (baseline).

---

## Action steps

**Insight 2026-05-14:** Audit-scriptet är själv subject för "är det 100%?".
Demonstrerad bugg: `check_section_19_honesty` walk:ar Return-noder i
nested helper-defs och flaggar `return None` i hjälpfunktioner som
honesty-hål. False positives = jaga fantomer.

**Konsekvens:** Fas 0 nedan **måste** köras före Wave 5a fix-passes.

### Fas 0 — Validera audit-scriptet (max-effort session start here)

- [ ] **V1 — Skapa positive/negative fixtures per check**: `tests/qa_audit_fixtures/` med tydligt korrekt + tydligt fel kod-snippets. Kör audit på dem, assert expected hit-count per fixture.
- [ ] **V2 — Fix `_direct_returns` scope-isolation**: använd `ast.iter_child_nodes` recursivt men stoppa vid nested `FunctionDef`/`AsyncFunctionDef`/`Lambda`. Verifiera mot V1-fixtures.
- [ ] **V3 — Audit varje check för samma "nested scope" issue**: `check_silent_failures`, `check_no_blocking_io_in_async`, alla AST-walks som scope-överstiger.
- [ ] **V4 — Mutation test scriptet**: medveten introducera 1 bug i varje check, verifiera att test catchar.
- [ ] **V5 — Code review checklist**: false-positive risk, false-negative risk, scope correctness, deterministic-given-input. Bocka av per check.
- [ ] **V6 — Definiera ratchet-tröskel per check**: vissa fails är acceptabla (e.g. 1 legitim `exec` med `# noqa: audit-Q9`-kommentar). Audit ska honor `# noqa: audit-QX`-suppressioner.
- [ ] **V7 — Validate scriptet är 100%**: dokumentera i `docs/qa/AUDIT_VALIDATION.md` att varje check har positive fixture + negative fixture + zero known false positives/negatives.
- [ ] **V8 — Kör validated audit → new baseline**: detta är den första pålitliga 100%-mätningen.

### Surface-level fixes (Wave 5a)

- [ ] **S1 — Fix Q21 honesty hole**: `diagnostics.py:_handle_trace_config` returns None. Replace with `{"success": False, "error": "..."}` or `{"success": True, ...}`. Verify baseline 6550 holds.
- [ ] **S2 — Fix Q21b silent failures (3)**: `kit_tools.py:106`, `kit_tools.py:110`, `ros_mcp_tools.py:399`. Each `{"success": False}` needs `"error": "<msg>"` key. Verify baseline.
- [ ] **S3 — Audit Q9 eval/exec (1 hit)**: `canonical_instantiator.py:527`. Check if it's `exec(generated_kit_code)` (legitimate) or unsafe. Document + suppress with comment, OR refactor.
- [ ] **S4 — Audit Q10 shell=True (1 hit)**: `fingerprint/collector.py:30`. Check sanitization. Suppress with comment if safe, else fix.
- [ ] **S5 — Fix Q12 blocking I/O (2 hits)**:
  - `bridge_tools.py:367 time.sleep` → `await asyncio.sleep`
  - `robot.py:5132 open` inside async — likely 1-line config read; replace with `asyncio.to_thread(path.read_text)` or accept and document.
- [ ] **S6 — Audit Q14 35 orphan schemas**: Most likely bound via MCP router / FastAPI route, not the heuristic's `data["..."] = _handle_*` pattern. Refine the audit heuristic to also detect `@router.post` and `@mcp.tool` decorators, OR explicitly mark these as bound. Goal: drop orphan count to <5.

### Docstring sweep (Wave 5b)

- [ ] **D1 — Fix 309 missing docstrings**: Run a docs sweep agent on the file list emitted by `Q15_missing_docstrings` in audit JSON output. Target: zero MISSING. THIN coverage is Tier 2.
- [ ] **D2 — Re-run audit, expect Q15 PASS**.

### Module size sweep (Wave 5c)

- [ ] **M1 — Refactor top-5 largest modules** out of 42 (>500 LOC):
  - `chat/canonical_instantiator.py` (712 LOC)
  - `chat/context_distiller.py` (831 LOC)
  - Plus 3 others from baseline run
  - Strategy: extract pure helpers into `_helpers.py` per module. Don't split arbitrarily.
- [ ] **M2 — For remaining 37 large modules**: judge whether they're truly cohesive single units OR splittable. If cohesive, **bump the threshold per-file via comment** (`# fmt: keep — cohesive theme module`) and update audit to honor it.

### Tier 2 (partial-determinism checks)

- [ ] **T2.1 — Build dead-read scanner** (`scripts/qa/dead_read_scan.py`): walk module globals, find names read but never written in non-test code. Wave 3a found `EUREKA.runs` — find the next.
- [ ] **T2.2 — Build error-path coverage tester**: for each handler, assert one test exists that triggers `success: False`. Use `pytest --collect-only` + grep.
- [ ] **T2.3 — Build O(n²) stage-loop detector**: AST scan for nested for-loops over stage iterators.
- [ ] **T2.4 — Build telemetry coverage scanner**: each handler emits at least one `telemetry.emit()` or has the decorator.

### Tier 3 (data extraction for judgment-questions)

- [ ] **TI.1 — Build dependency graph exporter** (`scripts/qa/architecture_data_export.py`): emit JSON with module-import edges, public-surface, cycles. Use for "is architecture sound?" judgment.
- [ ] **TI.2 — Build co-change hotspot report**: parse `git log` for files that change in the same commit ≥5 times. Identifies refactor pressure points.
- [ ] **TI.3 — Build cyclomatic complexity histogram**: pure AST, no radon dep. Identifies refactor targets.

### Gemini smoke (Wave 5d)

- [ ] **G1 — Implement 3 smoke stubs** with `@pytest.mark.gemini_live`:
  - `tests/gemini_smoke/test_vision_provider_smoke.py` (~2k+500 tokens)
  - `tests/gemini_smoke/test_spec_generator_smoke.py` (~1k+1k)
  - `tests/gemini_smoke/test_critic_smoke.py` (~1k+500)
- [ ] **G2 — Run smoke via Gemini CLI (free tier)**: log tokens_used. Verify all 3 pass.

### Final

- [ ] **F1 — Re-run Tier 1**: expect 0 fails on all 12 checks
- [ ] **F2 — Generate `docs/qa/100PCT_DEFINITION.md`**: map all 35 questions → data source → current status (pass/data-extracted)
- [ ] **F3 — Update memory with workflow**: how future quality regressions are caught (`scripts/qa/post_migration_health_check.py` is the gate)
- [ ] **F4 — Delete cron** (this plan is complete)

---

## Per-wake-up runbook

```
1. cd /home/anton/projects/Omniverse_Nemotron_Ext
2. Read docs/qa/PATH_TO_100PCT.md
3. Find first "[ ]" line under "Action steps"
4. Execute that step. Keep scope tight — one step per wake-up.
5. Mark "[x]". Update baseline table if relevant.
6. python scripts/qa/post_migration_health_check.py 2>&1 | head -5 (sanity-check)
7. git add + commit + push to anton remote
8. Done. Stop. Next cron tick takes the next step.
```

**Token discipline per wake-up:** under 30k tokens. If a step needs more, split it.

**If blocked:** mark step as `[!]` with reason + skip to next.
