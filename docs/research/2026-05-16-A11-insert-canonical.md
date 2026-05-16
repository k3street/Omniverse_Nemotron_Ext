# A11 Insert Canonical — Decision Document
**Date:** 2026-05-16  
**Agent:** A11  
**Template:** `workspace/templates/CP-NEW-peg-bushing-impedance.json`

---

## §1 Candidate + Insert-Pattern Reasoning

**Backlog entry:** `yrkesroll-assembler-peg-bushing-001`  
**Category:** yrkesroll (ONET SOC 51-2028 Assembler)  
**Backlog pattern_hint:** `compliance` — but the semantic is clearly "fit part into receptacle with force/precision", which maps to `insert`.

**Why insert:** Franka must pick a cylindrical peg and seat it into a bushing against a positional tolerance of 3 mm, with a hard force cap of 20 N. The success criterion is `peg seated in bushing` (assembly constraint active) — identical to the `insert` enum definition in `canonical_schema.py`. The `compliance` tag in the backlog describes the *control mechanism*, not the *task pattern*; the schema correctly separates these (pattern_hint captures topology, motion_controllers captures the controller stack).

---

## §2 Tools Schemas Pre-Lookup'd

All verified from `service/isaac_assist_service/chat/tools/handlers/_models.py`:

| Tool | Key required fields | Key optional fields used |
|---|---|---|
| `setup_assembly_constraint` | `peg_path`, `hole_path` | `tolerance`, `constraint_path` |
| `validate_assembly_constraint` | `name`, `type`, `target_a`, `target_b` | `tolerance_m`, `params` |
| `add_force_torque_sensor` | `sensor_path`, `parent_path` | `threshold`, `noise_std` |
| `setup_impedance_controller` | `robot_path` | `Kx`, `Kr`, `Dx`, `Dr`, `null_space_stiffness`, `torque_mode`, `dry_run` |
| `setup_pick_place_controller` | `robot_path`, `target_source` | `source_paths`, `destination_path`, `sensor_path`, `drop_target`, `planning_obstacles`, `approach_height`, `lift_height` |
| `add_proximity_sensor` | `sensor_path`, `position` | `size` |

All models use `extra='allow'` — no spurious kwarg ERRORs expected.

---

## §3 Distinction from CP-58 and CP-NEW-tactile-insertion

**CP-58 (peg-in-hole array):**
- 4 pegs, 4 holes, conveyor transport, cuRobo-only motion, no compliance layer
- `drop_targets` dict for 4 independent drop positions
- FT threshold=5.0 N (sensor presence, not force-cap semantics)
- Holes are Xform markers (no fixture solid)

**CP-NEW-tactile-insertion:**
- Single peg, no conveyor, TacEx (research sensor) dependency with fallback to FT
- Impedance not used; compliance is implicit "slow approach" only
- `validate_assembly_constraint` not called
- Known gate failure: PhysX velocity blow-up (verified_status: stable_fail)

**This canonical (CP-NEW-peg-bushing-impedance):**
- Single peg into a bushing fixture (solid Cylinder, not Xform or HolePanel)
- `setup_impedance_controller` with explicit Kx/Kr tuning and Z-softening for the descent phase
- `validate_assembly_constraint` pre-flight (Phase 72 tool) — new in this canonical
- FT threshold=20.0 N as a hard force cap matching ONET spec
- No TacEx dependency — purely FT + impedance
- Structural tags: `isaac:topology.insertion`, `isaac:assembly.force_gated`, `isaac:compliance.impedance`, `isaac:assembly.constraint_validated`

---

## §4 Form-Gates Pass

```
python scripts/lint_canonical_templates.py \
    workspace/templates/CP-NEW-peg-bushing-impedance.json 2>&1 | tail -1
# → 1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO

python scripts/lint_canonical_templates.py --validate-tool-calls \
    workspace/templates/CP-NEW-peg-bushing-impedance.json 2>&1 | tail -1
# → 1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

One fix applied before gates passed: `motion_controllers.failed` must be a `dict` (not `list`), changed `[]` → `{}`.

**Backlog updated:** `yrkesroll-assembler-peg-bushing-001` status `queued` → `drafted`.
