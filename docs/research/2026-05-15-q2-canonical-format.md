# Q2: Canonical Template Format Standardization and Migration Plan

**Date:** 2026-05-15
**Researcher:** Sonnet agent (Phase 1 / Question 2)
**Prior context:** `docs/research/2026-05-14-l-levels-discovery-audit.md`
**Scope:** 321 templates in `workspace/templates/`

---

## 1. Field-Presence Frequency Table

All 321 templates parsed. Field names and counts out of 321 total.

| Field | Count | % | Notes |
|---|---|---|---|
| `task_id` | 321 | 100.0 | Universal. |
| `goal` | 321 | 100.0 | Universal. |
| `tools_used` | 321 | 100.0 | Universal. |
| `thoughts` | 321 | 100.0 | Universal. Coaching/rationale for LLM. |
| `code` | 321 | 100.0 | Universal. Executable Python. |
| `failure_modes` | 321 | 100.0 | Universal. |
| `verify_args` | 109 | 34.0 | CP-only (all 109 CP templates). |
| `simulate_args` | 109 | 34.0 | CP-only (all 109 CP templates). |
| `verified_status` | 108 | 33.6 | CP-only (108/109; CP-06 has it). |
| `diagnose_args` | 108 | 33.6 | CP-only (108/109; CP-06 has it). |
| `extension_notes` | 108 | 33.6 | CP-only (108/109 CP, plus 22/22 CP-NEW). Overlap noted. |
| `extends` | 106 | 33.0 | CP-only (106/109 CP, 22/22 CP-NEW). |
| `settle_state` | 85 | 26.5 | CP-only (85/109). |
| `intent` | 5 | 1.6 | CP-01..CP-05 only. Has meaningful value (nonempty). |
| `roles` | 5 | 1.6 | CP-01..CP-05 only. Nonempty dict in all 5. |
| `role_defaults` | 5 | 1.6 | CP-01..CP-05 only. Nonempty. |
| `code_template` | 5 | 1.6 | CP-01..CP-05 only. Nonempty. |
| `verify_args_template` | 5 | 1.6 | CP-01..CP-05 only. Nonempty. |
| `simulate_args_template` | 5 | 1.6 | CP-01..CP-05 only. Nonempty. |
| `verified_date` | 2 | 0.6 | CP-01, CP-02 only. |
| `verified_metrics` | 2 | 0.6 | CP-01, CP-02 only. |
| `delivery` | 2 | 0.6 | CP-06, CP-07 only. One-off experiment field. |
| `cube_path` | 2 | 0.6 | CP-06, CP-07 only. One-off shortcut field. |
| `benchmark_vs_alternatives` | 1 | 0.3 | CP-01 only. |
| `blocked` | 1 | 0.3 | CP-06 only. Infrastructure-paused flag. |
| `compute_stack_placement_verified_*` | 1 | 0.3 | CP-08 only. Date-stamped one-off note. |
| `extends_notes` | 1 | 0.3 | CP-NEW-multi-amr-corridor only. Typo of `extension_notes`. |

**Honesty notes:**
- `roles: {}` would be empty; all 5 `roles` fields have meaningful content.
- `extension_notes` appears in all 108 CP templates that have `extends`, so
  `extends` → `extension_notes` is a 1:1 pairing; they always appear together.
- CP-NEW templates (22) are counted under CP prefix (see section 2).

---

## 2. Cohort Breakdown

### Cohort A — Non-CP persona templates (212 templates, 19 prefixes)

**Prefixes:** A (11), AD (23), AL (10), AM (10), C (3), D (14), E (11),
F (10), FX (5), G (6), J (10), K (12), L (11), M (17), P (12), R (11),
S (12), T (14), Y (10).

**Field set (universal):** `task_id`, `goal`, `tools_used`, `thoughts`,
`code`, `failure_modes`.

**Extra fields:** none. Zero instances of `verify_args`, `intent`, `roles`,
`settle_state`, `extends`, or any CP-specific field.

**Authoring era:** These are persona-driven dialogue templates (A=Alex hobbyist,
D=Dimitri/reliability, E=Erik/CTO, F=Fatima/DT-architect, etc.). They represent
conversational recipes, not runnable scene-build pipelines. They were authored
in a single batch before the CP pipeline existed.

**Typical field values:** `code` is Python pseudocode with placeholders like
`<ABSOLUTE_PATH>` or `<USER_CHOICE>` — not sandbox-executable sequences.
`thoughts` is coaching text for the LLM. `failure_modes` is a list of strings.

**Example:** `workspace/templates/A-01.json` — 13 lines, no prim paths,
`fix_base=True` callout pattern.

---

### Cohort B — CP numeric templates (87 templates: CP-01..CP-87)

**Field set:**

| Field | CP-01..05 | CP-06..87 |
|---|---|---|
| Core 6 (task_id, goal, tools_used, thoughts, code, failure_modes) | ✓ | ✓ |
| `verify_args` | ✓ | ✓ (all 87) |
| `simulate_args` | ✓ | ✓ (all 87) |
| `verified_status` | ✓ | ✓ (86/87; CP-06 has `blocked` instead) |
| `diagnose_args` | ✓ | ✓ (86/87) |
| `settle_state` | ✓ | 80/87 (missing ~7 early-numbered) |
| `extends` | absent (3 are base) | 103/107 total |
| `extension_notes` | when extends present | when extends present |
| `intent` | ✓ (all 5) | absent |
| `roles` | ✓ (all 5) | absent |
| `role_defaults` | ✓ (all 5) | absent |
| `code_template` | ✓ (all 5) | absent |
| `verify_args_template` | ✓ (all 5) | absent |
| `simulate_args_template` | ✓ (all 5) | absent |

**CP-01 is the richest template** — 214 lines, has `benchmark_vs_alternatives`,
`verified_date`, `verified_metrics` in addition to the full role-based schema.
File: `workspace/templates/CP-01.json`.

**CP-06 is special** — has `blocked` object, `delivery`, `cube_path` one-off
fields. Infrastructure paused; cannot migrate until unblocked.

**Authoring era:** CP-01..05 authored in Block 1B (2026-04-XX to 2026-05-06),
role-based fields added then. CP-06..87 authored progressively; verify_args
etc. added as the function-gate machinery matured (circa 2026-04-XX).

---

### Cohort C — CP-NEW named templates (22 templates)

**Prefixes:** CP-NEW-* (22 templates, all named with domain descriptors).

**File examples:**
- `workspace/templates/CP-NEW-amr-pickup-handoff.json`
- `workspace/templates/CP-NEW-multi-amr-corridor.json`

**Field set:** identical to CP numeric mid-tier: core-6 + `verify_args` +
`simulate_args` + `diagnose_args` + `extends` + `extension_notes` +
`verified_status` + `failure_modes`. All 22 have the full mid-tier set.

**Exception:** `CP-NEW-multi-amr-corridor.json` has `extends_notes` (typo)
in addition to `extension_notes` — one-off authoring error.

**No role-based fields** — same gap as CP-06..CP-87.

**Authoring era:** Late phase (2026-05-09 industrial expansion), drafted
as future canonical stubs. All have `verified_status: "draft"` or similar.

---

## 3. Canonical Schema Specification

Based on the field-presence data, the `types.py` Intent/LayoutSpec type
system, the `canonical_instantiator.py` runtime contract, and the
equivalence test pattern in `tests/test_role_template_equivalence.py`.

```typescript
/**
 * CanonicalTemplate — the standardized JSON format for workspace/templates/*.json
 *
 * Two tiers:
 *   T1 (scene-build canonicals, CP-*): must have verify_args + settle_state
 *   T2 (dialogue canonicals, A-/D-/E-/F-/etc): core-6 only, code is advisory
 *
 * Fields marked MANDATORY must be present and non-empty to pass conformance.
 * Fields marked OPTIONAL may be absent; the runtime handles graceful absence.
 * Fields marked DEPRECATED should not be authored; migration removes them.
 * Fields marked ROLE-ONLY apply only to templates with role_bindings.
 */
interface CanonicalTemplate {
  // ---- MANDATORY (all templates) ----------------------------------------

  /** Unique identifier. Pattern: CP-\d+ | CP-NEW-[a-z-]+ | [A-Z]{1,3}-\d+ */
  task_id: string;

  /** One-paragraph natural-language description of what this template builds
   *  or demonstrates. Used as similarity-retrieval corpus. */
  goal: string;

  /** Ordered list of tool names called by `code`. Used for schema-subset
   *  filtering (ALLOWED_AFTER_INSTANTIATE in canonical_instantiator.py). */
  tools_used: string[];

  /** Non-obvious implementation rationale. Number-prefixed bullets.
   *  Coaching signal injected into the LLM system prompt. */
  thoughts: string | string[];

  /** Executable Python. Sandbox-safe: only tool calls + _SAFE_BUILTINS.
   *  For T1 (CP-*) this is the deterministic build sequence.
   *  For T2 (dialogue) this is advisory pseudocode with placeholders. */
  code: string;

  /** List of failure modes. Each is a terse string describing one failure
   *  scenario and its root cause. The LLM uses these as a pre-mortem. */
  failure_modes: string[];

  // ---- MANDATORY for T1 (CP-*) templates only ---------------------------

  /** Form-gate inputs. Passed verbatim to verify_pickplace_pipeline.
   *  Shape: { stages: [{robot_path, pick_path, place_path, robot_kind}],
   *           cube_path: string }.
   *  Missing → execute_template_verify short-circuits with "no verify_args". */
  verify_args: VerifyArgs;

  /** Function-gate inputs. Passed verbatim to simulate_traversal_check.
   *  Shape: { cube_path, target_path, duration_s, ...extras }.
   *  Must include duration_s (CP-01 pattern: 180s for conveyor + 4-cube). */
  simulate_args: SimulateArgs;

  /** Diagnostic shortcut. Passed to diagnose_scene_feasibility or analogous.
   *  Shape: { robot_path, sensor_path?, robot_base?, pick_pose?, drop_pose?,
   *           obstacles?, max_reach? }.
   *  Missing for exactly 1 template (CP-06, blocked). */
  diagnose_args: DiagnoseArgs;

  /** Human-readable verification result. Free text.
   *  E.g. "build-spec-2026-04-XX; form-gate ✓; function-gate ✓".
   *  SHOULD follow pattern: "build-spec-<date>; form-gate <✓|pending>;
   *  function-gate <✓|pending|blocked>". */
  verified_status: string;

  // ---- MANDATORY for T1 with extends ------------------------------------

  /** task_id of the template this one extends. Used by human authors to
   *  trace the inheritance chain. NOT read by runtime. */
  extends?: string;

  /** Human notes on what this extension adds or changes vs parent.
   *  ALWAYS present when `extends` is present (enforced by conformance). */
  extension_notes?: string;

  // ---- STRONGLY RECOMMENDED for T1 -------------------------------------

  /** Pre-execution world state (cube positions + conveyor velocities).
   *  Used by settle_after_canonical() to restore scene after build.
   *  Shape: { cubes: {"/World/Path": [x,y,z]}, conveyors: {"/World/Path": [vx,vy,vz]} }.
   *  Absent in ~24 CP templates; those fall back to regex extraction from `code`
   *  which fails on f-string-templated prim paths. Migration: add settle_state. */
  settle_state?: SettleState;

  // ---- ROLE-BASED FIELDS (currently CP-01..CP-05 only) -----------------
  //  These represent the target for the full migration. The conformance
  //  check treats their absence as a WARNING (not error) until migration
  //  reaches each cohort.

  /** Structured intent used by structural-filter retrieval.
   *  Drives LayoutSpec.intent matching (pattern_hint + counts +
   *  structural_features + structural_tags).
   *  Spec: service/isaac_assist_service/multimodal/types.py Intent class. */
  intent?: {
    pattern_hint: "pick_place" | "sort" | "reorient" | "navigate";
    counts?: {
      robots?: number;
      conveyors?: number;
      bins?: number;
      cubes?: number;
      sensors?: number;
      humans?: number;
    };
    structural_features?: {
      n_robot_stations?: number;
      n_handoffs?: number;
      n_destinations?: number;
      destination_kind?: "single_bin" | "n_bins_routed" | "shelf" | "fixture";
      routing_axis?: "color" | "size" | "shape" | "label";
      uses_conveyor_transport?: boolean;
      has_color_routing?: boolean;
      has_orientation_requirement?: boolean;
      has_bounded_footprint?: boolean;
      has_passive_intermediate_station?: boolean;
      has_active_intermediate_station?: boolean;
      [key: string]: boolean | number | string | null | undefined;
    };
    structural_tags?: string[];  // format: "isaac:segment.subsegment"
  };

  /** Role declarations. Specifies named roles + constraints + cardinality.
   *  Used by RoleRetriever (role_retriever.py) for role-hint-based lookup.
   *  Used by canonical_instantiator.py:instantiate_role_based_code() to
   *  substitute {{role.field}} placeholders when code_template is present.
   *  Shape: { [role_name]: { constraints, expected_count, required,
   *            disambiguator?, min?, max?, param_name?, unordered? } }
   *  Example: CP-01.json:33 — primary_robot, input_conveyor, primary_destination,
   *           workpieces. */
  roles?: Record<string, RoleDeclaration>;

  /** Default concrete values for each role. Used as substitution source
   *  when LayoutSpec has no ratified role bindings (text-only / canonical path).
   *  Shape: { [role_name]: {path, class?, position, orientation?, size?,
   *            surface_velocity?} | [{path, position, ...}] }
   *  Must mirror roles: every role in `roles` must have an entry here. */
  role_defaults?: Record<string, object | object[]>;

  /** Parameterized code. Uses {{role.field}} and {{role[N].field}} syntax.
   *  Substituted by substitute_role_placeholders() in canonical_instantiator.py.
   *  Equivalence test: tests/test_role_template_equivalence.py ensures that
   *  executing code_template + role_defaults produces identical tool calls
   *  to executing the legacy `code` field. */
  code_template?: string;

  /** Role-parameterized verify_args. Uses same {{...}} syntax.
   *  Substituted to produce verify_args values when role bindings exist. */
  verify_args_template?: object;

  /** Role-parameterized simulate_args. Uses same {{...}} syntax. */
  simulate_args_template?: object;

  // ---- DEPRECATED / ONE-OFF (do not author; migration removes) ---------

  /** @deprecated CP-01 only. Inline benchmark data belongs in docs. */
  benchmark_vs_alternatives?: object;

  /** @deprecated CP-01, CP-02 only. Superseded by verified_status string. */
  verified_date?: string;

  /** @deprecated CP-01, CP-02 only. Superseded by verified_status string. */
  verified_metrics?: object;

  /** @deprecated CP-06, CP-07 only. One-off experiment field. */
  delivery?: object;

  /** @deprecated CP-06, CP-07 only. Replaced by cube_path inside verify_args. */
  cube_path?: string;

  /** @deprecated CP-08 only. Build note as a top-level field.
   *  Belongs in extension_notes or verified_status. */
  compute_stack_placement_verified_2026_05_07?: object;

  /** @deprecated CP-NEW-multi-amr-corridor only. Typo of extension_notes. */
  extends_notes?: string;

  /** Blocked-infrastructure marker. Present only on CP-06.
   *  Not DEPRECATED — kept while infra is under repair. Remove when
   *  CP-06 delivery is fixed and verified. */
  blocked?: BlockedSpec;
}
```

---

## 4. Migration Plan

### 4.1 Migration scope matrix

| Cohort | Count | What to add | What to remove | Mechanism |
|---|---|---|---|---|
| Non-CP persona (A/D/E/F etc.) | 212 | Nothing (T2 is complete) | Nothing | No migration needed |
| CP numeric 06..87 (no roles) | 82 | `intent`, `roles`, `role_defaults`, `code_template`, `verify_args_template`, `simulate_args_template`; fill `settle_state` gaps | `delivery`, `cube_path` (CP-06/07), `compute_stack_placement_*` (CP-08), `benchmark_vs_alternatives` (CP-01), `verified_date`, `verified_metrics` | Mechanical + manual |
| CP-NEW-* (22 templates) | 22 | Same role-based fields as above; fix `extends_notes` typo (1 template) | `extends_notes` (1 template) | Same approach |
| CP-01..05 (already migrated) | 5 | Nothing; optionally move `benchmark_vs_alternatives` + `verified_date` + `verified_metrics` to docs | `benchmark_vs_alternatives`, `verified_date`, `verified_metrics` | Low-priority cleanup |

**Total requiring role migration: 104 templates (CP-06..87 + CP-NEW-*)**
**Total requiring settle_state gap fill: ~24 CP templates**

### 4.2 Fields that can be inferred mechanically

The following fields can be added programmatically without human re-authoring,
with confidence ≥ 85%:

**a) `intent.pattern_hint`** — can be inferred from `tools_used`:
- Contains `navigate_to` / `setup_nav_robot` → `"navigate"`
- Contains `setup_pick_place_with_vision` or `create_kit_tray` + color routing → `"sort"`
- Contains `create_prim(rotation_euler=...)` + `require_upright` in code → `"reorient"`
- Default → `"pick_place"` (covers ~85% of CP templates)

**b) `intent.structural_features.uses_conveyor_transport`** — `"create_conveyor" in tools_used`

**c) `intent.structural_features.has_color_routing`** — `"destination_map" in code or "routing_axis" in code`

**d) `intent.structural_features.has_orientation_requirement`** — `"require_upright" in code`

**e) `intent.structural_features.n_robot_stations`** — count of `robot_wizard` calls
  in code (parseable via `ast` or regex `robot_wizard(` occurrences)

**f) `intent.structural_features.destination_kind`**:
- `"create_kit_tray"` in tools_used → `"fixture"`
- multiple `create_bin` → `"n_bins_routed"`
- default `create_bin` → `"single_bin"`

**g) `intent.structural_tags`** — can be generated from tools_used + inferred features:
- `"create_conveyor"` → `"isaac:transport.conveyor"`
- `"robot_wizard"` → `"isaac:robot.fixed_base.arm"` (default; AMR variants differ)
- n_robot_stations == 1 → `"isaac:topology.single_station"`
- n_robot_stations > 1 → `"isaac:topology.multi_station"`

**h) `intent.counts`** — parseable from code:
- count `robot_wizard(` occurrences → `counts.robots`
- count `create_conveyor(` → `counts.conveyors`
- count `create_bin(` → `counts.bins`
- regex `create_prim.*prim_type="Cube"` or similar → `counts.cubes` (less reliable
  for loop-generated cubes, but best-effort from settle_state cube count)

**i) `settle_state` gap fill** — for the ~24 CP templates missing settle_state:
  the `_extract_cube_positions_from_code` and `_extract_conveyor_velocities_from_code`
  functions in `canonical_instantiator.py` already do this extraction. They can
  be run offline and the result baked in.

### 4.3 Fields requiring human re-authoring

The following cannot be inferred mechanically with acceptable confidence:

**a) `roles` dict** — the role declaration (constraints, disambiguator,
  min/max cardinality) requires understanding what entity classes the template
  expects. For CP-01..05 the author chose:
  - `primary_robot.constraints: ["franka_panda", "ur5e", "kinova_gen3"]`
  - `input_conveyor.constraints: ["conveyor"]`
  Inferring these from code is brittle (e.g., a template might accept `ur10e`
  but only uses `franka_panda` in its `code` field). The correct constraints
  are semantic, not syntactic.

**b) `role_defaults`** — follows from roles, but the exact numeric values
  (position, orientation, size, surface_velocity) must be extracted from code
  and formatted per-role. This is automatable *for the defaults* but not for
  the constraint set.

**c) `code_template`** — must be manually authored from the existing `code`.
  It requires replacing hardcoded prim paths with `{{role.path}}` placeholders.
  For CP-06..87 this is ~50-200 lines per template. Automation could extract
  known prim paths (via `_extract_prim_paths`) and replace them, but only for
  templates without loop-generated paths.

**d) `diagnose_args.obstacles`** — the list of obstacle prim paths for
  diagnosis is semantic. Can be partially inferred from `planning_obstacles`
  args in the code, but the mapping is not 1:1.

### 4.4 Migration sequence

1. **Phase 0 (automated, ~2h LOC):** Run offline script to:
   - Add `intent` with mechanically-inferred fields to all 104 CP-06..87 + CP-NEW-*
   - Fill `settle_state` for the 24 gap templates using existing extractor functions
   - Remove deprecated one-off fields (delivery, cube_path, compute_stack_*, extends_notes typo)
   - Rename `extends_notes` → `extension_notes` for CP-NEW-multi-amr-corridor

2. **Phase 1 (human review, 104 templates):** Verify `pattern_hint` for each
   template. Expected error rate ~10-15% on reorient/sort/navigate edge cases.
   Fix by hand.

3. **Phase 2 (human authoring, 104 templates):** Add `roles` + `role_defaults`.
   Start with the 22 CP-NEW-* templates (freshest, best-documented).
   Use CP-01..05 as reference authoring pattern (file: `workspace/templates/CP-01.json`).

4. **Phase 3 (automated, per-template):** For each template with complete
   `roles` + `role_defaults`, generate `code_template` by extracting prim paths
   and replacing with `{{role.field}}` placeholders. Verify equivalence via
   `tests/test_role_template_equivalence.py`.

5. **Phase 4 (cleanup):** Remove deprecated fields. Keep `code` alongside
   `code_template` until full equivalence confirmed (as CP-01..05 do now).

---

## 5. Conformance Check Tool Specification

A CI script `scripts/lint/lint_canonical_templates.py` should validate every
template at commit time. Below is concrete pseudocode.

```python
# scripts/lint/lint_canonical_templates.py
#
# Usage:
#   python scripts/lint/lint_canonical_templates.py            # all templates
#   python scripts/lint/lint_canonical_templates.py --strict   # exit 1 on ERROR
#   python scripts/lint/lint_canonical_templates.py --warn-roles  # exit 1 on WARN too
#
# Exit codes: 0=clean, 1=errors found (when --strict), 2=JSON parse failure

import json, sys, re
from pathlib import Path

TEMPLATES_DIR = Path("workspace/templates")
STRUCTURAL_TAG_FORMAT = re.compile(r"^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$")
PATTERN_HINTS = {"pick_place", "sort", "reorient", "navigate"}
DESTINATION_KINDS = {"single_bin", "n_bins_routed", "shelf", "fixture"}
ROUTING_AXES = {"color", "size", "shape", "label"}

CORE_FIELDS = ["task_id", "goal", "tools_used", "thoughts", "code", "failure_modes"]
T1_FIELDS = ["verify_args", "simulate_args", "diagnose_args", "verified_status"]

def is_cp_template(task_id: str) -> bool:
    return task_id.startswith("CP-")

def lint_one(path: Path) -> list[dict]:
    """Return list of {level, rule, message} dicts. Level: ERROR | WARN | INFO."""
    issues = []

    def err(rule, msg):
        issues.append({"level": "ERROR", "rule": rule, "message": msg})
    def warn(rule, msg):
        issues.append({"level": "WARN", "rule": rule, "message": msg})

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        err("JSON_PARSE", f"Invalid JSON: {e}")
        return issues

    task_id = data.get("task_id", "?")
    is_cp = is_cp_template(str(task_id))

    # Rule C1: Core fields present and non-empty
    for f in CORE_FIELDS:
        if f not in data:
            err("C1_MISSING_CORE_FIELD", f"Missing mandatory field: {f!r}")
        elif not data[f]:
            err("C1_EMPTY_CORE_FIELD", f"Core field is empty: {f!r}")

    # Rule C2: task_id matches filename
    expected_tid = path.stem  # e.g. "CP-01"
    if str(task_id) != expected_tid:
        err("C2_TASK_ID_MISMATCH", f"task_id {task_id!r} != filename stem {expected_tid!r}")

    # Rule C3: tools_used is a list of non-empty strings
    tus = data.get("tools_used")
    if not isinstance(tus, list) or not all(isinstance(t, str) and t for t in tus):
        err("C3_TOOLS_USED_INVALID", "tools_used must be a non-empty list of strings")

    # Rule C4: failure_modes is a list of strings
    fms = data.get("failure_modes")
    if not isinstance(fms, list) or not all(isinstance(f, str) for f in fms):
        err("C4_FAILURE_MODES_INVALID", "failure_modes must be a list of strings")

    if is_cp:
        # Rule T1: T1 fields present for CP templates
        for f in T1_FIELDS:
            if f not in data:
                err("T1_MISSING_FIELD", f"CP template missing T1 field: {f!r}")

        # Rule T2: verify_args has required subkeys
        va = data.get("verify_args")
        if isinstance(va, dict):
            stages = va.get("stages")
            if not isinstance(stages, list) or not stages:
                err("T2_VERIFY_ARGS_NO_STAGES", "verify_args.stages must be a non-empty list")
            else:
                for i, stage in enumerate(stages):
                    for key in ("robot_path", "pick_path", "place_path"):
                        if key not in stage:
                            err("T2_VERIFY_ARGS_STAGE_MISSING",
                                f"verify_args.stages[{i}] missing key: {key!r}")

        # Rule T3: simulate_args has required subkeys
        sa = data.get("simulate_args")
        if isinstance(sa, dict):
            for key in ("cube_path", "target_path", "duration_s"):
                if key not in sa:
                    err("T3_SIMULATE_ARGS_MISSING", f"simulate_args missing key: {key!r}")

        # Rule T4: extends ↔ extension_notes must co-occur
        has_extends = "extends" in data
        has_ext_notes = "extension_notes" in data
        if has_extends and not has_ext_notes:
            err("T4_EXTENDS_NO_NOTES", "extends present but extension_notes absent")
        if has_ext_notes and not has_extends:
            warn("T4_NOTES_NO_EXTENDS", "extension_notes present but extends absent")

        # Rule T5: settle_state recommended
        if "settle_state" not in data:
            warn("T5_MISSING_SETTLE_STATE",
                 "settle_state absent; settle_after_canonical will use fragile regex fallback")

        # Rule T6: no deprecated one-off fields
        DEPRECATED = {"delivery", "cube_path", "compute_stack_placement_verified_2026_05_07",
                      "extends_notes", "benchmark_vs_alternatives"}
        found_dep = set(data.keys()) & DEPRECATED
        for f in found_dep:
            warn("T6_DEPRECATED_FIELD", f"Deprecated field present: {f!r} — migrate or remove")

        # Rule R1: intent structure (WARN until migration completes)
        intent = data.get("intent")
        if intent is None:
            warn("R1_MISSING_INTENT", "No intent field — template not visible to structural-filter retrieval")
        elif isinstance(intent, dict):
            ph = intent.get("pattern_hint")
            if ph not in PATTERN_HINTS:
                err("R1_BAD_PATTERN_HINT", f"intent.pattern_hint {ph!r} not in {PATTERN_HINTS}")
            tags = intent.get("structural_tags", [])
            for tag in tags:
                if not STRUCTURAL_TAG_FORMAT.match(tag):
                    err("R1_BAD_TAG_FORMAT", f"structural_tag {tag!r} does not match format")
            sf = intent.get("structural_features", {})
            dk = sf.get("destination_kind")
            if dk is not None and dk not in DESTINATION_KINDS:
                err("R1_BAD_DESTINATION_KIND", f"destination_kind {dk!r} not valid")
            ra = sf.get("routing_axis")
            if ra is not None and ra not in ROUTING_AXES:
                err("R1_BAD_ROUTING_AXIS", f"routing_axis {ra!r} not valid")

        # Rule R2: roles + role_defaults + code_template must be all-or-nothing
        has_roles = "roles" in data and bool(data["roles"])
        has_rd = "role_defaults" in data and bool(data["role_defaults"])
        has_ct = "code_template" in data and bool(data["code_template"])
        role_fields_present = sum([has_roles, has_rd, has_ct])
        if role_fields_present > 0 and role_fields_present < 3:
            err("R2_PARTIAL_ROLE_FIELDS",
                f"roles/role_defaults/code_template must all be present or all absent "
                f"(found {role_fields_present}/3)")
        if has_roles:
            # Rule R3: every role in roles must have an entry in role_defaults
            roles = data.get("roles", {})
            rd = data.get("role_defaults", {})
            for role_name in roles:
                if role_name not in rd:
                    err("R3_ROLE_DEFAULT_MISSING",
                        f"roles declares {role_name!r} but role_defaults has no entry for it")

    return issues


def main():
    strict = "--strict" in sys.argv
    warn_roles = "--warn-roles" in sys.argv
    templates = sorted(TEMPLATES_DIR.glob("*.json"))
    total_errors = 0
    total_warns = 0

    for path in templates:
        issues = lint_one(path)
        errors = [i for i in issues if i["level"] == "ERROR"]
        warns = [i for i in issues if i["level"] == "WARN"]
        total_errors += len(errors)
        total_warns += len(warns)
        if errors or (warn_roles and warns):
            print(f"\n{path.name}:")
            for i in errors:
                print(f"  ERROR [{i['rule']}] {i['message']}")
            if warn_roles:
                for i in warns:
                    print(f"  WARN  [{i['rule']}] {i['message']}")

    print(f"\n{len(templates)} templates: {total_errors} errors, {total_warns} warnings")
    if strict and total_errors > 0:
        sys.exit(1)
    if warn_roles and (total_errors + total_warns) > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
```

**Gate integration:** Add to CI (`.github/workflows/ci.yml` or equivalent):
```yaml
- name: Lint canonical templates
  run: python scripts/lint/lint_canonical_templates.py --strict
```

This runs in under 2 seconds (pure JSON parsing, no imports). The `--strict`
flag fails only on ERROR-level violations (missing core fields, bad structural
format). WARN-level violations (missing intent, missing settle_state, deprecated
fields) are visible but do not block merges until migration completes.

---

## 6. Risk and Cost Estimate

### 6.1 Mechanical migration (Phase 0 + settle_state fill)

**Effort:** ~200 LOC migration script.

**Scope:** 104 templates get `intent` with mechanically-inferred fields.
24 CP templates get `settle_state` filled.

**Risk:** LOW. These are additive-only changes. The `canonical_instantiator.py`
reads `code` for execution — it does NOT read `intent` or `roles` for the
hard-instantiate path. Adding these fields to a template cannot break the
existing execution behavior.

**Backward-compat impact:** Zero. Existing similarity-based retrieval ignores
unknown fields. The structural-filter retrieval path is gated OFF in
production (`env-gated off by default` per the prior audit). Intent fields
only become live when the gate is enabled.

### 6.2 Human re-authoring (roles + role_defaults + code_template)

**Effort per template:** ~30-60 min per template for an experienced author.
- 104 templates × 45 min avg = ~78 person-hours
- If done by an agent with CP-01..05 as patterns: ~15-20 min per template via
  code generation + equivalence test feedback loop = ~30-35 person-hours

**Error modes:**
- Wrong role constraints (e.g., listing `ur10` instead of `ur10e`)
- Missing role in `roles` dict (e.g., forgot `pick_sensor` role)
- `code_template` substitution leaves unfilled `{{...}}` — caught by
  `test_role_template_equivalence.py` test

**Risk:** MEDIUM. The equivalence test (`tests/test_role_template_equivalence.py`)
provides a hard gate: if `code_template + role_defaults` produces different
tool calls than `code`, the test fails. This protects against silent regressions
in the hard-instantiate path.

**Breaking hard-instantiate:** The current `execute_template_canonical` reads
`template["code"]` directly (line `raw_code = template.get("code") or ""`).
It does NOT use `code_template`. The `instantiate_role_based_code` function
exists but is only called when `role_bindings` is provided (which only happens
via the LayoutSpec ratify path, not the similarity path). Therefore:

- Keeping `code` alongside `code_template` (as CP-01..05 do) is SAFE.
- Removing `code` should only happen after `instantiate_role_based_code` is
  wired into `execute_template_canonical` — which is a separate Phase 28+
  work item.

### 6.3 Deprecated field removal

**Effort:** 10 LOC migration script. Near-zero risk.
Fields: `delivery` (CP-06/07), `cube_path` (CP-06/07),
`compute_stack_placement_verified_2026_05_07` (CP-08), `extends_notes` (CP-NEW-multi-amr-corridor),
`benchmark_vs_alternatives` + `verified_date` + `verified_metrics` (CP-01).

**Risk:** LOW. None of these fields are read by any production code path.
Verified by `grep -r "delivery\|cube_path\|benchmark_vs_alternatives\|verified_date\|verified_metrics"
service/` — all occurrences are inside templates, not service code.

### 6.4 Summary table

| Phase | Templates affected | LOC | Risk | Breaks hard-instantiate? |
|---|---|---|---|---|
| 0: Add `intent` mechanically | 104 | ~200 | Low | No |
| 0b: Fill `settle_state` gaps | ~24 | ~50 | Low | No |
| 0c: Remove deprecated fields | 7 templates, 7 fields | ~10 | Low | No |
| 1: Verify `pattern_hint` by hand | 104 | 0 | Low | No |
| 2: Author `roles` + `role_defaults` | 104 | ~15000* | Medium | No |
| 3: Generate + validate `code_template` | 104 | ~10000* | Medium | No |
| 4: Remove legacy `code` field | 104 | N/A | High** | YES** |

*Rough LOC estimate for template JSON content.
**Phase 4 should NOT be executed until `execute_template_canonical` is
updated to call `instantiate_role_based_code` instead of reading `code` directly.
That is a separate code change in `service/isaac_assist_service/chat/canonical_instantiator.py`.

---

## 7. Backward-Compatibility Strategy

### 7.1 Coexistence period

The schema supports old and new fields coexisting indefinitely:

- `code` + `code_template` coexist in CP-01..05 today. The runtime reads
  `code` for hard-instantiate; `code_template` is used only when
  `instantiate_role_based_code` is explicitly called.
- `intent` fields are additive; the similarity-based retriever ignores them.
- `roles` fields are additive; the legacy dict-based retriever ignores them.

**Recommended coexistence duration:** Until the structural-filter retrieval
path is enabled in production (currently env-gated off). No hard deadline.

### 7.2 Deprecation timeline

```
                    NOW         Phase 0-3    Phase 4    Post-Phase 4
                                (additive)   (risky)    (cleanup)

code field:         CANONICAL   CANONICAL    CANONICAL  REMOVED
                                             (keep)     (after wiring
                                                         code_template path)

intent field:       5/321       321/321      321/321    321/321
                    (CP-01..05)

roles field:        5/321       5/321        321/321    321/321
                                (add during Phase 2)

code_template:      5/321       5/321        321/321    321/321

deprecated fields:  7 templates  REMOVED     REMOVED    REMOVED
(delivery, etc.)
```

### 7.3 The one irremovable risk

CP-06's `blocked` field indicates infrastructure that was built but is not
delivering cubes. That template's `code` cannot be migrated to a working
`code_template` until the underlying PickPlaceController FixedJoint attachment
bug is resolved (documented in `CP-06.json:blocked.root_cause_hypothesis`).
**CP-06 should be excluded from Phase 2/3 migration until unblocked.**

### 7.4 CP-NEW-* templates

All 22 CP-NEW-* templates have `verified_status: "draft"` or similar.
They should go through Phases 0-3 AFTER the CP numeric templates, using
the same conformance gate. Their `extends` field (all 22 have one) means
their `intent` can inherit from the parent template's pattern.

---

## 8. Key File References

| File | Relevance |
|---|---|
| `workspace/templates/CP-01.json` | Richest template; reference for all role-based fields. Lines 1-214. |
| `workspace/templates/CP-01.json:19-56` | `intent` + `roles` — canonical schema example |
| `workspace/templates/CP-01.json:58-87` | `role_defaults` — per-role default values |
| `workspace/templates/CP-01.json:88-176` | `code_template` — {{role.field}} substitution syntax |
| `workspace/templates/A-01.json` | T2 (dialogue) template — core-6 only, advisory code |
| `workspace/templates/CP-06.json:3-20` | `blocked` field structure — infra-paused pattern |
| `workspace/templates/CP-08.json` | `compute_stack_placement_verified_*` anomalous field |
| `workspace/templates/CP-NEW-multi-amr-corridor.json` | `extends_notes` typo |
| `service/isaac_assist_service/multimodal/types.py` | Intent, StructuralFeatures, LayoutSpec Pydantic models |
| `service/isaac_assist_service/multimodal/types.py:46-51` | PatternHint closed enum |
| `service/isaac_assist_service/multimodal/types.py:105-136` | StructuralFeatures typed fields |
| `service/isaac_assist_service/chat/canonical_instantiator.py:482-582` | execute_template_canonical — reads `code` field |
| `service/isaac_assist_service/chat/canonical_instantiator.py:459-479` | instantiate_role_based_code — reads `code_template` |
| `service/isaac_assist_service/chat/canonical_instantiator.py:405-456` | substitute_role_placeholders — {{role.field}} engine |
| `service/isaac_assist_service/chat/tools/role_retriever.py:99-297` | RoleRetriever — consumes `roles` indirectly via RoleTemplateIndex |
| `service/isaac_assist_service/multimodal/canonical_templates_b1b.py` | Phase 28 in-code role schema (different from JSON templates) |
| `service/isaac_assist_service/multimodal/migrations/__init__.py` | Forward-migration framework (currently no active migrations) |
| `tests/test_role_template_equivalence.py:70-104` | Equivalence gate for CP-01..05 — model for Phase 3 |
| `tests/test_role_based_templates.py` | Phase 20 retriever tests |

---

## 8. Lint Baseline 2026-05-15

**Script:** `scripts/lint_canonical_templates.py` (reads schema from `scripts/canonical_schema.py`)
**Run date:** 2026-05-15
**Templates scanned:** 321

### 8.1 Summary Counts

| Level | Count | Description |
|-------|-------|-------------|
| OK | 212 | No issues |
| ERROR | 17 | Must fix before merging |
| WARN | 26 | Recommended fixes; do not block merge yet |
| INFO | 208 | Migration-pending signals (role fields absent) |

### 8.2 Error Breakdown by Rule

| Rule | Count | Meaning |
|------|-------|---------|
| `DEP_FIELD_PRESENT` | 10 | Deprecated one-off field present (see Q4 §2) |
| `C1_EMPTY_CORE_FIELD` | 2 | Core field present but empty (AD-03, AD-04: empty `tools_used`) |
| `C3_TOOLS_USED_EMPTY` | 2 | `tools_used` is an empty list (same templates) |
| `T1_MISSING_FIELD` | 1 | CP template missing T1-mandatory `diagnose_args` (CP-07) |
| `R1_BAD_DESTINATION_KIND` | 1 | `intent.structural_features.destination_kind` has value `'color_routed'` (not in valid set); CP-03 |

**Note:** The original schema spec required `cube_path` in `simulate_args`, but 25 templates legitimately
use `cube_paths` (plural list) for multi-cube scenarios. The lint rule was corrected to accept either
`cube_path` or `cube_paths`. This reduced the ERROR count from 42 to 17.

### 8.3 Warn Breakdown by Rule

| Rule | Count | Meaning |
|------|-------|---------|
| `T1_MISSING_SETTLE_STATE` | 24 | CP templates missing `settle_state` — all are CP-NEW-* (22) plus CP-06 and CP-87 |
| `T1_NOTES_NO_EXTENDS` | 2 | `extension_notes` present but `extends` absent (CP-06, CP-07) |

### 8.4 Info Breakdown by Rule

| Rule | Count | Meaning |
|------|-------|---------|
| `R1_MISSING_INTENT` | 104 | CP templates without `intent` field (migration pending) |
| `R2_MISSING_ROLE_FIELDS` | 104 | CP templates without `roles`/`role_defaults`/`code_template` trio |

### 8.5 Example Paths per Error Type

**`DEP_FIELD_PRESENT` (10 errors across 6 files):**
- `workspace/templates/CP-01.json` — `benchmark_vs_alternatives`, `verified_date`, `verified_metrics`
- `workspace/templates/CP-06.json` — `delivery`, `cube_path`
- `workspace/templates/CP-NEW-multi-amr-corridor.json` — `extends_notes`

**`C1_EMPTY_CORE_FIELD` / `C3_TOOLS_USED_EMPTY` (2 errors each):**
- `workspace/templates/AD-03.json` — `tools_used: []`
- `workspace/templates/AD-04.json` — `tools_used: []`

**`R1_BAD_DESTINATION_KIND` (1 error):**
- `workspace/templates/CP-03.json` — `destination_kind: 'color_routed'` (valid values: `single_bin`, `n_bins_routed`, `shelf`, `fixture`)

**`T1_MISSING_SETTLE_STATE` (24 warns — sample):**
- `workspace/templates/CP-87.json`
- `workspace/templates/CP-NEW-brick-stacking.json`
- `workspace/templates/CP-NEW-amr-pickup-handoff.json`

### 8.6 Action Priority

1. **Immediate (block CI):** Fix `AD-03`/`AD-04` empty `tools_used` (2 files).
2. **Short-term:** Remove deprecated fields from CP-01, CP-02, CP-06, CP-07, CP-08, CP-NEW-multi-amr-corridor (6 files, mechanical).
3. **Medium-term:** Fix CP-03 invalid `destination_kind` value; add `diagnose_args` to CP-07.
4. **Migration backlog:** 104 CP templates need `intent` + `roles` + `role_defaults` + `code_template` (per §4 migration plan); 24 need `settle_state`.

## 9. Motion-Controller Compatibility Field (added 2026-05-15)

A `motion_controllers` field was added to the canonical schema after
Anton flagged that canonicals use different planners (cuRobo, RMPflow,
admittance/impedance, MoveIt2, etc.) and the library needs to record
which ones each canonical has been verified-compatible with.

**Shape:**

```json
{
  "motion_controllers": {
    "verified": ["curobo@1.8.2", "rmpflow"],
    "failed":   {"admittance": "physx_instability_at_contact"},
    "untested": ["moveit2"]
  }
}
```

**Lint rules added** (see `scripts/canonical_schema.py` and
`scripts/lint_canonical_templates.py`):

- `T1_MC_MISSING` (WARN) — T1 template's `tools_used` contains a
  motion-planning tool (`plan_trajectory`, `move_to_pose`,
  `setup_admittance_controller`, etc.) but no `motion_controllers`
  declared. Fires on **109 templates** in the current baseline.
- `T1_MC_MISSING_INFO` (INFO) — T1 template with no motion-planning
  tools and no `motion_controllers` declared. Fires on **17 templates**.
- `T1_MC_TYPE` (ERROR) — field present but not a dict.
- `T1_MC_UNKNOWN_NAME` (WARN) — controller name not in
  `VALID_MOTION_CONTROLLER_NAMES` (typo guard).
- `T1_MC_FAILED_REASON` (ERROR) — `failed` entry missing reason string.

**Honesty rule:** `verified` means an actual successful run exists;
absence means untested (not "works"); `failed` requires a non-empty
reason. Version-pin syntax `name@version` is supported because
controller versions matter (Warp 1.8.2 → 1.11.0 changed behavior).

**Baseline impact:** counts shifted from `212 OK / 17 ERROR / 26 WARN /
208 INFO` to `210 OK / 17 ERROR / 118 WARN / 225 INFO`. ERROR count
unchanged.

**Migration approach:** populated mechanically per-canonical during the
Track B migration pass, derived from `verified_status` notes and
function-gate run history. Conservative default: leave absent rather
than guess.
