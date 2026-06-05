# Isaac Sim Extensions & Third-Party Tools Survey

**Date:** 2026-05-10
**Context:** Identifying ecosystem add-ons that could enrich Isaac Assist (`Omniverse_Nemotron_Ext`) — currently 109 canonical templates, ~400 tool handlers, MCP server, Phase 6 industrial bridges shipped.
**Goal:** Map functional gaps to existing third-party assets, score by integration cost vs. canonical-template payoff.

---

## 1. The discovery surface

The Isaac Sim ecosystem has consolidated dramatically since Isaac Sim 4.5 → 5.1. Three places concentrate signal:

- **`github.com/isaac-sim/*`** — official NVIDIA repos (~30 active). Beyond `IsaacLab`, key ones: `IsaacSimZMQ`, `IsaacLabEureka`, `IsaacLabExtensionTemplate`, `OmniIsaacGymEnvs`, `isaacsim-app-template`.
- **`sjtuyinjie/awesome-isaac-sim`** — community curated list (still maintained 2026); good index of robot-specific labs (Unitree, SO-ARM, LIMO) and surgical/dual-arm forks.
- **`NVIDIA-ISAAC-ROS/*`** — Isaac ROS GEMs. FoundationPose, cuMotion-MoveIt, Nav2 GEMs sit here. Distinct from Isaac Sim extensions but increasingly the production deployment surface.

Many community extensions still target Isaac Sim 4.5 / Isaac Lab 2.x — verify Isaac Sim 5.1 / Newton compatibility before wrapping. ORBIT-Surgical confirmed broken on 5.1 as of issue #4353.

---

## 2. High-value addons by capability bucket

### 2.1 Foundation models / VLA

**Isaac GR00T N1.7** — `github.com/NVIDIA/Isaac-GR00T`
Open VLA foundation model. We already wrap evaluate_groot + finetune_groot, so the gap is N1.7 features: 20K hours EgoScale video pretraining, improved language-following, multimodal text+image input. Effort: **small** (model swap inside existing handlers). Pairs with: yrkesroll dialog tasks (T4 tier), humanoid canonicals.

**OpenVLA + SimplerEnv-OpenVLA** — `github.com/openvla/openvla`, `DelinQu/SimplerEnv-OpenVLA`
7B open VLA, outperforms RT-2-X by 16.5%. Already used in real-to-sim trajectory replay against Franka + Robotiq. Effort: **medium** (new handler `evaluate_openvla` + Llama 2 + SigLIP/DINOv2 deps). Pairs with: pick-place canonicals, drawer-open. Differential value over GR00T: open weights, smaller, better for ablation experiments.

**Cosmos Transfer 2.5** — built into Isaac Sim 6.0 / `nvidia-cosmos.github.io/cosmos-cookbook`
World foundation model, photorealistic sim2real video augmentation (RGB+depth+segmentation → photoreal video). `CosmosWriter` Replicator writer is the integration point. Effort: **small** if we add `configure_cosmos_writer` next to `configure_coco_yolo_writer`. Pairs with: every vision canonical — direct lever on sim-to-real gap measurement.

### 2.2 Reward iteration / RL pipeline

**IsaacLabEureka** — `github.com/isaac-sim/IsaacLabEureka`
Official port of NVlabs Eureka to Isaac Lab. GPT-4-class LLM evolves reward functions; +52% normalized improvement on 29 Isaac Gym envs, beats human experts on 83%. Supports OpenAI + Azure OpenAI. Effort: **medium** to wrap as `eureka_iterate_reward` handler that ties into our existing `evaluate_reward`/`iterate_reward` stubs. Pairs with: every RL canonical, particularly Phase 5 humanoid + Phase 4 manipulation training.

### 2.3 Grasp generation

**AnyGrasp SDK** — `github.com/graspnet/anygrasp_sdk`
Full-DoF dense grasp pose detection, robust against depth noise. Active integration challenges with Isaac Sim per issue #73 (translating grasp results into PhysX gripper actions). Effort: **medium** (resolves a real gap — our `define_grasp_pose` is hand-authored YAML, no learned grasp). Pairs with: `setup_grasp_pose_sampler`, peg-in-hole, cluttered-bin yrkesroll.

**DexGraspNet 2.0** — `pku-epic.github.io/DexGraspNet/`
1.32M grasps × 5,355 objects validated in Isaac Gym. Cluttered-scene generative dexterous grasping. Galbot uses it on Isaac Sim. Effort: **large** (dexterous-hand only — Allegro/Shadow, not our default Franka 2-finger). Skip until we have a dexterous canonical.

**Sim-Grasp** — `github.com/junchengli1/Sim-Grasp`
Cluttered-environment grasp policy training using Isaac Sim, two-finger focus. Closer match to our gripper inventory. Effort: **medium**. Pairs with: bin-pick yrkesroll.

**MultiGripperGrasp Toolkit 2.0** — `github.com/IRVLUTD/isaac_sim_grasping`
Native Isaac Sim simulation tools across multiple gripper morphologies. Effort: **small-medium**. Worth scanning for code we can lift directly into our handlers.

### 2.4 Pose estimation / perception

**FoundationPose** — `github.com/NVlabs/FoundationPose` + `NVIDIA-ISAAC-ROS/isaac_ros_pose_estimation`
6-DoF pose estimation+tracking on novel objects, no per-object retraining. >120 FPS on Jetson Thor. Trained on Isaac-Sim-generated synthetic data (41K Objaverse + 1K GSO). Effort: **medium** as ROS2 GEM (since we already have `setup_isaac_ros_cumotion_moveit` — same integration shape). Pairs with: every vision canonical, sim-real digital-twin yrkesroll.

### 2.5 Whole-body / humanoid

**HOVER** — `github.com/NVlabs/HOVER`
Neural whole-body controller for humanoids (multi-mode policy distillation). 1.5M params, ~50 min wallclock training, deployed on Unitree H1. Currently Isaac Lab 2.0 + Isaac Sim 4.5 — port to 5.1 needed. Effort: **medium-large** (but high payoff vs. our current `setup_whole_body_control` stub). Pairs with: our G1 humanoid canonicals, Phase 5 morning-routine yrkesroll.

### 2.6 Procedural environments / reconstruction

**NVIDIA NuRec / 3DGS** — `developer.nvidia.com/omniverse/nurec`
GA at GTC 2026; Isaac Sim 5.1 supports NeRF, 3DGS, 3DGUT for ingesting real sensor data into USD. Effort: **medium** (handler `import_nurec_scan` → digital-twin from a phone walkthrough). Pairs with: warehouse/kitchen yrkesroll, sim-to-real benchmark scenarios. Strategic: closes the "I want to test in MY warehouse" use case.

**GRADE** — Bonetto et al., 2026
Realistic + dynamic environments for robotics research, native Isaac Sim integration. Effort: **medium** if useful as scene generator. Worth a dedicated review pass.

**BlenderProc → USD pipeline**
External, but converts 3D-Front and procedural Blender scenes to USD. Effort: **medium-large** as a side-pipeline. Lower priority than NuRec for our specific use case.

**Infinigen** (Isaac Sim 4.5 doc tutorial)
Procedural natural environments — outdoor/forest/terrain. Effort: **medium**. Niche fit for industrial canonicals; valuable for mobile-robot off-pavement scenarios.

### 2.7 Communication / external coupling

**IsaacSimZMQ** — `github.com/isaac-sim/IsaacSimZMQ`
Official ZMQ extension, OmniGraph node `OgnIsaacBridgeZMQNode` streams cameras+bboxes+timing. C++ mode uses CUDA pointers (multi-sensor high-res), Python mode for prototyping. Effort: **small** — we already have `configure_zmq_stream` stub. Wiring this would replace the stub with real implementation. Pairs with: external CV-model loop, ML-in-the-loop canonicals.

**Toni-SM extensions** (`semu.robotics.ros2_bridge`, `omni.add_on.ros_control_bridge`, `semu.xr.openxr`)
Mature ROS2 + MoveIt integration, plus OpenXR for VR teleop. Effort: **small** (drop-in extensions). Toni-SM also maintains `skrl` which is already an Isaac Lab first-class RL backend. The OpenXR extension is the cheapest path to WebXR/Quest teleop without writing native VR code.

### 2.8 Sensor primitives

**v2e / ESIM event-camera simulators** — external (no native extension)
Frame-to-event conversion via pretrained ANN. Isaac Sim only outputs frames; v2e converts. Effort: **medium** (subprocess wrapper handler `simulate_event_camera`). Pairs with: high-speed pick-place, slip-detection yrkesroll. Niche but distinctive.

**Plastic-deformation extension** — `github.com/hijimasa/isaac-sim-plastic-deformation`
FEM pseudo-plastic deformation demo. Effort: **small** to wrap. Pairs with: assembly canonicals where parts dent (single-use scenarios mostly).

### 2.9 Motion / planning enhancements

**`Auromix/isaac_sim_motion_generator`** — community wrapper around cuRobo with FK/IK/motion-gen for both sim and real. Effort: **small** (reference implementation we can crib). Could simplify our cuRobo handler glue code.

cuRobo limits we already hit (Warp 1.11 upgrade done 2026-05-09): industrial deployments report ~500 collision objects approaches Jetson 8GB ceiling. Worth documenting as a known canonical-design constraint.

---

## 3. Functional gap status

| Gap (from request)                | Best-fit asset                  | Status                                    |
| --------------------------------- | ------------------------------- | ----------------------------------------- |
| Contact-GraspNet                  | AnyGrasp SDK (newer/better)     | Not integrated; medium effort             |
| GraspNet / DexGraspNet            | DexGraspNet 2.0, Sim-Grasp      | Sim-Grasp is the practical pick           |
| SDF / occupancy mapping           | Native `generate_occupancy_map` | Already present; no gap                   |
| GR00T wrapper                     | Isaac-GR00T N1.7                | Have N1.6 wrapper; bump to 1.7            |
| RT-2 / OpenVLA                    | OpenVLA + SimplerEnv-OpenVLA    | Not integrated; medium effort             |
| Procedural env generation         | NuRec (real scans), GRADE       | Not integrated; medium                    |
| Teleop backends (Foundation Pose) | FoundationPose Isaac ROS GEM    | Not integrated; medium                    |
| AnyGrasp                          | AnyGrasp SDK                    | Not integrated; medium                    |
| MimicGen                          | Native Isaac Lab 2.3 Mimic tool | Available; we should expose handlers      |
| Eureka reward iteration           | IsaacLabEureka                  | **Best ROI** for our `iterate_reward`     |
| Event cameras                     | v2e wrapper                     | Niche; medium-large                       |
| LiDAR diff / audio                | Already covered                 | Native sensors exist                      |
| PhysX accel / dynamics tuning     | Newton backend (Isaac Lab 3.0)  | Coming in 2026; track upstream            |

---

## 4. Top 5 recommendations (value/effort ranked)

1. **IsaacLabEureka** — small-medium effort, large RL canonical leverage. Direct fit with existing `iterate_reward` stub. Closes a published-reward-iteration gap that no other Isaac Assist user can DIY easily.

2. **Cosmos Transfer 2.5 + CosmosWriter** — small effort once the SDG team confirms 5.1 paths. Single biggest knob on our sim-to-real measurement story (which is a measurement track, Phase 9). Adds `configure_cosmos_writer` next to existing COCO-YOLO writer.

3. **FoundationPose Isaac ROS GEM** — medium effort, same shape as `setup_isaac_ros_cumotion_moveit`. Unlocks novel-object yrkesroll without per-object pose-model training. High payoff for kit-assembly and bin-pick canonicals.

4. **IsaacSimZMQ wired into our `configure_zmq_stream`** — small effort. The official ZMQ extension is well-maintained, has CUDA-pointer fast path. Closes a stub we already advertise. Enables external-CV-loop yrkesroll.

5. **NuRec / 3DGS digital-twin import** — medium effort, strategic. Lets users bring their own warehouse/lab as a Gaussian-splat USD. Differentiator from any other Isaac Sim wrapper; pairs well with our 109-template library because each becomes runnable in user-specific environments.

**Bumped from top 5 but still recommended next quarter:** OpenVLA wrapper (parallel evaluator next to GR00T), HOVER port (humanoid track), AnyGrasp (when we add a dexterous canonical).

---

## 5. Implementation notes

- **Newton backend** ships with Isaac Lab 3.0 — track this; many extensions targeting PhysX-only will need updating. Our deformable canonicals are the most exposed.
- **Compatibility caveat:** Several promising community repos (ORBIT-Surgical, HOVER) lag Isaac Sim 5.1. Add a `compat_status` field in any handler we write to surface "tested on 5.1" vs "ported."
- **Pattern:** ROS GEMs (FoundationPose, cuMotion-MoveIt) follow the same `setup_isaac_ros_*` shape we already use — replicate that pattern for new GEMs rather than inventing handlers.
- **Avoid:** wrapping things that duplicate native Isaac Sim 5.1 capability (e.g., Toni-SM ros2_bridge is good but Isaac Sim's official ROS2 bridge has caught up).
