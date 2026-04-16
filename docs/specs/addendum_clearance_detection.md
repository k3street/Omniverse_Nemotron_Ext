# Clearance Detection Addendum

**Enhances:** Phase 8B (Motion Planning), Safety Compliance  
**Source:** Personas P03 (Kenji), P07 (Thomas)  
**Research:** `rev2/research_clearance_detection.md`

---

## Overview

"Warn me when the robot comes within 50mm of the fixture" — not just collision, but near-miss detection. Critical for manufacturing engineers and safety validation.

---

## Recommended Approach: `contactOffset` (Zero Extra Cost)

**Best method found:** PhysX `contactOffset` on robot link prims generates contact events BEFORE actual penetration. The `separation` field = true surface-to-surface gap.

```python
# Set 50mm clearance threshold on robot links
from pxr import PhysxSchema

for link_prim in robot_links:
    collision_api = PhysxSchema.PhysxCollisionAPI(link_prim)
    collision_api.CreateContactOffsetAttr().Set(0.050)  # 50mm
    
    # Enable contact reporting
    PhysxSchema.PhysxContactReportAPI.Apply(link_prim)
```

**Zero extra cost** — piggybacks on normal PhysX simulation step. `separation > 0` = gap before contact.

---

## Tools

### `set_clearance_monitor(articulation_path, clearance_mm, target_prims)`

**Type:** CODE_GEN handler

"Warn me when the Franka comes within 50mm of /World/Fixture"

**Implementation:**
```python
# Set contactOffset on all robot links
for link in robot.get_link_prims():
    PhysxSchema.PhysxCollisionAPI(link).CreateContactOffsetAttr().Set(clearance_mm / 1000.0)
    PhysxSchema.PhysxContactReportAPI.Apply(link)

# Subscribe to contact events
def on_contact(event):
    for pair in event.contact_pairs:
        if pair.separation > 0 and pair.separation < clearance_mm / 1000.0:
            distance_mm = pair.separation * 1000
            warn(f"CLEARANCE: {pair.actor0} within {distance_mm:.1f}mm of {pair.actor1}")

physx_interface.subscribe_contact_report_events(on_contact)
```

**Multi-tier zones (ISO 10218 pattern):**
- **Warning zone** (100mm): yellow highlight in viewport
- **Stop zone** (50mm): red highlight + optional sim pause
- **Collision** (0mm): full stop

### `visualize_clearance(articulation_path, mode)`

**Type:** Kit RPC call

**mode = "heatmap":** SDF-based continuous distance visualization

```python
# Apply SDF to fixture meshes
PhysxSchema.PhysxSDFMeshCollisionAPI.Apply(fixture_prim)

# Query SDF at robot link positions every frame
distances = sdf_shape.get_sdf_and_gradients(robot_link_positions)
# Color: green (>100mm) → yellow (50-100mm) → red (<50mm)
```

**mode = "zones":** Static trigger volumes around fixtures

```python
# Create invisible trigger prims at warning/stop distances
PhysxSchema.PhysxTriggerAPI.Apply(warning_zone_prim)
```

### `check_path_clearance(trajectory, obstacles)`

**Type:** DATA handler (pre-flight, before motion execution)

Check minimum clearance along a planned trajectory before executing:

```python
# For each waypoint in trajectory:
for q in trajectory_waypoints:
    # FK to get link positions
    link_positions = forward_kinematics(q)
    # Query SDF distance to each obstacle
    min_distance = min(sdf.query(pos) for pos in link_positions)
    if min_distance < clearance_threshold:
        flag(f"Waypoint {i}: minimum clearance {min_distance*1000:.1f}mm < {clearance_threshold*1000:.0f}mm")
```

**Integration with Phase 8B:** Run before `execute_trajectory`. If clearance violated → warn + suggest adjusted path.

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| contactOffset setting | L0 | Correct attribute value in generated code |
| Multi-tier zone config | L0 | Warning/stop thresholds → correct contactOffset values |
| Path clearance check | L0 | Known trajectory + obstacle → correct minimum distance |
| Contact event handling | L3 | Requires Kit + PhysX simulation |
| SDF visualization | L3 | Requires Kit + GPU |
