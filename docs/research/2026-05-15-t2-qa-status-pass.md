# T2 QA-Status Classification Pass — 2026-05-15

**Scope:** 212 non-CP (T2) templates in `workspace/templates/`
**Agent:** Sonnet 4.6, Week 1 / Track A continuation
**Prior context:** `docs/research/2026-05-15-q4-template-drift.md` §2

---

## §1 Methodology

**Field added:** `qa_status` (string, free-text, informational only) on every T2 template
without a prior honest marker. For "referenced" templates, also `qa_status_meta` (structured
dict with `evidence_source`, `classified_date`, `classification`).

**Classification rules:**

- **referenced** — template ID appears verbatim in test files, QA scripts, or `role_template_index.py`.
  Evidence source cited. This means "name is wired somewhere", NOT "outputs were validated".
- **dialogue_canonical** — all 6 core fields (`task_id`, `goal`, `tools_used`, `thoughts`,
  `code`, `failure_modes`) present and non-empty. Structurally complete but not exercised
  in any Kit-RPC or campaign QA run.
- **draft** — one or more core fields missing or empty. Structurally incomplete.
- **orphan** — template references tool names absent from the live handler registry
  (DATA_HANDLERS + CODE_GEN_HANDLERS across all theme modules).

**Tool registry built from:** `service/isaac_assist_service/chat/tools/handlers/*.py`
`register()` body — both `data["X"] =` and `codegen["X"] =` assignments, plus
ROS2 live tools declared in `_dispatch.py` and `run_usd_script` in `tool_executor.py`.
Total: 405 unique tool names.

**Referenced search:** `grep`-searched `tests/`, `scripts/`, `service/` for exact quoted
T2 template IDs. No fuzzy matching.

**CP templates excluded:** CP-* already have `verified_status` from prior Wilson pass.
`qa_status` is T2-only.

**Idempotent:** Script skips any template already having `qa_status`. Safe to re-run.

---

## §2 Aggregate Counts

| Status | Count | Notes |
|--------|-------|-------|
| **referenced** | 14 | Name cited in tests or QA scripts |
| **dialogue_canonical** | 198 | All 6 core fields present, not exercised |
| **draft** | 0 | No structurally incomplete T2 templates found |
| **orphan** | 0 | All tools_used entries resolve in live registry |
| **Total classified** | **212** | Matches authoritative count from Q4 |

**Surprise finding:** Zero orphan T2 templates. Initial grep suggested 42 orphans (e.g.,
`run_usd_script`, `configure_ros2_bridge`, `diagnose_ros2`), but these ARE registered —
they live in `codegen["X"] =` assignments in `ros2.py` and `tool_executor.py` special-case.
The initial regex searched only `data["X"] =` lines and missed codegen registrations.
See §4 for detail.

---

## §3 Sample 5 Per Status

### referenced (14 total)

| Template | Evidence |
|----------|----------|
| `M-01` | `tests/test_phase12_qa.py`, `scripts/qa/run_all_sequential.py`, `scripts/qa/launch_campaign.py` — highest coverage; used as canonical campaign example |
| `E-01` | `tests/test_phase12_qa.py`, `scripts/qa/run_all_sequential.py` — Erik CTO persona, first task |
| `T-01` | `tests/test_phase_21_role_template_index.py`, `tests/test_qa_scripts.py`, `scripts/qa/run_all_sequential.py` — wired into role-index test fixture |
| `A-01` | `tests/test_canonical_lint.py`, `scripts/qa/run_all_sequential.py` — lint test uses as T2 base fixture |
| `G-01` | `tests/test_qa_scripts.py` — used as mock task ID in QA script tests |

### dialogue_canonical (198 total — sample from different prefixes)

| Template | Reasoning |
|----------|-----------|
| `AD-15` | All 6 fields present; adversarial task coaching LLM on handling malformed prim paths. Not in any test or script. |
| `AL-08` | All 6 fields; AutoLab persona, sensor calibration task. Cohort A (2026-04-18 batch). |
| `AM-09` | All 6 fields; asset management task. Zero QA campaign appearances per Q4 §2. |
| `FX-05` | All 6 fields; single-line code (`find_prims_by_schema(...)`) — intentionally concise, not a draft. |
| `Y-08` | All 6 fields; Yuki persona robotics education task. Never run. |

### draft — none found

All 212 T2 templates have non-empty values for all 6 core fields.

### orphan — none found

All `tools_used` entries across 212 T2 templates map to live handler registrations.

---

## §4 Orphan Analysis (False-Alarm Detail)

Initial grep flagged 42 templates as potential orphans for tools including
`run_usd_script`, `configure_ros2_bridge`, `diagnose_ros2`, `export_nav2_map`,
`setup_ros2_bridge`, `configure_ros2_time`, `fix_ros2_qos`.

Verification confirmed all are live:

| Tool | Registration location |
|------|-----------------------|
| `run_usd_script` | `tool_executor.py:320` (special-case branch) + `tool_schemas.py:132` |
| `configure_ros2_bridge` | `handlers/ros2.py:1032` `codegen["configure_ros2_bridge"] = ...` |
| `configure_ros2_time` | `handlers/ros2.py:1033` |
| `fix_ros2_qos` | `handlers/ros2.py:1034` |
| `setup_ros2_bridge` | `handlers/ros2.py:1036` |
| `diagnose_ros2` | `handlers/ros2.py:1027` `data["diagnose_ros2"] = ...` |
| `export_nav2_map` | `handlers/_models.py:4195` (schema) + `tool_descriptions_polish_b3.py:418` |

**No orphan T2 templates exist.** No delete/repair decisions required.

---

## §5 Files Modified

212 T2 template JSON files, each gaining a `qa_status` field appended to the
existing 6-field body. 14 of the 212 also received `qa_status_meta` (referenced-only).

CP-* templates: untouched (0 files).

**Lint re-run after pass:**
`321 templates scanned: 216 OK, 0 ERROR, 55 WARN, 225 INFO`
Identical to pre-pass baseline. No regressions.
