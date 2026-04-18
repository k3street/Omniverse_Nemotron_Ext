# Phase 8B — Motion Planning (RMPflow & Lula): Critique

**Agent:** Research 8B Motion Planning  
**Date:** 2026-04-15  
**Status:** Complete

## Critical: RMPflow Code Is Architecturally Broken

Generated code calls `get_next_articulation_action()` once. RMPflow must be called **every physics step** until convergence. Current code moves the arm one tiny step and stops.

## API Corrections

- **`lula_cspace` planner does not exist** — C-space trajectory is a post-processing step, not a peer planner
- **`load_supported_motion_gen_config`** — actual name is `load_supported_motion_policy_config()`
- **Lula RRT does not support orientation targets** — spec claims `target_orientation` input
- **Partial pose targets not supported** — both position and orientation must be provided
- **`rmpflow.update_world()` missing** — critical for dynamic obstacle avoidance

## cuMotion/cuRobo — The Missing Planner

NVIDIA explicitly recommends cuMotion for new development. GPU-accelerated, dynamic obstacle avoidance via nvblox. Module: `isaacsim.robot_motion.curobo`. Must be added as fourth planner option.

## XRDF ≠ URDF

Both needed simultaneously. XRDF supplements URDF, doesn't replace it. `isaacsim.robot_setup.xrdf_editor` has **no headless Python API** — GUI only.

## Robot Description Generation Underestimated

For 19 pre-supported robots, configs ship with extension. For custom robots: 4 manual steps including watertight mesh requirement. Tool should check `get_supported_robot_policy_pairs()` first.

## Missing Safety Considerations

- No convergence criterion or timeout
- Physics sim must be running (silent failure if paused)
- Joint velocity limits not settable at runtime
- No singularity detection API

## Sources
- [Lula RMPflow — Isaac Sim 6.0](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/manipulators/manipulators_rmpflow.html)
- [cuRobo/cuMotion](https://docs.isaacsim.omniverse.nvidia.com/latest/manipulators/manipulators_curobo.html)
- [XRDF Format](https://nvidia-isaac-ros.github.io/concepts/manipulation/xrdf.html)
