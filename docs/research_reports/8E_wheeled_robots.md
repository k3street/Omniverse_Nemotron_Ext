# Phase 8E — Wheeled Robots & Conveyor Systems: Critique

**Agent:** Research 8E Wheeled Robots  
**Date:** 2026-04-15  
**Status:** Complete

## Summary

| Task | API Real? | Mechanism Correct? | Missing |
|---|:-:|:-:|---|
| 8E.1 create_wheeled_robot | Yes | Mostly | Mecanum USD attributes non-trivial |
| 8E.2 navigate_to | Partial | No — A*/RRT not built in | Full nav stack is custom |
| 8E.3 create_conveyor | Yes | Imprecise — surface velocity, not "velocity injection" | **CPU-only physics** |
| 8E.4 create_conveyor_track | UI only | N/A | No documented Python API |
| 8E.5 merge_meshes | UI only | Partially wrong | No Python API; no vertex dedup |

## Critical Issues

- **A*/RRT path planning does not exist in Isaac Sim** — navigate_to is a full custom nav stack
- **Conveyors require CPU physics** — incompatible with GPU physics (default for RL)
- **mecanum = omnidirectional** — semantic duplicate in spec
- Missing: localization, odometry, dynamic obstacles, non-holonomic planning

## Sources
- [isaacsim.robot.wheeled_robots](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/robot_simulation/mobile_robot_controllers.html)
- [Conveyor Belt](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/digital_twin/warehouse_logistics/ext_isaacsim_asset_gen_conveyor.html)
- [Conveyor GPU physics bug — IsaacLab #4561](https://github.com/isaac-sim/IsaacLab/issues/4561)
