# Phase 8D — Robot Setup Suite

**Status:** Not implemented  
**Depends on:** Phase 3 (import_robot)  
**Research:** `research_reports/8D_robot_setup.md`, `rev2/verify_api_claims.md`

---

## Overview

Wrap robot setup extensions as tools for import, configuration, and assembly. **Most extensions are GUI-only** — this phase has significant scope for what's achievable programmatically.

---

## Tools

### 8D.1 `robot_wizard(asset_path, config)`

**`isaacsim.robot_setup.wizard` has NO Python API** (confirmed, Beta, GUI-only).

**Realistic implementation:** A code-gen tool that chains existing APIs:
1. Import via `isaacsim.asset.importer.urdf` (programmatic API exists)
2. Apply sensible defaults based on `config.robot_type` (manipulator/mobile/humanoid):
   - Drive stiffness/damping from lookup table
   - Collision mesh approximation (convex hull for simple shapes)
   - Self-collision: adjacent links filtered by default
3. Return a summary of what was configured

**What the tool CANNOT do (requires UI):**
- Visual collision mesh editing
- Interactive joint classification
- Custom drive mode selection per joint

**Document clearly:** "For advanced robot setup, use the Robot Wizard UI in Isaac Sim: Tools > Robotics > Robot Wizard."

### 8D.2 `tune_gains(articulation_path, method, target_performance)`

**`isaacsim.robot_setup.gain_tuner` has NO auto-tuning API** (confirmed, GUI-only).

**Realistic implementation:** Custom gain tuning logic:

```python
# Step response tuning (must implement ourselves):
for joint_name in articulation.dof_names:
    # Command step input
    art.set_joint_position_targets([target], joint_indices=[i])
    # Record response over N steps
    for step in range(N):
        world.step()
        positions.append(art.get_joint_positions()[i])
    # Compute rise time, overshoot, settling time
    # Adjust kp/kd via Ziegler-Nichols or gradient descent
```

**Methods:**
- `manual` — set kp/kd directly (passthrough to `set_joint_drives`)
- `auto_step` — custom step response analysis (implemented in tool, not extension)

**`auto_trajectory` removed** — too complex for V1 without the extension's internal logic.

### 8D.3 `assemble_robot(base_path, attachment_path, mount_frame, joint_type)`

**API exists:** `isaacsim.robot_setup.assembler.RobotAssembler` (confirmed, has Python API)

**Correction:** Only **fixed joints** supported. Remove `revolute` and `prismatic` from `joint_type` enum.

```python
assembler = RobotAssembler()
assembler.begin_assembly(stage, robot_base, base_mount, robot_attach, attach_mount, namespace, variant)
assembler.assemble()
assembler.finish_assemble()
```

**Caveats:**
- Uses physically simulated fixed joint (not kinematic rigid connection)
- Potential instability if bodies displaced before playback
- `single_robot=True` recommended for teleportation stability
- "Auto-aligns mount frames via XRDF" is NOT documented — remove from spec

### 8D.4 `configure_self_collision(articulation_path, mode)`

**No dedicated extension exists.** Implement using raw USD Physics:

- `mode="auto"` — this is **default articulation behavior** (adjacent links already skip collisions). Effectively a no-op for most robots.
- `mode="enable"` — set `enabledSelfCollisions=True` on `ArticulationRootAPI`. Non-adjacent links collide.
- `mode="manual"` — apply `UsdPhysics.FilteredPairsAPI` to specific prim pairs

**Warning:** Enable self-collision ONLY after verifying collision meshes don't overlap at joint pivots — causes PhysX "explosion." Add pre-check for geometry overlap.

### 8D.5 `migrate_urdf_importer(prim_path)`

**Existing code at tool_executor.py:699 uses deprecated `from omni.isaac.urdf import _urdf`** — crashes on Isaac Sim 4.5+.

**Migration:**
| Old | New |
|-----|-----|
| `from omni.isaac.urdf import _urdf` | `from isaacsim.asset.importer.urdf import _urdf` |
| `from omni.importer.mjcf` | `from isaacsim.asset.importer.mjcf` |
| `JointDriveCfg.gains = (kp, kd)` | `JointDriveCfg.gains = PDGainsCfg(stiffness=kp, damping=kd)` |

**Note:** The gains type change is structural, not just a string replace. Code that assigns `gains=(1000, 50)` must become `gains=PDGainsCfg(stiffness=1000, damping=50)`.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| robot_wizard defaults | L0 | Per robot_type, verify sensible kp/kd defaults |
| assemble_robot codegen | L0 | compile(), verify fixed joint only |
| self_collision codegen | L0 | FilteredPairsAPI usage |
| URDF migration transform | L0 | Input old code → output new code, verify gains type |
| Step response tuning | L3 | Requires Kit + physics sim |

## Known Limitations

- robot_wizard, gain_tuner are GUI-only — programmatic tools are subset of functionality
- Assembler: fixed joints only, no revolute/prismatic
- Self-collision "auto" is essentially a no-op on most robots
- URDF migration: gains type change is breaking, not just string replace
- Gain tuning: custom implementation, not using NVIDIA's internal tuning logic
