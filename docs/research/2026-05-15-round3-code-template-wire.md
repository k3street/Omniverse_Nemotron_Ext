# Round 3 Patch A — code_template wire-up decision doc

**Date:** 2026-05-15
**Author:** Claude (automated patch, QC pending)

---

## Problem statement

`execute_template_canonical` read `template.get("code")` unconditionally (line 501 before patch). `instantiate_role_based_code` existed and had correct substitution logic, but was only reachable from tests — never from production. CP-09/10/11 were migrated to `code_template` + `roles` + `role_defaults` (per Round 2 equivalence audit) but production runs continued to execute the stale `code` field. The wire-up was a missing dispatch branch, not a logic gap.

---

## Diff summary

| File | Change | LOC delta |
|---|---|---|
| `service/isaac_assist_service/chat/canonical_instantiator.py` | Added role-field detection branch before `raw_code` assignment | +8 / -3 (net +5) |
| `tests/test_role_based_code_dispatch.py` | New test file | +240 |
| `docs/research/2026-05-15-round3-code-template-wire.md` | This doc | +~90 |

The production change is exactly:

```python
# Before (line 501):
raw_code = template.get("code") or ""

# After (lines 501-509):
if template.get("code_template") and template.get("roles") and template.get("role_defaults"):
    logger.debug(f"[CanonicalInst] {task_id} using role-based code_template path")
    raw_code = instantiate_role_based_code(template)
else:
    raw_code = template.get("code") or ""
```

`instantiate_role_based_code` already accepted `(template, role_bindings=None)`. The call site passes `None` (using `role_defaults` from the template itself), which matches the Block 1B documented behavior. No signature changes were needed.

---

## Test cases added

`tests/test_role_based_code_dispatch.py` — 7 tests (5 logical cases + 3 parametrized pilot CPs):

| Test | What it checks |
|---|---|
| `test_role_based_template_uses_role_path` | Template with all three role fields → `instantiate_role_based_code` is called |
| `test_legacy_template_uses_code_field` | Template without role fields → legacy `code` path, `create_prim` tool captured |
| `test_both_fields_prefers_code_template` | Template with both fields → role path wins; `FrankaNew` path in args, not `FrankaOld` |
| `test_bad_substitution_passes_through_unresolved_placeholder` | Missing role key → placeholder left as literal string (sentinel); no Python exception escapes |
| `test_pilot_cp_routes_to_role_based_path[CP-09/10/11]` | Real templates from workspace → dispatch verified + `instantiated: True` |

---

## Design decision: bad-substitution behavior (Test 4)

When a `role_defaults` key is missing for a placeholder, `substitute_role_placeholders` leaves `{{role.field}}` unchanged in the output string. This causes a `NameError` in `exec` (the placeholder is not valid Python), which is caught by the existing `try/except` in `execute_template_canonical` and returned as `{"instantiated": False, "errors": ["sandbox exec failed: NameError: ..."]}`. 

This is intentional: a partial substitution failure produces a clear, named error rather than a silent wrong execution. No fallback to the legacy `code` field was added — fallback would hide migration bugs.

---

## Verification: CP-09/10/11 now use role-based path

Test `test_pilot_cp_routes_to_role_based_path` directly verifies this for all three CPs. All 3 pass with `instantiated: True`. The spy on `instantiate_role_based_code` confirms the function is reached.

Equivalence tests in `tests/test_role_template_equivalence.py` continue to pass (85/85) — confirming that the role path and legacy path produce identical tool-call sequences.

---

## Edge cases surfaced

1. **`task_id` extraction moved before the branch** — `task_id = template.get("task_id", "?")` must precede the `logger.debug` call in the role branch. Confirmed in the final implementation.

2. **Partial role fields** — templates with only one or two of `{code_template, roles, role_defaults}` fall through to legacy path. This is correct: a half-migrated template should not silently use an incomplete role spec.

3. **`instantiate_role_based_code` already handles missing `code_template`** — if called with a template that has `code_template: ""`, it returns `template.get("code", "")`, not an empty string from substitution. The production check guards `template.get("code_template")` (falsy check), so this path is unreachable from the new branch.

---

## Open questions

- **Block 2 role bindings**: `execute_template_canonical` currently passes `role_bindings=None`, which causes `instantiate_role_based_code` to fall back to `role_defaults`. When LayoutSpec-sourced bindings are available (Block 2), the caller will need to pass them. This requires extending `execute_template_canonical`'s signature with an optional `role_bindings` parameter. Not needed for Block 1B.

- **`verify_args_template` / `simulate_args_template`**: CP-09/10/11 also have role-templated versions of these fields. Verify + function gates still read the static `verify_args` / `simulate_args`. A follow-up patch could wire those too, but that is out of scope for this patch.
