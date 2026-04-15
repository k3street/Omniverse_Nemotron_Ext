# Research Synthesis — After Two Rounds

**Date:** 2026-04-15  
**Method:** 25 critique agents (rev1) + 12 counter-argument agents (rev2)

## Confirmed Issues (both rounds agree)

### Code Bugs — Must Fix
- **7A RL:** 5 structural bugs in `_generate_isaaclab_env_code` (ObservationGroupCfg, actions, rewards, @configclass, gym.register). `isaaclab.train` module doesn't exist.
- **8B RMPflow:** Code calls `get_next_articulation_action()` once — needs per-physics-step callback with convergence check.
- **7B SDG:** Wrong GitHub link (OmniIsaacGymEnvs archived). TFRecord not natively supported. KITTI writer has zeroed fields.
- **8F ROS2:** `isaacsim.ros2.urdf` is an importer, not exporter. Spec has direction backwards.

### API Reality — Spec Overclaims
These tools are GUI-only with no Python API (confirmed by both rounds):
- `camera_inspector`, `gain_tuner`, `robot_wizard`, `merge_mesh`, `xrdf_editor`
- `debug_draw` only has points/lines/lines_spline (no spheres/arrows/boxes/text)
- `grasp_editor` — no programmatic driving, only file loading

### Isaac Sim 6.0 Compatibility
- `omni.isaac.sensor`, `omni.isaac.urdf` — blocking, must migrate
- `isaacsim.ros2.bridge.*` → split to `isaacsim.ros2.nodes.*` in 6.0
- 6.0 extension.py is a non-functional stub

## Corrected by Rev2 (Rev1 was wrong or overstated)

### 6A Scene Builder
- Rev1: "LLMs can't generate coordinates, need constraint solver"
- **Correction:** DirectLayout (2025) and SceneSmith (2026) show LLMs CAN generate coordinates with chain-of-thought prompting. The fix is prompt design + validate/auto-fix, not a full solver.

### Security
- Rev1: 3 CRITICAL vulnerabilities
- **Correction:** Local dev tool threat model. exec() is Kit's architecture, not a bug. Jupyter/Ollama/ComfyUI don't auth either. Real fixes: bind to 127.0.0.1, mask API keys in get_settings, fix path traversal.

### GPU Memory
- Rev1: "35B LLM + Isaac Sim = OOM on all consumer GPUs"
- **Correction:** RTX 5090 (32 GB) fits. RTX 4090 fits with 27b model. Tiered defaults are the answer, not "switch to 7B."

### 6B Image-to-3D
- Rev1: "Can't coexist on same GPU, API-first default"
- **Correction:** TripoSR FP16 (~4 GB) coexists with Isaac Sim on RTX 4090. Sequential execution viable for heavier models. Local-first default is better for air-gapped labs.

### 8C Cortex
- Rev1: "Standalone-only, incompatible with Kit extensions"
- **Correction:** Tutorial 7 shows Kit extension pattern. CortexWorld hooks into Kit's physics callback. But behaviors ARE demos (not factory), and grasp/gripper limitations are real.

### 7F ZMQ
- Rev1: "Drop entirely, redundant with ROS2"
- **Correction:** <30% of users need ROS2. ZMQ C++ node has real throughput advantage. Keep but scope to one tool wrapping OgnIsaacBridgeZMQNode.

## Rev1 Findings That Stand (Rev2 confirmed or didn't challenge)

- 7D Arena: compile-time composition, heterogeneous robots unsupported
- 7E Eureka: wrong repo URL, greedy-only algorithm, hours per run
- 7G GR00T: 24 GB+ VRAM for inference, needs cloud compute
- 7H Cloud: OVH unsupported, A100/H100 no RT cores, security risk real
- 8E Wheeled: no built-in A*/RRT, conveyors CPU-only physics
- 8D Robot Setup: 4/5 tools GUI-only (confirmed)
- 8A Quick Wins: API corrections all confirmed
- Cross: 12 tools missing from context_distiller TOOL_CATEGORIES
- Cross: 43 parameters without descriptions
- Cross: dependency analysis and critical path accurate
- Cross: test gaps (validate_scene_blueprint, create_isaaclab_env = zero tests)

## Overall Verdict

**"Targeted repair, not rewrite."** The spec's architecture and vision are sound. The problems are:
1. Implementation bugs in existing code (7A, 8B)
2. API overclaims for GUI-only extensions (8D, 8A)
3. Outdated model references (6B, 7E)
4. Missing tool registrations in context_distiller (12 tools)
5. Undescribed parameters (43 params)
6. Real but proportionate security fixes (bind to localhost, mask keys)
