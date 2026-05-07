# Task SORT-01 [HARD] — Color-routed sorting: form + function dual gate

**Persona:** common workflow

**Goal:** Build a color-sorting station: one Franka, one conveyor, two
bins (one red, one blue). Two 5cm cubes arrive on the conveyor — one
red, one blue. The robot picks each cube and places it in the bin
matching its color. After building, verify the pipeline is physically
executable AND that each cube actually arrives in its color-matched
bin in simulation.

**Starting state:**
- Empty stage with default light + physics + ground plane

## Success criterion — BOTH gates must pass

### Form gate — `verify_pickplace_pipeline` returns `pipeline_ok=true` with

- ≥1 robot, ≥1 conveyor, ≥2 bins, ≥2 cubes
- Each cube has a distinct visual + physics material binding
  (red cube → red OmniPBR + rubber; blue cube → blue OmniPBR + rubber)
- Each bin has a distinct visual material that visually identifies its
  intended color (red bin tinted red, blue bin tinted blue)
- **Topological bridge:** conveyor's xy bbox spans both cube initial
  positions and reaches into the robot's pick zone
- **Reach:** every cube → robot → bin combination within the robot's
  workspace radius (Franka ≤ 0.855m)
- **Controller installed:** robot has a per-color routing capable
  pick-place subscription in `builtins`
- **Cube source bridged:** each cube starts on the active conveyor

### Function gate — `simulate_traversal_check` returns `success=true` with

For EACH cube (one call per cube), at t=90s after Stop+Play:

- Cube xy is inside its color-matched bin's bbox
  (red cube → red bin; blue cube → blue bin)
- Cube z ≥ target bin floor − 0.10m
- |cube velocity| < 0.05 m/s (cube has come to rest)

## Agent discipline

If either gate fails, surface the issues honestly. Do NOT thrash-rebuild.
Do NOT silently swap cube positions to "fix" routing — the routing
should be a property of the controller / scene structure, not a
mid-run repositioning trick.

## Why this task exists

SORT-01 probes:
- **Typed-resolver for color** — does the agent resolve "red cube"
  to a structured color value and use it consistently across visual
  material, physics material, and routing destination?
- **Conditional routing logic** — the controller has TWO possible
  destinations and must dispatch based on cube identity (color).
  Today's `setup_pick_place_controller` accepts one `destination_path`
  per install; sorting by color requires either a tool extension
  (`color_routing` arg) or a `resolve_color_routing` typed-resolver
  that produces the mapping for the orchestrator to install per-color
  controllers. **See `docs/specs/2026-05-08-canonical-task-gap-analysis.md`**.
- **Multi-cube scheduling** — two cubes on the same belt; controller
  must handle them sequentially without conflating identities.

## Failure modes to watch

- Agent uses identical visual material for both cubes (no color
  discriminability)
- Agent installs ONE controller with both bins as source_paths and
  destination_path pointing at one bin (both cubes end up in the same
  bin — sort never happens)
- Agent silently calls `set_attribute` on the cube post-pick to
  teleport it to the correct bin (anti-pattern: dishonest routing)
- Agent uses run_usd_script for any of: cube creation, bin creation,
  controller install, color binding (anti-pattern, build tools exist)
- Agent skips `simulate_traversal_check` — claims done after form-gate
  passes but never tests cube actually arrives at correct bin

**Time budget:** 8 minutes wall, 6 turns max.

## Pre-session setup

```python
setup_world(physics=True, light=True, ground=True)
```

## Canonical reference

Once CP-03 is built and verified, this task spec gates the agent-eval
that tests whether an agent following retrieval guidance can execute
the same canonical pattern unaided.
