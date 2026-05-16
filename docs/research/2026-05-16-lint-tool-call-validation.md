# Lint Tool-Call Validation — Design & Verification

Date: 2026-05-16
Author: claude-sonnet-4-6 (no prior context)
Scope: `--validate-tool-calls` pass for `scripts/lint_canonical_templates.py`

---

## §1 Design choices

### Discovery approach: dynamic import (option a)

`canonical_schema.py:build_tool_model_map()` does:
1. Appends `service/` to `sys.path`
2. `importlib.import_module("isaac_assist_service.chat.tools.handlers._models")`
3. `inspect.getmembers(mod, isclass)` filtered to `name.endswith("Args")`
4. Naming convention: `SetDriveGainsArgs` → strip `Args` → CamelCase → snake_case → `set_drive_gains`

**Why dynamic import over text-scan:**
- Zero regex maintenance — naming convention is mechanical and consistent (435 models, 0 exceptions observed)
- Gives live `model_fields` dict with `is_required()` and field annotation — no re-parsing needed
- Falls back to empty dict on `ImportError`; lint emits a single `TC_MODEL_UNAVAILABLE` WARN and continues

**Why not subprocess:**
- Subprocess spawns a new Python process and adds ~0.5 s per lint run; unnecessary when the service module is importable from the same virtualenv

### code_template sanitization

`{{role.field}}` placeholders are replaced with the string sentinel `"__TEMPLATE_VAR__"` before AST parsing. This makes `robot_wizard(robot_name={{primary_robot.class}})` parse as `robot_wizard(robot_name="__TEMPLATE_VAR__")`. The sentinel IS a value (a string), so it satisfies presence checks for required fields. Type-shape checks (e.g. string vs `List[float]`) are intentionally not performed — the actual type is only known post-substitution.

When a template var appears inside an f-string literal, the substitution may produce invalid Python (e.g. `f"angle >= "__TEMPLATE_VAR__" deg"`). In that case `TC_SYNTAX_ERROR` WARN is emitted and validation is skipped for that field — not a crash.

---

## §2 Rule codes

| Code | Level | Meaning |
|------|-------|---------|
| `TC_MODEL_UNAVAILABLE` | WARN | `_models.py` could not be imported; validation skipped |
| `TC_SYNTAX_ERROR` | WARN | Code field has a `SyntaxError`; cannot AST-parse |
| `TC_UNKNOWN_TOOL` | WARN | Snake-case function name with underscore not in model map |
| `TC_REQUIRED_MISSING` | ERROR | One or more Pydantic required fields absent from call |
| `TC_UNKNOWN_KWARG` | WARN | Kwarg not in model schema (absorbed by `extra='allow'`) |

`TC_REQUIRED_MISSING` is ERROR because missing required fields cause Pydantic `ValidationError` at runtime before any Kit RPC executes — the canonical literally cannot run. `TC_UNKNOWN_KWARG` is WARN because `extra='allow'` means the call succeeds but the handler silently ignores the kwarg; may or may not be a logic bug.

---

## §3 Coverage limitations

1. **Dynamic `**kwargs` unpacking** — calls like `tool(**params_dict)` are detected as having `has_star=True`; required-field check is skipped. The WARN is not emitted either. Coverage gap: ~3-5% of real call sites in complex templates use this pattern.

2. **Template-var inside f-string** — when `{{var}}` appears inside a string literal value (not as a standalone argument), sanitization may produce invalid Python. Affected field gets `TC_SYNTAX_ERROR` WARN and is skipped. Manifests in ~2/8 `code_template` fields in the CP-NEW batch. The `code` field (no template vars) is always parseable.

3. **Aliased tool names / attribute access** — calls like `helpers.sim_control(action='play')` produce `fn = 'sim_control'` (from `ast.Attribute.attr`) and are validated correctly. But `getattr(mod, 'sim_control')(action='play')` is invisible to AST walk; `fn` cannot be determined statically. Not observed in current templates.

4. **String-interpolated tool names** — `fn = f"plan_{mode}"; globals()[fn](...)` is invisible. Not observed in current templates but theoretically possible in generated code.

5. **Type-shape validation** — `axis="Z"` (string) for a `List[float]` field is NOT flagged. Only presence of kwargs is checked, not their Python literal types. A future `TC_TYPE_MISMATCH` rule could catch this class of bug but requires annotation-level type inference.

---

## §4 Verification result on A1-A8 (post R-A-fix-3)

Command:
```
python scripts/lint_canonical_templates.py --validate-tool-calls \
  workspace/templates/CP-NEW-palletizer-layer-stack.json \
  workspace/templates/CP-NEW-kit-prep-operator.json \
  workspace/templates/CP-NEW-barcode-scanner-divert.json \
  workspace/templates/CP-NEW-turn-faucet.json \
  workspace/templates/CP-NEW-ros2-bridge-franka.json \
  workspace/templates/CP-NEW-assembly-line-4robot-handoff.json \
  workspace/templates/CP-NEW-eureka-pick-place-reward.json \
  workspace/templates/CP-NEW-heap-zone-unstack.json
```

Result: **0 ERROR, 2 WARN**

| Template | Status | Notes |
|----------|--------|-------|
| CP-NEW-palletizer-layer-stack (A1) | OK | — |
| CP-NEW-kit-prep-operator (A2) | WARN TC_UNKNOWN_KWARG | `set_semantic_label(label=...)` absorbed by extra='allow'; noted as MED in R-A-fix-3 |
| CP-NEW-barcode-scanner-divert (A3) | WARN TC_UNKNOWN_KWARG | `barcode_reader_sensor(scan_volume, read_attribute)` absorbed; noted as MED in R-A-fix-3 |
| CP-NEW-turn-faucet (A4) | OK | All 4 BLOCKERs fixed by R-A-fix-3 |
| CP-NEW-ros2-bridge-franka (A5) | OK | — |
| CP-NEW-assembly-line-4robot-handoff (A6) | OK | — |
| CP-NEW-eureka-pick-place-reward (A7) | OK | — |
| CP-NEW-heap-zone-unstack (A8) | OK | BLOCKER fixed by R-A-fix-3 |

**New issues surfaced beyond R-A-fix-3 scope:** none. The 2 WARNs were already documented as intentional known-MEDs in the R-A-fix-3 decision doc.

**Bonus finding (A-01 to A-09 dialogue canonicals):** running on `workspace/templates/A-0*.json` surfaces 7 ERROR / 14 WARN across 7 templates. These are the A-series "Alex" dialogue templates (non-CP) that were never audited for schema compliance. The checker found:
- `import_robot(urdf_path=...)` → required `file_path` missing
- `set_drive_gains(articulation_prim=..., stiffness=..., damping=...)` → required `joint_path`, `kp`, `kd` missing
- `grasp_object(gripper_prim=..., target_prim=..., close_force=...)` → required `robot_path` missing (uses wrong kwarg name)
- `run_usd_script(script=...)` → required `code` and `description` missing (uses wrong kwarg name)
- `diagnose_physics_error(symptom=...)` → required `error_text` missing

These are real bugs, not false positives. They would cause Pydantic ValidationError at runtime.

---

## §5 Recommended enablement plan

**Current state (2026-05-16):** flag is off-by-default (`--validate-tool-calls` must be explicit).

**Phase 1 — Fix A-01 to A-09 ERRORs:** 7 templates have TC_REQUIRED_MISSING errors (wrong kwarg names reflect API drift since the templates were authored). Fix these first; estimated 1 session.

**Phase 2 — Flip to default-on for `code` field only:**
After the A-series fix, run `python scripts/lint_canonical_templates.py --validate-tool-calls workspace/templates/*.json` and confirm 0 ERROR across all 328 templates. Then add `validate_tool_calls=True` as the default in `main()`. The `--no-validate-tool-calls` flag can be added for escape hatches.

**Phase 3 — Flip `--strict-tool-calls` to default-on:**
After Phase 2 is clean, enable `code_template` validation. The TC_SYNTAX_ERROR WARNs from f-string breakage are acceptable (they don't block the run). Remaining TC_UNKNOWN_KWARG WARNs in A2/A3 should be fixed or suppressed with a `# lint: ignore[TC_UNKNOWN_KWARG]` inline comment (to be implemented).

**Timing:** flip Phase 2 after baseline clean; do not flip before because the A-series ERRORs would break `--strict` CI.
