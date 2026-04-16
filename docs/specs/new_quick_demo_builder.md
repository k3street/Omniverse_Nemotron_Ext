# New Capability — Quick Demo Builder

**Type:** New tool set
**Source:** Personas P01 (Bo — demo engineer), P03 (Sofia — robotics researcher), P08 (Alex — RL engineer)

---

## Overview

"Set up a pick-and-place demo with Franka" → system scaffolds the entire scene (robot, table, objects, physics, ground plane) in one call. Eliminates the 10+ tool calls normally required to get a working demo running.

---

## Tools

### `create_demo_scene(demo_type, robot, options)`

**Type:** CODE_GEN handler (generates a full scene setup script)

**Demo types:**
- `"pick_and_place"` — robot arm + table + objects + physics
- `"navigation"` — mobile robot + ground plane + obstacles + goal marker
- `"conveyor"` — conveyor belt + robot arm + bins + spawning objects
- `"stacking"` — robot arm + table + stackable blocks + physics
- `"inspection"` — robot arm + camera + objects on turntable

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `demo_type` | string | Yes | One of the demo types above |
| `robot` | string | No | Robot name (default: auto per demo type). E.g. 'franka', 'ur10', 'nova_carter' |
| `num_objects` | integer | No | Number of objects to place. Default: 3 |
| `ground_plane` | boolean | No | Add a ground plane. Default: true |
| `physics` | boolean | No | Enable physics on all objects. Default: true |
| `lighting` | string | No | Lighting preset: 'default', 'studio', 'warehouse'. Default: 'default' |

**Returns:**
```json
{
  "type": "code_patch",
  "code": "...",
  "description": "create_demo_scene(demo_type='pick_and_place', robot='franka')",
  "demo_type": "pick_and_place",
  "objects_placed": 3,
  "robot": "franka"
}
```

### `list_demo_types()`

**Type:** DATA handler

Returns all available demo templates with descriptions and default robot.

**Returns:**
```json
{
  "demos": [
    {"type": "pick_and_place", "description": "...", "default_robot": "franka"},
    ...
  ]
}
```

### `add_demo_objects(demo_type, prim_root, count, randomize)`

**Type:** CODE_GEN handler — add more objects to an existing demo scene.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `demo_type` | string | Yes | Demo type (determines object types) |
| `prim_root` | string | No | USD root path for objects. Default: '/World/Objects' |
| `count` | integer | No | Number of objects. Default: 3 |
| `randomize` | boolean | No | Randomize positions and colors. Default: true |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Demo scene code generation | L0 | Each demo type generates valid Python |
| Parameter handling | L0 | Custom robot, num_objects, lighting |
| List demo types | L0 | Returns all 5 demo types |
| Add demo objects | L0 | Generates valid placement code |
| Default robot per type | L0 | pick_and_place→franka, navigation→nova_carter |
