# Round 11b — Motion-Controller Filter Wire to Orchestrator

**Date:** 2026-05-16  
**Branch:** feat/multimodal-foundation (no commit yet — Anton commits after QC)

---

## §1 Env-var semantics

`RETRIEVAL_MC_FILTER` now serves dual purpose:

| Value | Meaning |
|-------|---------|
| unset / `""` / whitespace | No filter (default, backward-compatible) |
| `on` / `off` / `true` / `false` / `1` / `0` / `yes` / `no` | Plain gate — parsed by `_mc_filter_enabled()` in template_retriever; **not** a constraint expression; `_parse_mc_filter_env()` returns `None` |
| `curobo` | `{must_verified: ["curobo"]}` |
| `curobo,rmpflow` | `{must_verified: ["curobo", "rmpflow"]}` |
| `!admittance` | `{must_not_failed: ["admittance"]}` |
| `curobo,!moveit2` | `{must_verified: ["curobo"], must_not_failed: ["moveit2"]}` |

When a constraint expression is provided, `RETRIEVAL_MC_FILTER` implicitly
acts as `on` (the expression is non-empty and non-flag, so `_mc_filter_enabled()`
evaluates True via the usual `os.environ.get` check in template_retriever).

---

## §2 Diff summary

### `service/isaac_assist_service/chat/orchestrator.py`
- **+52 LOC** — `_parse_mc_filter_env()` module-level helper (lines ~43–91)
- **+8 LOC** — `_mc_filter = _parse_mc_filter_env()` call + comment block
- **+3 LOC** — `motion_controller_constraint=_mc_filter` kwarg on soft-filter call
- **+2 LOC** — `motion_controller_constraint=_mc_filter` kwarg on hard-filter call
- **+3 LOC** — restructured fallback `retrieve_templates_with_scores` call with kwarg + log annotation

Total orchestrator delta: **~+68 LOC**

### `service/isaac_assist_service/chat/tools/template_retriever.py`
- **+1 LOC** — `motion_controller_constraint: Optional[Dict] = None` param on `retrieve_with_intent_filter`
- **+8 LOC** — docstring paragraph documenting the new param
- **+12 LOC** — propagation to all three `retrieve_templates_with_scores` calls inside `retrieve_with_intent_filter` (null-signal fallback, candidates-empty fallback, Stage-2 inline apply)
- **+1 LOC** — `motion_controller_constraint: Optional[Dict] = None` param on `retrieve_with_intent_soft_filter`
- **+8 LOC** — docstring paragraph documenting the new param
- **+3 LOC** — propagation to `retrieve_templates_with_scores` inside `retrieve_with_intent_soft_filter`

Total retriever delta: **~+33 LOC**

### `tests/test_mc_filter_orchestrator_wire.py` (new file)
**+155 LOC** — 17 test cases across two classes.

---

## §3 Test coverage

| Test | Description | Result |
|------|-------------|--------|
| T1 | unset env-var → None | PASS |
| T2 | `"curobo"` → must_verified | PASS |
| T3 | `"curobo,rmpflow"` → two must_verified | PASS |
| T4 | `"!admittance"` → must_not_failed | PASS |
| T5 | `"curobo,!moveit2"` → both kinds | PASS |
| T6a | `""` → None | PASS |
| T6b | whitespace → None | PASS |
| T7 | plain on/off/true/false/1/0/yes/no → None (×12) | PASS |
| T8 | token whitespace stripping | PASS |
| S1 | `_parse_mc_filter_env` defined in orchestrator.py | PASS |
| S2 | `_parse_mc_filter_env()` called in retrieval block | PASS |
| S3 | soft-filter call site has `motion_controller_constraint=_mc_filter` | PASS |
| S4 | ≥3 occurrences of kwarg (soft + hard + fallback) | PASS |
| S5 | fallback retrieve call has kwarg | PASS |

All 52 tests pass (17 new + 35 existing `test_multimodal_text_intent_flag`).
Lint: 320 templates, 0 errors.

---

## §4 What this unlocks

Before R11b: `motion_controller_constraint` was reachable only from test
code that called `retrieve_templates_with_scores` directly with the kwarg.
Production user prompts went through the orchestrator, which never built or
passed the constraint — the filter was dead code from a production perspective.

After R11b: setting `RETRIEVAL_MC_FILTER=curobo` before starting the service
routes ALL production retrieval through the MC filter:

- Soft-filter path (default): `retrieve_with_intent_soft_filter` →
  `retrieve_templates_with_scores(…, motion_controller_constraint={"must_verified": ["curobo"]})`
- Hard-filter path: same propagation through `retrieve_with_intent_filter`
- Embedding-only fallback: direct `retrieve_templates_with_scores` call also carries the constraint

Result: a user who sends any prompt gets only templates where cuRobo is in
`motion_controllers.verified`. Templates without the `motion_controllers`
field are still included (benefit of the doubt, unmigrated templates).

---

## §5 Follow-up: prompt-context wiring

Env-var is the right deployment lever for now (lab, CI, specific service
instances). When the system needs per-session or per-user controller
preference (e.g. user says "I use cuRobo"), the natural extension is:

1. Extract controller preference from user message in `produce_layout_spec_from_text`
   and add a `preferred_controllers` field to `Intent`.
2. In the orchestrator retrieval block, merge `Intent.preferred_controllers`
   with `_parse_mc_filter_env()` to build the final constraint.
3. The env-var constraint acts as a hard policy; the intent-derived constraint
   acts as a user preference (could be a soft boost instead of a hard filter).

This keeps R11b minimal (env-var only) and leaves the prompt-context path as
a clean additive change in a future round.
