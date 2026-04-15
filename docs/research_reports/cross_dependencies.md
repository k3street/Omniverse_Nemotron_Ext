# Cross-Cutting: Dependency Analysis

**Agent:** Dependency Analysis  
**Date:** 2026-04-15  
**Status:** Complete

## Critical Path

```
Phase 0 → 1A → 3 → 7A → 7G
```

Longest blocking chain. If any slips, GR00T/Eureka/Arena are blocked.

Secondary: `0 → 1A → 3 → 8A → 8B → 8C` (manipulation showcase).

## Circular Dependencies

1. **8C ↔ 8D** — `grasp_object` needs `grasp_editor` from 8D, both same sprint. Fix: implement 8D.4 first.
2. **7C → 8B** — Hand retargeting needs IK solver, but 8B comes after 7C. Soft dependency.
3. **6B → 8E.5** — merge_meshes referenced but scheduled 15 weeks later. Not blocking.

## Parallelization (5 independent tracks after 1A)

- Track A: 1B, 1C, 1D
- Track B: Phase 2 (debugging)
- Track C: Phase 3 (sim control)
- Track D: Phase 4B (ROS2)
- Track E: Phase 4D (scene query)

## Safe to Cut (leaf nodes)

7D (Arena), 7F (ZMQ), 7H (Cloud), 7G (GR00T), 6B (Image-to-USD)

## Hidden Shared Bottleneck

`generate_robot_description` (8B.4) — required by 8B, 8C, and 8D but not documented as shared dependency.

## External Dependency Risk

- **HIGH:** IsaacLab-Arena (7D), IsaacSimZMQ (7F), Image-to-3D models (6B)
- **MEDIUM:** Eureka, IsaacAutomator, LiveKit
- **LOW:** IsaacLab, GR00T N1
