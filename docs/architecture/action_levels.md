# Action-level taxonomy (L1 / L2 / L3)

_Per Phase 18b of `specs/IA_FULL_SPEC_2026-05-10.md`._

Every entry in `service.isaac_assist_service.chat.tools.tool_schemas.ISAAC_SIM_TOOLS`
carries an `x-action-level` annotation whose value is one of `L1`, `L2`,
or `L3`. The level captures how deep the surface goes: how many
primitive operations a single tool call composes and whether the result
of that call is a single returnable artefact or a multi-phase plan with
checkpoints.

## Definitions

### L1 — atomic primitive

A tool that performs a single USD / physics / I/O operation. One call
edits one object, reads one attribute, casts one ray. No internal
composition of other tools. No workflow.

Examples (today, in `ISAAC_SIM_TOOLS`):

- `create_prim` — create a single USD prim of a given type at a path.

  Quoting the spec example:
  ```jsonc
  {
    "name": "create_prim",
    "description": "Create a single USD prim of given type at path.",
    "x-action-level": "L1",
    "parameters": { ... }
  }
  ```

- `set_attribute` — write one USD attribute value.
- `raycast` — cast one ray and return the hit.

### L2 — small composed task in one chat turn

A tool that deterministically composes ≥2 L1 operations behind a
single name, returns when the composed action is finished, and does
not create separate workflow checkpoints. The user sees one tool call
and one outcome; there is no "approve the next step" gate inside.

Examples:

- `build_scene_from_blueprint` — spawn a complete scene from a
  blueprint JSON.

  ```jsonc
  {
    "name": "build_scene_from_blueprint",
    "description": "Spawn a complete scene from a blueprint JSON.",
    "x-action-level": "L2",
    "parameters": { ... }
  }
  ```

- `setup_pick_place_controller` — wire a Franka pick-place stack
  (controller + gripper + target-grasp resolution) in one call.
- `generate_sdg_dataset` — drive a synthetic-data generation pipeline
  from a config in a single return.

### L3 — multi-phase strategic plan with checkpoints

A tool that opens a workflow with explicit checkpoints, persists state
across user turns, and expects approval / iteration between phases.
The `start_workflow` family is the canonical case: it spins up a
template that surveys, plans, builds, and validates in distinct gated
phases.

Example:

- `start_workflow` — begin a multi-phase strategic workflow with
  checkpoints.

  ```jsonc
  {
    "name": "start_workflow",
    "description": "Begin a multi-phase strategic workflow with checkpoints.",
    "x-action-level": "L3",
    "parameters": { ... }
  }
  ```

## Classification rule

Apply the rule in order and stop at the first match:

1. **L3** if the tool calls `start_workflow` internally **or** it
   coordinates ≥3 dimension shifts (scene / control / data / training
   / evaluation / ROS) in a single call.
2. **L2** if it composes ≥2 L1 calls deterministically with no
   separate workflow checkpoint and returns when the composition is
   finished.
3. **L1** otherwise — a single primitive operation.

### Boundary calls

When a tool sits on the L1/L2 or L2/L3 boundary, classify it at the
**most-conservative** level — i.e., the higher of the two. The
heavier label forces visible re-discussion at audit time when the
classification turns out wrong, instead of silently letting a
multi-phase tool fly under an "atomic" badge.

## Why this taxonomy

The 416 IA tools are flat-listed today. There is no in-code
distinction between "place a cube" and "spawn-train-eval an RL
policy." Annotating the level forces the design conversation:
*which IA tools are L3?*

The expected ratio (per Phase 18b of the spec) is roughly:

| Level | Approximate count | Shape |
|-------|-------------------|-------|
| L1 | ~370 | Atomic ops on USD / physics / sensors |
| L2 | ~30 | Composed cell builders, vision analyzers, SDG generators |
| L3 | ~3 today; ≥10 by Epoch VII | Workflow-template launchers |

That ratio is itself the diagnostic: the IA surface is right-shaped
for atomic ops and shallow for strategic ones — exactly the opposite
shape from what `start_workflow` users will reach for. Phase 33+
(workflow generalization) is the lever to grow L3, but you can't
measure progress without the annotation.

## Auditor & CI gate

`scripts/audit_tool_levels.py` walks `ISAAC_SIM_TOOLS`, counts the
levels, and writes `docs/audits/tool_levels_{date}.md`.

- `--strict` (default): exit 1 if any tool lacks a valid
  `x-action-level`. This is the CI gate once the bulk-annotation pass
  has shipped.
- `--warn`: exit 0 with a stderr warning if anything is unannotated.
  Use this before the bulk-annotation pass lands.

## Status — bulk annotation deferred

As of 2026-05-12 the auditor + this doc are in place but the actual
annotation of the 416 entries in `tool_schemas.py` is **deferred** to
a separate serial pass with full regression coverage. The schema edit
touches every tool dict and the spec also wants `tool_executor.py`'s
`tool_metadata` to surface `level` via `get_tool_info(name)` plus
`mcp_server.py`'s tool-list response to include the field — three
hot files that should not be edited in parallel with other Phase 18
work.

This means `python scripts/audit_tool_levels.py --warn` is expected
to report 416 UNANNOTATED tools today and exit 0; the same command
without `--warn` exits 1. Both behaviours flip once the bulk
annotation lands.
