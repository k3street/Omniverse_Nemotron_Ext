# Research Reports — Isaac Assist PLAN.md Review

**Generated:** 2026-04-15  
**Method:** 25 parallel Sonnet research agents with web search, each reviewing a specific phase or cross-cutting concern against PLAN.md

## Per-Phase Reports

| Report | Phase | Key Finding |
|--------|-------|-------------|
| [6A_scene_blueprint.md](6A_scene_blueprint.md) | 6A Scene Builder | LLMs cannot generate 3D coordinates; needs constraint solver |
| [6B_image_to_usd.md](6B_image_to_usd.md) | 6B Image-to-USD | Model lineup outdated; GPU coexistence impossible on consumer GPUs |
| [7A_isaaclab_rl.md](7A_isaaclab_rl.md) | 7A IsaacLab RL | 6 critical code bugs in existing implementation |
| [7B_replicator_sdg.md](7B_replicator_sdg.md) | 7B Replicator SDG | Wrong GitHub link; TFRecord not supported; KITTI writer broken |
| [7C_xr_teleoperation.md](7C_xr_teleoperation.md) | 7C XR Teleop | LiveKit wrong for control; no safety stops; USD TimeSamples wrong format |
| [7D_arena.md](7D_arena.md) | 7D Arena | Compile-time composition, not runtime; heterogeneous robots unsupported |
| [7E_eureka_rewards.md](7E_eureka_rewards.md) | 7E Eureka | Wrong repo; greedy-only algorithm; hours of GPU per run |
| [7F_zmq_bridge.md](7F_zmq_bridge.md) | 7F ZMQ Bridge | Redundant with ROS2; recommend drop or minimize |
| [7G_groot_n1.md](7G_groot_n1.md) | 7G GR00T N1 | Open model but 24 GB+ VRAM; needs cloud compute |
| [7H_cloud_deployment.md](7H_cloud_deployment.md) | 7H Cloud Deploy | OVH unsupported; A100/H100 no RT cores; security risk |
| [8A_quick_wins.md](8A_quick_wins.md) | 8A Quick Wins | Debug Draw limited to points/lines; Camera Inspector GUI-only |
| [8B_motion_planning.md](8B_motion_planning.md) | 8B Motion Planning | RMPflow code calls once instead of per-step; cuMotion missing |
| [8C_cortex_behaviors.md](8C_cortex_behaviors.md) | 8C Cortex | Standalone-only; behaviors are demos not library; 3-5x effort |
| [8D_robot_setup.md](8D_robot_setup.md) | 8D Robot Setup | 4/5 tools are GUI-only with no Python API |
| [8E_wheeled_robots.md](8E_wheeled_robots.md) | 8E Wheeled Robots | No built-in A*/RRT; conveyors CPU-only physics |
| [8F_ros2_deep.md](8F_ros2_deep.md) | 8F ROS2 Deep | URDF extension is importer not exporter (backwards) |

## Cross-Cutting Reports

| Report | Topic | Key Finding |
|--------|-------|-------------|
| [cross_security.md](cross_security.md) | Security | 3 CRITICAL: unauth exec(), zero auth FastAPI, AUTO_APPROVE bypass |
| [cross_performance.md](cross_performance.md) | Performance | Default 35B LLM + Isaac Sim = OOM; Kit RPC 8s timeout kills ops |
| [cross_ux_consistency.md](cross_ux_consistency.md) | UX | 3 different approval UIs; magic prefix routing; parameter naming chaos |
| [cross_dependencies.md](cross_dependencies.md) | Dependencies | Critical path: 0→1A→3→7A→7G; 3 circular deps found |
| [cross_testing.md](cross_testing.md) | Testing | validate_scene_blueprint and create_isaaclab_env have zero tests |
| [cross_llm_tool_design.md](cross_llm_tool_design.md) | Tool Design | 12 tools invisible to LLM; 43 params undescribed; distiller is great |
| [cross_competitive.md](cross_competitive.md) | Competition | Unique position; NVIDIA window 18-36 months |
| [cross_api_compatibility.md](cross_api_compatibility.md) | API Compat | 8 blocking issues for Isaac Sim 6.0 |
