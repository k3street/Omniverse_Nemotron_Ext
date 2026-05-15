# Round 2 / Audit 1 — Reachability of Fields Added 2026-05-15

**Scope:** Seven field/feature areas added during the canonical-migration session.
**Method:** Grep `service/` for each field as a string literal; trace consumer code to confirm
behavioral effect (not just parse/write). Test-only consumers are distinguished from production.
**Constraint:** Read-only. No code modified.

---

## §1 Reachability Summary Table

| Field / Feature | Producer | Consumer(s) | Reachable? | Severity if Unreachable |
|---|---|---|---|---|
| `motion_controllers` | `scripts/lint_canonical_templates.py` | Lint only (WARN/ERR) — zero `service/` reads | **UNREACHABLE** for retrieval/filtering | HIGH |
| `qa_status` | Template JSON files (226 entries) | None — zero Python reads anywhere | **UNREACHABLE** | MED |
| `verified_wilson_lower` + `verified_runs` | `scripts/qa/mark_verified.py` (writes ChromaDB) | No `service/` reader; no retrieval filter uses them | **UNREACHABLE** for ranking/filtering | MED |
| `code_template` + `roles` + `role_defaults` | Template JSON (CP-09/10/11) | `instantiate_role_based_code` defined in `canonical_instantiator.py` — called only by tests; `execute_template_canonical` hard-reads `"code"` field on line 501, bypasses `code_template` entirely | **UNREACHABLE** in production execution path | HIGH |
| `relabel_note` | `workspace/benchmarks/retrieval_30prompts.json` (4 entries) | Benchmark test reads: `id`, `prompt`, `ground_truth`, `acceptable_alternatives`, `expected_action`, `category`, `complexity`, `notes` — `relabel_note` is NOT in that set | **UNREACHABLE** | LOW |
| New workflow types (assemble_pick_place_cell, validate_robot_import, generate_sdg_dataset) | `_state.py` `_WORKFLOW_TEMPLATES` registry | `start_workflow` passes registry check; `approve_workflow_checkpoint` advances phase state. No phase has a Kit-RPC executor — status transitions to `"executing_X"` but no code runs | **PARTIALLY REACHABLE** (registry + state machine work; execution is LLM-delegated, not autonomous) | MED |
| Equivalence test extension (CP-09/10/11 in `test_role_template_equivalence.py`) | Tests only | `instantiate_role_based_code` called in test sandbox — does a real semantic equivalence check (captured tool-call sequences compared) | **REACHABLE** (tests) — not production | LOW (test coverage is meaningful) |

---

## §2 Per-Field Detailed Audit

### §2.1 `motion_controllers`

**Producer:**
- `scripts/canonical_schema.py:68` — documents the field schema.
- `scripts/lint_canonical_templates.py:190–243` — validates structure (T1_MC checks), issues WARN if a
  motion-planning tool is used but field is absent, ERRs on bad types.

**Consumer search in `service/`:**
```
grep -rn "motion_controllers" service/   → (no output)
```
Zero hits. The retrieval index (`template_retriever.py:_build_index`) stores only `{"task_id": tid}` as
ChromaDB metadata (line 100). The structural filter (`filter_templates_by_intent`, line 350) uses
`intent`, `structural_features`, and count fields — never `motion_controllers`.

**Verdict:** `motion_controllers` is lint-validated metadata. No production code reads it to filter
retrieval candidates or gate execution. A user asking "find me a template that works with cuRobo" gets
no benefit from this field today.

---

### §2.2 `qa_status`

**Producer:**
- 226 template JSON files under `workspace/templates/` contain the key, e.g.:
  `workspace/templates/L-11.json:16` — `"qa_status": "dialogue_canonical; structurally complete; …"`

**Consumer search:**
```
grep -rn "qa_status" service/   → (no output)
grep -rn "qa_status" scripts/   → (no output)
grep -rn "qa_status" tests/     → (no output)
```
Absolute zero. The field is not in `lint_canonical_templates.py`. The indexer does not write it to
ChromaDB. No cron prioritizes on it. No route exposes it.

**Verdict:** `qa_status` is a documentation-only label embedded in JSON. It adds human readability but
has no programmatic consumer anywhere in the codebase.

---

### §2.3 `verified_wilson_lower` + `verified_runs`

**Producer:**
- `scripts/qa/mark_verified.py:153` — `meta["verified_runs"] = n_runs`
- `scripts/qa/mark_verified.py:158` — `meta["verified_wilson_lower"] = round(lo, 4)`

These are written into ChromaDB metadata via `coll.update(ids=updated, metadatas=new_metas)` (line 174).

**Round-trip read check (mark_verified.py itself):**
Line 141: `existing = coll.get(ids=perfect_ids, include=["metadatas"])` — fetches existing metadata to
preserve non-verified keys before overwrite. It reads the `have` dict to merge, but never reads back
`verified_wilson_lower` or `verified_runs` from the stored values — it recomputes them from the run
files every time. No round-trip consumption of the stored values.

**Consumer search in `service/`:**
```
grep -rn "verified_wilson_lower\|verified_runs\|verified_metrics" service/   → (no output)
```
The template retriever (`template_retriever.py`) does not filter on `verified=True` or use
`verified_wilson_lower` as a ranking boost. The orchestrator does not gate hard-instantiate on
`verified` status.

**Verdict:** Wilson lower-bound and run counts are written to ChromaDB but never read back by any
retrieval, ranking, or execution path. They survive only as auditable metadata for human inspection
via direct ChromaDB queries.

---

### §2.4 `code_template` + `roles` + `role_defaults` (CP-09/10/11)

**Producer:**
- CP-09.json lines 146+, CP-10.json lines 171+, CP-11.json lines 166+ contain `code_template` with
  `{{role.field}}` placeholders and `role_defaults` with concrete bindings.

**The function that consumes `code_template`:**
- `canonical_instantiator.py:459` — `instantiate_role_based_code(template, role_bindings=None)`:
  reads `template.get("code_template")` (line 475), falls back to `template.get("code", "")` if
  absent (line 477), substitutes role placeholders using `role_defaults` (line 478–479).

**The production execution path:**
- `orchestrator.py:946` — `inst_result = await execute_template_canonical(top["template"])`
- `canonical_instantiator.py:501` — `raw_code = template.get("code") or ""`

`execute_template_canonical` reads the `"code"` field directly. It does **not** call
`instantiate_role_based_code`. Because CP-09/10/11 retain their legacy `"code"` field alongside
`"code_template"`, execution succeeds — but it always runs the hardcoded legacy code, never the
role-parameterized `code_template`.

**Callers of `instantiate_role_based_code`:**
```
grep -rn "instantiate_role_based_code" .   → only in:
  tests/test_canonical_instantiator.py:368,455,464,470
  tests/test_role_template_equivalence.py:75,80,99,103
```
Zero calls from `service/`. The function is test-only.

**Verdict:** `code_template` is reachable only in test execution. In production, `execute_template_canonical`
reads `"code"` and ignores `code_template`. The migration gap: `execute_template_canonical` needs one
line changed to call `instantiate_role_based_code` instead of bare `template.get("code")`.

---

### §2.5 `relabel_note` (benchmark prompts B11/B12/B13/B19)

**Producer:**
- `workspace/benchmarks/retrieval_30prompts.json:115,126,137,198` — four entries with
  `"relabel_note": "Relabeled 2026-05-15 — top-1 retrieval is correct; prior label was conservative"`

**Benchmark test reader (`tests/test_retrieval_benchmark.py:67–79`):**
Fields read from each entry: `id`, `prompt`, `ground_truth`, `acceptable_alternatives`,
`expected_action`, `category`, `complexity`, `notes`. The `relabel_note` key is never accessed.

**Other consumers:**
```
grep -rn "relabel_note" .   → only workspace/benchmarks/retrieval_30prompts.json
```

**Verdict:** `relabel_note` is a documentation annotation. The benchmark harness does not read it.
Acceptable as-is — it serves as an audit trail for why `ground_truth` was changed, not as a
machine-consumed field. LOW severity.

---

### §2.6 New Workflow Types (`assemble_pick_place_cell`, `validate_robot_import`, `generate_sdg_dataset`)

**Registry check — PASSES:**
- `_state.py:324–363` — all three registered in `_WORKFLOW_TEMPLATES` with correct shape
  (`description`, `phases`, `default_params`).
- `workflow.py:607` — `start_workflow` checks `workflow_type not in _te._WORKFLOW_TEMPLATES` and
  returns error if absent. New types pass this check.

**State-machine path — WORKS:**
- `_handle_start_workflow` (line 598) creates a workflow dict and returns `workflow_id`.
- `_handle_approve_workflow_checkpoint` (line 729) advances phases via `_wf_advance_phase` (line 76),
  updates status to `"executing_{phase_name}"` or `"awaiting_{phase_name}_approval"`.

**Phase execution — DOES NOT RUN CODE:**
- `approve_workflow_checkpoint` transitions state but dispatches **no Kit RPC call** for phases of
  the new workflow types. The `"executing_load_template"`, `"executing_place_objects"` etc. statuses
  are signals to the LLM to perform the action in the next turn — not autonomous execution triggers.
- Existing workflows (`rl_training`, `robot_import`, `sim_debugging`) follow the same pattern.
  The `kit_tools.queue_exec_patch` calls in `workflow.py` (lines 331, 354, 384, 968) are for
  `watch_changes` and `_handle_get_watch_changes_snapshot` — not phase advancement.

**Verdict:** Partially reachable. The registry + state machine are live and functional. Phase-level
autonomous execution is LLM-delegated by design (same as existing workflow types). This is correct
behavior, not a gap — but should be documented so QA does not expect Kit-level phase verification.

---

### §2.7 Equivalence Test Extension (CP-09/10/11)

**What the test does (`tests/test_role_template_equivalence.py:70–93`):**
1. Loads both `"code"` and `instantiate_role_based_code(template)` (which uses `code_template` + `role_defaults`).
2. Runs both through the canonical sandbox (`exec(compile(code, ...)`) with tool-call capturers.
3. Compares normalized `(tool_name, sorted-kwargs-repr)` sequences.

This is a **genuine semantic equivalence test** — it verifies that the role-substituted output
produces the same tool calls as the hardcoded legacy code, not merely a string comparison.

**Verdict:** Test-reachable only. Meaningful test coverage. The equivalence proof holds at test time
but production still runs the legacy `"code"` path (see §2.4).

---

## §3 Round 3 Wire-Up Backlog

### T1 — Wire `code_template` into `execute_template_canonical`
**Priority: HIGH — BLOCKER for role-based execution to reach production**

`execute_template_canonical` (`canonical_instantiator.py:501`) replaces:
```python
raw_code = template.get("code") or ""
```
with a call to `instantiate_role_based_code(template, role_bindings=None)`.

Files: `service/isaac_assist_service/chat/canonical_instantiator.py` (~3 LOC change).
Guard: keep legacy `"code"` fallback inside `instantiate_role_based_code` (already implemented at
line 477) so existing templates without `code_template` are unaffected.

---

### T2 — Wire `motion_controllers` into structural-filter retrieval
**Priority: HIGH — 62+ templates annotated, zero retrieval benefit today**

Add `motion_controllers.verified` to the ChromaDB metadata stored per template in `_build_index`
(`template_retriever.py:100`). Add a `where` filter in `filter_templates_by_intent` or
`retrieve_with_intent_filter` that excludes templates where the requested controller is in
`motion_controllers.failed`.

Files: `service/isaac_assist_service/chat/tools/template_retriever.py` (~20 LOC).
Also: `service/isaac_assist_service/retrieval/indexer.py` if it has a parallel index path.

---

### T3 — Wire `verified_wilson_lower` as a retrieval ranking boost
**Priority: MED — written to ChromaDB but ignored by ranker**

The retrieval path (`retrieve_with_intent_filter`, line 393) currently ranks by cosine distance
only. Add a secondary sort key: prefer templates with `verified=True` and boost by
`verified_wilson_lower` (e.g., add `0.05 * wilson_lower` to the similarity score). Requires storing
`verified`, `verified_wilson_lower` in ChromaDB metadata at index time (currently only `task_id` is
stored — see `_build_index:100`).

Files: `template_retriever.py:_build_index` (+2 metadata fields), retrieval ranking code (~15 LOC).

---

### T4 — Wire `qa_status` into cron-prioritization or retrieval exclusion
**Priority: LOW — documentation value is acceptable as-is; machine value would come from filtering**

Option A: exclude `"not exercised in QA"` templates from hard-instantiate candidates until they have
a QA run. Option B: surface `qa_status` in the LLM's few-shot prompt context so the model knows
which templates are battle-tested.

Files: `template_retriever.py` or `orchestrator.py` (~10 LOC for Option A).
Decision: defer to Round 3 unless retrieval quality degrades on unexercised templates.

---

### T5 — Add `relabel_note` to benchmark result JSON output (optional)
**Priority: LOW — documentary only**

The benchmark writer (`test_retrieval_benchmark.py:298+`) could include `relabel_note` in the
per-prompt result dict for traceability. One-liner: `"relabel_note": entry.get("relabel_note", "")`.

Files: `tests/test_retrieval_benchmark.py` (~2 LOC).

---

## §4 Severity Classification

| Item | Classification | Rationale |
|---|---|---|
| `code_template` not called by `execute_template_canonical` | **BLOCKER** | The entire role-based template migration is inert in production. All CP-09/10/11 run hardcoded `"code"` instead of the parameterizable `code_template`. The equivalence tests pass, but the production path is frozen at legacy behavior. |
| `motion_controllers` not in retrieval filter | **HIGH** | 62+ templates annotated for controller compatibility; no user or orchestrator can leverage this to avoid recommending incompatible templates. |
| `verified_wilson_lower` not used by ranker | **MED** | Wilson lower-bound captures QA confidence but is invisible to the ranking path. Verified templates have no retrieval advantage over unverified ones. |
| `qa_status` has zero consumers | **MED** | 226 templates carry status labels that no code acts on. Risk: unexercised dialogue templates could be hard-instantiated if retrieval scores them highly. |
| Workflow phase execution is LLM-delegated | **MED** | By design, consistent with existing workflow types — but QA should not assume Kit-RPC evidence of phase completion for the three new types. |
| `relabel_note` not read by benchmark | **LOW** | Acceptable as audit trail. No behavioral impact. |
| Equivalence test covers `code_template` | **LOW** (informational) | Meaningful test; confirms semantic correctness of the refactor. Becomes more valuable once T1 is wired. |
