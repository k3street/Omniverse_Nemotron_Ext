# Phase 8D — Robot Setup Suite: Critique

**Agent:** Research 8D Robot Setup  
**Date:** 2026-04-15  
**Status:** Complete

## Summary Scorecard

| Item | Extension? | Python API? | Spec Correct? |
|------|:-:|:-:|---|
| 8D.1 robot_wizard | Yes (Beta) | **No** | Wrong — GUI only |
| 8D.2 gain_tuner | Yes | **No** | Wrong — auto_step/auto_trajectory don't exist |
| 8D.3 assembler | Yes (Alpha) | **Yes** | Partially — fixed joints only, no revolute/prismatic |
| 8D.4 self_collision | **No** | **No** | Wrong — requires raw USD Physics |
| 8D.5 urdf migration | Yes | Yes | Mostly right — gains type change is breaking |

## Key Details

- **robot_wizard** is Beta-tagged, "not fully functional for all use cases"
- **gain_tuner** shows plots for *visualization* but does not auto-write optimized kp/kd
- **assembler** only supports fixed joints. Uses physically simulated fixed joint bridge, not kinematic rigid connection
- **self_collision** "auto mode" is a no-op — adjacent links already don't collide by default
- **Existing code** at `tool_executor.py:699` uses legacy `from omni.isaac.urdf import _urdf` — crashes on Isaac Sim 4.5+

## Sources
- [Robot Wizard Beta — Isaac Sim 6.0](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/robot_setup/robot_wizard.html)
- [Gain Tuner](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/robot_setup/ext_isaacsim_robot_setup_gain_tuner.html)
- [Robot Assembler](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/robot_setup/assemble_robots.html)
