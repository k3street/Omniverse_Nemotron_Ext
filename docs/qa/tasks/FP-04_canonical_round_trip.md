# Task FP-04 [MEDIUM] — Canonical round-trip preserves shape

**Modality:** internal (no user-facing modality — regression test)

**Goal:** For each of CP-01..CP-05, instantiate the template via the
role-based code_template path, then sandbox-capture tool calls, and assert
they exactly equal the legacy `code` field's captured calls. This is the
pre-execution-equivalence test from Block 1B Step 18.

**Starting state:** templates as committed (must have both `code` and
`code_template`/`role_defaults`/`roles` fields).

**Success criterion:**
- For each CP-{01..05}: `_capture_tool_calls(legacy_code) ==
  _capture_tool_calls(instantiate_role_based_code(template))` (modulo
  whitespace / dict-insertion order)
- No `{{...}}` placeholder left in the substituted output
- All test-harness assertions in `tests/test_role_template_equivalence.py`
  pass

**Implementation:** `tests/test_role_template_equivalence.py` (Block 1B
Step 18). 5 parametrized test cases.

**Failure mode:** if a role-template refactor introduces drift, this test
fails BEFORE the template hits the function-gate. Per Block 1B discipline
in spec §20: "any function-gate ✓ that becomes ✗ → template rolled back,
refactor approach reworked." The equivalence test catches it pre-execution.

**Future extension:** when LayoutSpec.objects are supplied to ratifier
(Block 4 wiring), additionally assert that ratify auto-binding produces
the same `role_defaults`-equivalent bindings.
