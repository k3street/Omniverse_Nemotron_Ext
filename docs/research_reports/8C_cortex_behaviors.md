# Phase 8C — Cortex Behaviors & Manipulation: Critique

**Agent:** Research 8C Cortex Behaviors  
**Date:** 2026-04-15  
**Status:** Complete

## Summary: 3–5x More Work Than Spec Implies

## Cortex — Standalone-Only Problem

Cortex works only with standalone Python workflow. `CortexWorld` owns the simulation step, conflicting with the Kit extension + Kit RPC architecture.

## Behavior Types Are Demos, Not a Library

`isaacsim.cortex.behaviors` contains robot-specific, scene-specific demo scripts. No `create_behavior(type="pick_and_place")` factory exists.

## Gripper Issues

- **No auto-detection** of gripper joints — `ParallelGripper` requires explicit joint names
- **SurfaceGripper** is OmniGraph node-based, different architecture from `ParallelGripper`
- **Magnetic gripper** does not exist in Isaac Sim

## Grasp Editor

- **GUI-only** — no programmatic API to drive it
- "Auto-computed approach vectors" is fiction — requires ML pipeline (FoundationPose/GraspNet)
- `.isaac_grasp` files must be manually authored per object

## NL → Behavior Tree (8C.5)

Active research, not a product feature. Reading the tree as text = feasible. NL modification = research project.

## Recommended Rescoping

1. 8C.1: code generation pattern, not API wrapper
2. 8C.2: drop auto-detection and magnetic
3. 8C.3: pre-authored `.isaac_grasp` files only
4. 8C.4: generate YAML template, not viewport authoring
5. 8C.5: read-only visualization only

## Sources
- [Isaac Cortex Overview](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/cortex_tutorials/tutorial_cortex_1_overview.html)
- [isaacsim.robot.manipulators](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.robot.manipulators/docs/index.html)
- [Grasp Editor](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/robot_setup/grasp_editor.html)
