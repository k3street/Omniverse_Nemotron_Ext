# Compositional Canonicals — Primitive Library + Composition Engine

**Date:** 2026-05-11
**Status:** first draft — applies *after* IA Full Spec, Contact-Rich,
Kit-Supervisor and Stack-Evaluation specs have landed
**Owner:** TBD
**Estimated LOC:** ~3000-4500 (primitives + engine + tests + migrations)

**Dependencies:**
- IA Full Spec (esp. Phase 20 role-based templates, Phase 25 palette,
  Phase 80b grip-stability)
- `docs/specs/2026-05-11-contact-rich-manipulation-spec.md`
- `docs/specs/2026-05-11-kit-supervisor-spec.md`
- `docs/specs/2026-05-11-stack-evaluation-spec.md`
- Multimodal Foundation Spec (LayoutSpec IR)

---

## 0. TL;DR

Today: 109 canonical templates, each a monolithic complete scene
(~30-100 tool-calls). Patterns like "Franka + table + DomeLight"
duplicated in 80+ templates. Bug fixes need 80 patches.

Vision: 30-50 reusable **primitives** (Franka, conveyor, bin,
flip-wall, rotary-table, ...) + a **composition engine** that
combines them into scenes. CP-01 becomes:
```yaml
composition:
  - {primitive: table_workcell}
  - {primitive: franka_at, args: {pos: [0, 0, 0.75]}}
  - {primitive: conveyor_with_cubes, args: {n_cubes: 4}}
  - {primitive: bin_at, args: {pos: [0, -0.4, 0.75]}}
  - {primitive: pick_place_controller}
```

Result: bug fix to conveyor → fix 1 primitive → all 80 CPs benefit.
New CPs become drag-drop or LLM-driven compositions of existing
primitives.

This spec defines the primitive API, composition engine, migration
plan from atomic templates, and integration with Canvas modality.

---

## 1. Problem statement

### 1.1 What atomic templates cost us

Audit of `workspace/templates/CP-*.json` (109 templates):

| Pattern | Duplication count |
|---|---|
| DomeLight + Ground + Table | ~80 CPs |
| Franka on table (orientation rule) | ~60 CPs |
| Conveyor + cubes + sleepThreshold + rubber | ~50 CPs |
| Bin destination + drop_target | ~50 CPs |
| Pick-place controller | ~80 CPs |
| Color-routing | ~10 CPs |
| Flip-wall / rotary / dispenser | ~5-8 CPs each |

A typical patch to "the conveyor pattern" (e.g., adding
`PhysxSurfaceVelocityAPI` ordering fix per Phase 80b) requires editing
50+ JSON files. We've done this 5+ times in the last week:
- duration_s ≥ 180 patch: 45 CPs
- cube_paths upgrade: 13 CPs
- drop_target explicit: 17 CPs
- solverPositionIterationCount=16: 7 CPs

Each patch is a regex-search across 109 files. Risk of inconsistency
high; coverage gaps hard to verify.

### 1.2 What we lose downstream

- **Drag-drop canvas can't compose new scenes.** The multimodal
  foundation (LayoutSpec.objects) emits typed objects, but the
  canonical pipeline only matches against complete atomic templates.
  You can drag a Franka onto a canvas — but there's no `franka_at`
  primitive for the system to instantiate from that placement.
- **LLM can't propose novel compositions.** The agent picks from 109
  fixed CPs. It can't say "I'll combine the flip-wall pattern from
  CP-05 with the rotary-table from CP-67."
- **Hard to write new yrkesroll-CPs.** Each new vocational template
  duplicates 30-50 tool-calls. The yrkesroll-track stalls when adding
  the 25th template feels like the 1st.

### 1.3 What composition gives back

- Fix once, propagate everywhere
- Canvas drag-drop → automatic primitive selection
- LLM agent composes new CPs from natural language
- Each primitive is ~10-30 tool-calls; testable in isolation
- New canonical = combine 5-10 primitives, ~5 minutes work

---

## 2. Architecture

```
┌───────────────────────────────────────────────────────────────┐
│ User input: text prompt | canvas placement | template id      │
└────────────────────────────┬──────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────┐
│ Composition resolver                                          │
│   • LayoutSpec.objects → primitive[] (drag-drop path)         │
│   • Template.composition → primitive[] (declared path)        │
│   • LLM proposal → primitive[] (free composition path)        │
└────────────────────────────┬──────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────┐
│ Composition engine                                            │
│   • Resolve primitive parameters (defaults + overrides)       │
│   • Order primitives by dependency graph                      │
│   • Auto-suffix prim paths (Franka_1, Franka_2)              │
│   • Allocate role bindings (primary_robot → /World/Franka_1) │
└────────────────────────────┬──────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────┐
│ Primitive instantiation                                       │
│   for each primitive in dependency order:                     │
│     code += primitive.expand(args, allocated_paths)           │
└────────────────────────────┬──────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────┐
│ Existing execute_template_canonical pipeline                  │
│   (unchanged — composition produces the same `code` field)    │
└───────────────────────────────────────────────────────────────┘
```

**Key insight:** composition produces the EXACT SAME `code` field
existing atomic templates have today. Engine is a code-generator;
downstream execution is unchanged. Zero risk to verifier, supervisor,
or function-gate.

---

## 3. Primitive specification

### 3.1 Primitive file format

`workspace/primitives/<name>.json`:

```jsonc
{
  "primitive_id": "franka_at",
  "version": "1.0.0",
  "category": "robot",
  "summary": "Place a Franka Panda at a table-top position with
             standard orientation (90° around Z so +Y faces robot
             base +X).",

  "params": {
    "pos": {"type": "list[float]", "default": [0, 0, 0.75]},
    "robot_name_suffix": {"type": "str", "default": ""},
    "orientation": {"type": "list[float]",
                    "default": [0.7071068, 0, 0, 0.7071068]},
    "robot_class": {"type": "str", "default": "franka_panda",
                    "enum": ["franka_panda", "franka_research3"]}
  },

  "produces": {
    "prim_paths": ["/World/Franka{robot_name_suffix}"],
    "roles_satisfied": ["primary_robot"]
  },

  "consumes": {
    "expects_table_at_z": 0.75,
    "requires_primitives": ["table_workcell"]
  },

  "code_template": "robot_wizard(robot_name=\"{{robot_class}}\",
                                  dest_path=\"/World/Franka{{robot_name_suffix}}\",
                                  position={{pos}},
                                  orientation={{orientation}})",

  "verify_args_contribution": {
    "stages": [{"robot_path": "/World/Franka{{robot_name_suffix}}",
                "robot_kind": "{{robot_class}}"}]
  },

  "settle_state_contribution": {}
}
```

Fields:
- `primitive_id`: unique stable identifier
- `version`: semver; bumped when behavior changes
- `category`: `robot`, `transport`, `destination`, `sensor`,
  `controller`, `material`, `routing`, `support_structure`
- `params`: parameter schema with defaults
- `produces`: prim paths created + roles this primitive satisfies
- `consumes`: hard dependencies (which primitives must come first) +
  z-height assumptions + scene assumptions
- `code_template`: substitution string (uses {{name}} like §6.1)
- `verify_args_contribution`: what to add to `simulate_args.stages`
- `settle_state_contribution`: what to add to `settle_state`

### 3.2 Initial primitive set (~35 primitives)

| Category | Primitives |
|---|---|
| **Foundation** | `table_workcell`, `ground`, `dome_light`, `cell_xform`, `physics_scene_cpu` |
| **Robot** | `franka_at`, `ur10_at`, `ur5e_at`, `kinova_at`, `nova_carter_at` (AMR) |
| **Transport** | `conveyor_with_cubes`, `conveyor_empty`, `rotary_table`, `gravity_dispenser`, `recirculation_loop` |
| **Destination** | `bin_at`, `tinted_bin_at`, `pallet_at`, `tray_at`, `slot_panel` |
| **Sensors** | `proximity_sensor_at`, `ft_sensor_on`, `camera_at`, `lidar_on` |
| **Materials** | `color_material_red`, `color_material_blue`, `physics_material_rubber`, `physics_material_metal` |
| **Routing** | `color_routing_2bin`, `color_routing_4bin`, `defect_reject_routing` |
| **Special** | `flip_wall_at`, `pick_sensor_zone`, `landing_zone_marker`, `hole_panel` |
| **Controllers** | `pick_place_controller`, `multi_robot_handoff_controller`, `pick_place_with_color_routing`, `amr_navigate_to` |
| **Compliance** | `attach_admittance_controller`, `attach_impedance_controller` (per Contact-Rich spec) |
| **Stability** | `apply_phase_80b_grip_safe` (per Phase 80b) |

Each primitive: ~5-30 tool calls. Total primitive corpus: ~600-800
tool calls (vs current 109 templates × ~50 calls = ~5400 duplicated
calls).

---

## 4. Composition engine

### 4.1 Composition declaration in template

```jsonc
{
  "task_id": "CP-01-composed",
  "intent": { ... },
  "composition": [
    {"primitive": "dome_light"},
    {"primitive": "table_workcell", "args": {"size": [2.0, 1.0, 0.75]}},
    {"primitive": "physics_scene_cpu"},
    {"primitive": "franka_at", "args": {"pos": [0, 0, 0.75]}},
    {"primitive": "conveyor_with_cubes", "args": {
      "pos": [0.0, 0.4, 0.78],
      "size": [3.0, 0.4, 0.05],
      "n_cubes": 4,
      "cube_x_positions": [-1.4, -1.15, -0.9, -0.65]
    }},
    {"primitive": "bin_at", "args": {"pos": [0, -0.4, 0.75]}},
    {"primitive": "proximity_sensor_at", "args": {
      "pos": [0.4, 0.4, 0.835], "name": "PickSensor"
    }},
    {"primitive": "pick_place_controller", "args": {
      "target_source": "curobo"
    }}
  ]
}
```

### 4.2 Engine algorithm

```python
def compose(composition: list[PrimitiveCall]) -> CompositionResult:
    # 1. Load all referenced primitives
    primitives = [load_primitive(c["primitive"]) for c in composition]

    # 2. Validate dependency graph (table_workcell must precede franka_at)
    topo_order = resolve_dependencies(primitives)

    # 3. Auto-allocate prim path suffixes for duplicates
    # (e.g., two franka_at calls → /World/Franka_1, /World/Franka_2)
    allocator = PrimPathAllocator()
    args_resolved = [allocator.resolve(p, c["args"]) for p, c in
                     zip(topo_order, composition)]

    # 4. Generate code by expanding each primitive's code_template
    code_parts = []
    for primitive, args in zip(topo_order, args_resolved):
        code_parts.append(primitive.expand(args))

    # 5. Compose verify_args + settle_state from contributions
    verify_args = merge_verify_args(topo_order, args_resolved)
    settle_state = merge_settle_state(topo_order, args_resolved)

    # 6. Allocate role bindings (primary_robot → /World/Franka_1)
    role_bindings = allocate_roles(topo_order, args_resolved)

    return CompositionResult(
        code="\n".join(code_parts),
        verify_args=verify_args,
        settle_state=settle_state,
        role_bindings=role_bindings,
        primitive_manifest=[p.primitive_id for p in topo_order],
    )
```

### 4.3 Path allocator

When two primitives produce the same default prim path (`franka_at`
default `/World/Franka`), the allocator auto-suffixes:

```python
class PrimPathAllocator:
    def resolve(self, primitive, args):
        for produces_path in primitive.produces["prim_paths"]:
            if produces_path in self._allocated:
                args = self._suffix(primitive, args)  # /Franka → /Franka_1
            self._allocated.add(resolved_path)
        return args
```

This is what makes 2 × `franka_at` "just work" — no name collision.

### 4.4 Role binding resolution

The composition engine builds the `role_bindings` map per Contact-Rich
Spec §6.1:

- Each primitive declares `roles_satisfied`
- `franka_at` declares `["primary_robot"]`; the FIRST franka_at in
  composition order binds to `primary_robot`
- The SECOND franka_at gets `secondary_robot` (per role schema in
  templates that declare it)
- More than 2 robots → fall back to ratifier (per multimodal foundation)

---

## 5. Migration from atomic templates

### 5.1 Phased migration

| Phase | Scope | Risk |
|---|---|---|
| M1 | Land primitive library + composition engine | New code only; no migration |
| M2 | Migrate CP-01 to composed form | Equivalence test enforces no diff |
| M3 | Migrate CP-02..05 | role-based templates already done |
| M4 | Migrate "easy" CPs (basic pick-place, sort) | ~40 CPs |
| M5 | Migrate complex CPs (rotary, dispenser) | ~30 CPs |
| M6 | Migrate yrkesroll + remaining edge cases | ~30 CPs |
| M7 | Remove `code` field; only `composition` remains | Final cleanup |

### 5.2 Equivalence test (gate for each migration)

For every migrated template:
- Sandbox-capture tool-call sequence from legacy `code` field
- Sandbox-capture tool-call sequence from composed `composition` field
- Assert identical `(tool_name, args)` lists modulo whitespace/order
- This extends the existing `test_role_template_equivalence.py` pattern
  from Block 1B Step 18

### 5.3 Backwards compat during migration

Templates can have BOTH `code` AND `composition` fields during migration:
- If only `code`: legacy path
- If only `composition`: new path
- If both: assert equivalence in tests, prefer `composition` at runtime
- After M7: `code` field deprecated and removed

---

## 6. Integration with Canvas modality

### 6.1 Drag-drop → primitive selection

The Canvas SPA emits `LayoutSpec.objects` per multimodal foundation §3.
Each `TypedObject` has a `class` (franka_panda, conveyor, bin, ...).

A new resolver maps object_class → primitive:

```python
OBJECT_CLASS_TO_PRIMITIVE = {
    "franka_panda": "franka_at",
    "ur10": "ur10_at",
    "conveyor": "conveyor_empty",  # composed with cubes if cubes nearby
    "bin": "bin_at",
    "cube": None,  # consumed by conveyor_with_cubes
    "rotary_table": "rotary_table",
    ...
}
```

When user drags Franka + conveyor + 4 cubes + bin onto canvas:
1. LayoutSpec emitted with 7 objects
2. Resolver groups: 1 franka + 1 conveyor-with-4-cubes + 1 bin
3. Composition built: 3 primitives + controller + sensor
4. Composition engine generates code → execute_template_canonical

### 6.2 LLM-driven composition

The agent (Isaac Assist chat) can propose new CPs as compositions:

```
USER: "Build me a 3-robot assembly line with rotary table in middle"
AGENT: Proposing composition: [
  table_workcell × 1,
  franka_at × 3,
  conveyor_with_cubes × 1,
  rotary_table × 1,
  bin_at × 1,
  multi_robot_handoff_controller × 2
]
Should I proceed?
USER: Yes
```

The agent invokes `compose_and_execute(composition_list)` directly,
bypassing the 109-template match.

### 6.3 New tool `compose_scene`

```python
async def compose_scene(
    composition: list[dict],          # [{primitive, args}, ...]
    dry_run: bool = False,
    save_as_template: str | None = None,  # write to /workspace/templates/
) -> dict:
    """Compose a scene from primitives. If dry_run, return generated
    code without executing. If save_as_template, persist the
    composition as a new CP template for future reuse."""
```

This is the LLM-facing entry point for free composition.

---

## 7. Tool registry additions

| Tool | Purpose |
|---|---|
| `compose_scene` | Execute composition list |
| `list_primitives` | List available primitives + categories |
| `describe_primitive` | Show params, dependencies, examples |
| `propose_composition` | LLM helper: given intent, suggest primitive list |
| `save_composition_as_template` | Persist composition as new CP |
| `lint_composition` | Pre-flight validation (deps, conflicts) |

---

## 8. State machine & error handling

### 8.1 Composition resolution states

```
COMPOSITION_DECLARED
  ↓ validate primitives exist
COMPOSITION_VALIDATED
  ↓ resolve dependencies + topo-sort
COMPOSITION_ORDERED
  ↓ allocate prim paths (collisions auto-suffixed)
COMPOSITION_ALLOCATED
  ↓ expand code_templates
COMPOSITION_GENERATED
  ↓ pass to execute_template_canonical (unchanged downstream)
EXECUTING
```

### 8.2 Failure modes

| Mode | When | Response |
|---|---|---|
| F1: missing primitive | unknown primitive_id | reject + suggest closest match |
| F2: dep cycle | A requires B, B requires A | reject |
| F3: missing dep | franka_at without table_workcell | auto-prepend or warn |
| F4: param type mismatch | string when float expected | reject + show schema |
| F5: prim path collision unsuffixable | 3 robots all bound to primary_robot | escalate to ratifier |
| F6: code expansion error | template has unfilled placeholder | reject (caught at lint) |

---

## 9. Test plan

### 9.1 L0 unit (~50 tests)
- Primitive loading (file format, schema validation)
- Dependency resolution (topo-sort correctness, cycle detection)
- Path allocator (collision handling)
- Code template expansion (placeholder substitution)
- Verify_args / settle_state merging

### 9.2 L1 equivalence (per migrated CP)
- `tests/test_composition_equivalence.py` parametrized over all
  migrated CPs
- For each: assert composed code generates identical tool-call sequence
  as legacy `code` field
- Same pattern as `test_role_template_equivalence.py`

### 9.3 L2 integration (live Kit, opt-in)
- Compose CP-01 from primitives, execute, verify same end-state as
  legacy CP-01
- Compose a NOVEL CP not in 109-set, verify it builds + runs
- Drag-drop canvas → LayoutSpec → primitives → execute round-trip

---

## 10. Performance SLAs

| Operation | p50 | p95 |
|---|---|---|
| Primitive load (cached) | 1ms | 5ms |
| Composition resolution | 10ms | 50ms |
| Code generation | 5ms | 20ms |
| Total overhead vs atomic | <50ms p95 | acceptable |

Engine adds ~20-50ms per composition call (vs zero for atomic loading
JSON). Negligible vs ~70s per CP execution.

---

## 11. Phased roll-out

### M1 — Foundation (1-2 sessions)
- Primitive file format + loader
- Composition engine core (resolve, allocate, expand)
- 5 foundation primitives (dome_light, ground, table_workcell,
  cell_xform, physics_scene_cpu)
- `compose_scene` tool + tests

### M2 — Core primitives (2-3 sessions)
- 15 most-used primitives (franka_at, conveyor_with_cubes, bin_at,
  proximity_sensor_at, pick_place_controller, materials, etc.)
- Equivalence test against CP-01

### M3 — CP-01..CP-05 migrated (1 session)
- 5 migrations with equivalence test green
- Validates the approach end-to-end

### M4 — Easy CP batch (3-5 sessions)
- ~40 simpler pick-place / sort CPs migrated
- Volume scaling — measure migration time per CP

### M5 — Complex CP batch (3-5 sessions)
- ~30 rotary, dispenser, multi-robot, color-routing CPs
- May reveal need for new primitives (record + add)

### M6 — Edge cases (2-3 sessions)
- Yrkesroll + remaining special cases
- 100% migration coverage

### M7 — Cleanup (1 session)
- Remove `code` field from migrated templates
- Update docs, tooling that reads templates

### M8 — Canvas integration (2-3 sessions)
- LayoutSpec.objects → primitive resolver
- Drag-drop end-to-end
- LLM `propose_composition` tool

---

## 12. Open questions

1. **Granularity of primitives.** Should `pick_place_controller` be
   one primitive or split into `setup_controller` + `register_belt`
   + `register_cubes`? Default: keep as one; split later if needed.
2. **Parameter inheritance across primitives.** When `table_workcell`
   has `size`, should `franka_at`'s default `pos.z` derive from it?
   Default: no implicit inheritance v1; primitives declare what they
   expect (`consumes.expects_table_at_z`) but don't auto-pull values.
3. **Versioning + breaking changes.** When `franka_at` v2.0 changes
   default orientation, do templates pin v1 or auto-upgrade? Default:
   pin per template via `version` field in primitive call.
4. **Composition as DAG vs tree.** Some primitives might want to be
   parameterized BY OTHER primitives ("franka_at uses table_workcell's
   surface_z"). Default: flat list v1; DAG support later.
5. **LLM proposes new primitives.** When agent wants a primitive that
   doesn't exist (`flip_wall_curved`), do we auto-create or refuse?
   Default: refuse with suggestion; primitives are author-controlled
   library, not LLM-emitted.
6. **Yrkesroll Nucleus-asset primitives.** Each yrkesroll template
   pulls specific Nucleus USDs. Is each USD reference a primitive?
   Default: one `asset_reference` primitive parameterized by path;
   per-yrkesroll details captured in args.

---

## 13. Implementation checklist

### Primitive library
- [ ] `workspace/primitives/` directory + schema
- [ ] `service/.../composition/primitive_loader.py`
- [ ] 35 primitive JSON files per §3.2 table
- [ ] `tests/test_primitive_loader.py` (≥20 L0)
- [ ] `tests/test_primitive_schema_validation.py` (≥15 L0)

### Composition engine
- [ ] `service/.../composition/engine.py` (resolve, allocate, expand)
- [ ] `service/.../composition/path_allocator.py`
- [ ] `service/.../composition/dependency_resolver.py`
- [ ] `service/.../composition/role_binding.py`
- [ ] `tests/test_composition_engine.py` (≥30 L0)
- [ ] `tests/test_path_allocator.py` (≥15 L0)

### Tool API
- [ ] `service/.../chat/tools/composition_handlers.py`
- [ ] `compose_scene` + `list_primitives` + `describe_primitive` +
      `propose_composition` + `save_composition_as_template` +
      `lint_composition`
- [ ] `tests/test_composition_handlers.py` (≥20 L0)

### Migration
- [ ] CP-01..CP-05 composition field added (M3)
- [ ] `tests/test_composition_equivalence.py` parametrized
- [ ] Migration script `scripts/migrate_to_composition.py`
      (semi-automated: identifies easy patterns, prompts for review)

### Canvas integration
- [ ] `service/.../composition/canvas_resolver.py`
- [ ] LayoutSpec.objects → primitive list
- [ ] `tests/test_canvas_composition.py` (≥10 L0)

### Documentation
- [ ] `docs/guides/primitive_authoring.md`
- [ ] `docs/guides/composition_examples.md`
- [ ] `docs/architecture/composition_overview.md`
- [ ] Update master execution plan with M1-M8

---

## 14. References

- IA Full Spec Phase 20 (role-based templates) — composition's
  prerequisite step
- Contact-Rich Manipulation Spec (controller_stack field) — primitives
  for compliance/policy layers
- Stack-Evaluation Spec (compatibility matrix) — primitives also
  benefit from compatibility validation
- Multimodal Foundation Spec (LayoutSpec.objects) — the IR that
  composition consumes from Canvas
- This repo's prior specs above + Kit Supervisor Spec for unattended
  migration verification runs

---

## 15. Why this is a separate spec

The Contact-Rich, Stack-Evaluation, and Kit-Supervisor specs operate
on the EXECUTION layer (how stacks run). This spec operates on the
TEMPLATE-AUTHORING layer (how templates are constructed). They're
orthogonal:

- Without composition: you can still use Contact-Rich's stack variants
  on the existing 109 atomic templates
- Without Contact-Rich: composition still removes duplication

Both should land for full benefit, but each is independently valuable.

---

## 16. Anti-overengineering safeguards

- **Migration is gated on equivalence tests.** Every migrated CP must
  produce identical tool-call sequence. No "trust me it's the same."
- **Composition produces the same `code` field shape.** Existing
  verifier, supervisor, function-gate all work unchanged.
- **Primitives are author-controlled.** No LLM-emitted primitives v1.
  This avoids invisible drift in the primitive vocabulary.
- **Pin versions per template.** Breaking a primitive doesn't silently
  break all templates that use it.
- **Backwards compat during migration.** Templates can have both
  `code` and `composition` fields until M7 cleanup.
- **No premature primitive splits.** Start with `pick_place_controller`
  as one primitive; split only when an empirical need surfaces.

---

## 17. Annual cost analysis

| Activity | Frequency | Effort |
|---|---|---|
| Author new primitive | ~5 / year | ~2 hours each |
| Migrate 10 CPs (M4-M6 batches) | ~10 batches | ~1 day each |
| Update primitive for upstream change | ~5 / year | ~2 hours each |
| Verify composition equivalence in CI | every commit | ~5s |

Total: ~3-4 weeks of focused work to migrate all 109 CPs. Subsequent
years: ~1 week/year maintenance + new-primitive authoring.

Compare with current cost of duplicating bug fixes: each new pattern
(e.g., the Phase 80b grip_safe_mode rollout) currently costs 1-2 days
to apply to all relevant templates. With composition: ~30 minutes.

Break-even: ~5-10 cross-template patch events. We've done that many
in the last week alone.
