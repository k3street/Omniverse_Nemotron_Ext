# New Capability — Quick Demo Builder

**Type:** Guided conversational workflow  
**Source:** Persona P15 (Amir, startup founder), P08 (Alex, indie maker)  
**Research:** `rev2/research_onboarding_dx.md`, `rev2/research_killer_demos.md`

---

## Overview

For users who need a convincing simulation demo in hours, not weeks. Guided conversation that produces a render-ready scene without exposing robotics primitives.

**Target:** First-time users, founders preparing investor demos, students needing lab results, marketing teams needing footage.

---

## The Conversation Flow

```
Isaac Assist: What kind of robot demo do you need?
  [Pick and place]  [Mobile navigation]  [Humanoid]  [Custom]

User: Pick and place

Isaac Assist: What robot? I have 20+ models ready:
  [Franka Panda]  [UR5e]  [Kuka iiwa]  [Custom URDF]

User: Franka Panda

Isaac Assist: What should it pick up?
  [Cubes/boxes]  [Bottles]  [Custom object]  [Describe it]

User: Medicine bottles from a tray

Isaac Assist: I'll build this:
- Franka Panda on a table
- Tray with 6 medicine bottles
- Target bin on the right
- Overhead camera for a good angle
- Pre-trained pick-and-place policy

Building... [15 seconds]

✓ Scene ready. The robot will pick bottles and place them in the bin.
  [▶ Play]  [📷 Record video]  [🔧 Adjust]
```

**Time to demo:** Under 2 minutes from first message to working sim.

---

## Implementation

### Tool: `quick_demo(demo_type, robot, objects, scene_style)`

**Type:** Orchestrator (chains existing tools)

**Internally calls:**
1. `load_scene_template(demo_type)` — Phase 6A template
2. `import_robot(robot)` — Phase 3
3. `create_prim()` / `catalog_search()` — Phase 1A/6A for objects
4. `deploy_policy(checkpoint)` — Phase 7A.4 with pre-trained policy
5. `set_viewport_camera()` — Phase 4C for good angle

**Pre-trained policies shipped with templates:**
| Demo Type | Policy | Robot | Task |
|-----------|--------|-------|------|
| pick_place | PPO (rsl_rl) | Franka | Pick cube from tray → bin |
| mobile_nav | A* + DiffDrive | Jetbot | Navigate to waypoint |
| humanoid_walk | GR00T N1.6 | G1 | Walk forward 2m |

### Tool: `record_demo_video(duration, camera, output_path)`

**Type:** Kit RPC call

Record viewport to MP4:
```python
from omni.kit.viewport.utility import capture_viewport_to_buffer
# Or use omni.kit.capture for video recording
```

**Output:** MP4 file ready for investor deck or YouTube.

### Scene Style Presets

| Style | Lighting | Background | Use |
|-------|---------|-----------|-----|
| `clean` | Studio lighting, white floor | Product demo |
| `industrial` | Overhead fluorescent, concrete | Factory pitch |
| `lab` | Bright, neutral | Academic presentation |
| `dramatic` | Rim lighting, dark bg | Marketing video |

---

## Pricing Signal (from Persona Research)

Amir would pay **$299 one-time** for a "demo package" — a done-for-you healthcare robot scene with pre-trained policy and video export. This is a product, not a feature.

**Free tier:** Templates + basic recording  
**Paid:** Custom objects, style presets, higher resolution video, watermark removal

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Conversation flow state machine | L0 | Each choice → correct next prompt |
| Template chaining | L1 | Mock all sub-tools, verify correct sequence |
| Video recording | L3 | Requires Kit + GPU |

---

## Why This Matters

The onboarding research showed: 90 seconds to first wow is gold standard. This feature IS the first wow. A new user goes from "I've never used Isaac Sim" to "I have a working robot demo video" in under 2 minutes. That's the conversion moment — and the YouTube video that drives word of mouth.
