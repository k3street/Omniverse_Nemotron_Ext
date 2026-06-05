"""R18 migration script: novel_pattern templates → pattern_hint=other + structural_tags.

Applies intent, roles, role_defaults, and code_template to each of the 14
deferred templates. Removes migration_deferred on success.

Run this script manually; it writes files in-place.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "workspace" / "templates"


def migrate(task_id: str, patch: dict) -> None:
    p = TEMPLATES / f"{task_id}.json"
    d = json.loads(p.read_text())
    d.update(patch)
    d.pop("migration_deferred", None)
    p.write_text(json.dumps(d, indent=2))
    print(f"  migrated {task_id}")


# ──────────────────────────────────────────────────────────────────────────────
# CP-48 — Vision inspect-and-reject (single robot, 4 good + 1 bad)
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-48", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 1,
            "n_handoffs": 0,
            "destination_kind": "bin",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:perception.vision_classifier",
            "isaac:robot.fixed_base.arm",
            "isaac:transport.conveyor",
            "isaac:routing.color_based_reject",
            "isaac:topology.inspect_and_reject",
            "isaac:sensor.camera",
        ],
    },
    "roles": {
        "primary_robot": {
            "constraints": ["franka_panda", "ur5e"],
            "expected_count": 1,
            "required": True,
        },
        "input_conveyor": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "good_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "reject_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "camera": {
            "constraints": ["camera"],
            "expected_count": 1,
            "required": True,
        },
        "pick_sensor": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "good_cubes": {
            "constraints": ["cube"],
            "min": 4,
            "max": 4,
            "unordered": False,
        },
        "bad_cube": {
            "constraints": ["cube"],
            "expected_count": 1,
            "required": True,
        },
    },
    "role_defaults": {
        "primary_robot": {
            "path": "/World/Franka",
            "class": "franka_panda",
            "position": [0, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "input_conveyor": {
            "path": "/World/ConveyorBelt",
            "position": [0.0, 0.4, 0.78],
            "size": [3.0, 0.4, 0.05],
            "surface_velocity": [0.2, 0, 0],
        },
        "good_bin": {
            "path": "/World/GoodBin",
            "position": [-0.3, -0.4, 0.75],
            "size": [0.25, 0.25, 0.15],
            "material_path": "/World/Materials/Green",
        },
        "reject_bin": {
            "path": "/World/RejectBin",
            "position": [0.3, -0.4, 0.75],
            "size": [0.25, 0.25, 0.15],
            "material_path": "/World/Materials/Red",
            "drop_target": [0.3, -0.4, 0.95],
        },
        "camera": {
            "path": "/World/Cam",
            "position": [0, 1.5, 1.5],
            "look_at": [0, 0, 0.8],
        },
        "pick_sensor": {
            "path": "/World/PickSensor",
            "position": [0.4, 0.4, 0.835],
            "size": [0.06, 0.06, 0.06],
        },
        "good_cubes": [
            {"path": "/World/Cube_g1", "position": [-1.0, 0.4, 0.835]},
            {"path": "/World/Cube_g2", "position": [-0.7, 0.4, 0.835]},
            {"path": "/World/Cube_g3", "position": [-0.4, 0.4, 0.835]},
            {"path": "/World/Cube_g4", "position": [-0.1, 0.4, 0.835]},
        ],
        "bad_cube": {
            "path": "/World/Cube_bad",
            "position": [0.2, 0.4, 0.835],
        },
    },
    "code_template": """\
# DomeLight + Ground + Cell + Table
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1500.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Cell", prim_type="Xform")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.0, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

robot_wizard(
    robot_name={{primary_robot.class}},
    dest_path={{primary_robot.path}},
    position={{primary_robot.position}},
    orientation={{primary_robot.orientation}},
)

create_conveyor(
    prim_path={{input_conveyor.path}},
    position={{input_conveyor.position}},
    size={{input_conveyor.size}},
    surface_velocity={{input_conveyor.surface_velocity}},
)

# Materials — green for good, red for defective
create_material(material_path="/World/Materials/Green", shader_type="OmniPBR", diffuse_color=[0.0, 1.0, 0.0])
create_material(material_path="/World/Materials/Red",   shader_type="OmniPBR", diffuse_color=[1.0, 0.0, 0.0])

# 4 green cubes (good)
{{#each good_cubes}}
create_prim(prim_path={{this.path}}, prim_type="Cube", position={{this.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{this.path}}, schema_name=api)
assign_material(prim_path={{this.path}}, material_path="/World/Materials/Green")
{{/each}}

# 1 red cube (defective)
create_prim(prim_path={{bad_cube.path}}, prim_type="Cube", position={{bad_cube.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{bad_cube.path}}, schema_name=api)
assign_material(prim_path={{bad_cube.path}}, material_path="/World/Materials/Red")

all_cubes = [{{good_cubes[0].path}}, {{good_cubes[1].path}}, {{good_cubes[2].path}}, {{good_cubes[3].path}}, {{bad_cube.path}}]
bulk_set_attribute(prim_paths=all_cubes, attr="physxRigidBody:sleepThreshold", value=0.0)
for c in all_cubes:
    apply_physics_material(prim_path=c, material_name="rubber")

# Two bins
create_bin(prim_path={{good_bin.path}},   position={{good_bin.position}}, size={{good_bin.size}})
assign_material(prim_path={{good_bin.path}}, material_path="/World/Materials/Green")
create_bin(prim_path={{reject_bin.path}}, position={{reject_bin.position}}, size={{reject_bin.size}})
assign_material(prim_path={{reject_bin.path}}, material_path="/World/Materials/Red")

# Camera + viewport
create_prim(prim_path={{camera.path}}, prim_type="Camera", position={{camera.position}})
set_camera_look_at(camera_path={{camera.path}}, target={{camera.look_at}})

add_proximity_sensor(sensor_path={{pick_sensor.path}}, position={{pick_sensor.position}}, size={{pick_sensor.size}})

# Composite vision-driven setup
setup_pick_place_with_vision(
    robot_path={{primary_robot.path}},
    cube_paths=all_cubes,
    class_labels=["green cube", "red cube"],
    camera_path={{camera.path}},
    destination_map={"green": {{good_bin.path}}, "red": {{reject_bin.path}}},
    destination_path={{reject_bin.path}},
    sensor_path={{pick_sensor.path}},
    belt_path={{input_conveyor.path}},
    drop_target={{reject_bin.drop_target}},
    planning_obstacles=["/World/Table", {{input_conveyor.path}}, {{good_bin.path}}, {{reject_bin.path}}],
    vision_precomputed={
        {{good_cubes[0].path}}: "green",
        {{good_cubes[1].path}}: "green",
        {{good_cubes[2].path}}: "green",
        {{good_cubes[3].path}}: "green",
        {{bad_cube.path}}: "red",
    },
)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-57 — Parcel singulation from heap
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-57", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 1,
            "n_handoffs": 0,
            "destination_kind": "bin",
            "uses_conveyor_transport": False,
        },
        "structural_tags": [
            "isaac:topology.heap_singulation",
            "isaac:robot.fixed_base.arm",
            "isaac:workpiece.heap_zone",
            "isaac:transport.heap_surface",
            "isaac:topology.single_station",
        ],
    },
    "roles": {
        "primary_robot": {
            "constraints": ["franka_panda", "ur5e"],
            "expected_count": 1,
            "required": True,
        },
        "heap_zone": {
            "constraints": ["heap"],
            "expected_count": 1,
            "required": True,
        },
        "heap_surface": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "output_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "pick_sensor": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
    },
    "role_defaults": {
        "primary_robot": {
            "path": "/World/Franka",
            "class": "franka_panda",
            "position": [0, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "heap_zone": {
            "path": "/World/CubeHeap",
            "center": [0.4, 0.3, 0.835],
            "radius": 0.10,
            "n_items": 5,
            "item_size": 0.05,
        },
        "heap_surface": {
            "path": "/World/HeapSurface",
            "position": [0.4, 0.3, 0.78],
            "size": [0.30, 0.30, 0.05],
            "surface_velocity": [0.001, 0, 0],
        },
        "output_bin": {
            "path": "/World/Bin",
            "position": [0, -0.4, 0.75],
            "size": [0.3, 0.3, 0.15],
        },
        "pick_sensor": {
            "path": "/World/PickSensor",
            "position": [0.4, 0.3, 0.85],
            "size": [0.10, 0.10, 0.06],
        },
    },
    "code_template": """\
# DomeLight + Ground + Cell + Table
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Cell", prim_type="Xform")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.0, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

robot_wizard(
    robot_name={{primary_robot.class}},
    dest_path={{primary_robot.path}},
    position={{primary_robot.position}},
    orientation={{primary_robot.orientation}},
)

# Heap of items
create_heap_zone(
    heap_path={{heap_zone.path}},
    center={{heap_zone.center}},
    radius={{heap_zone.radius}},
    n_items={{heap_zone.n_items}},
    item_size={{heap_zone.item_size}},
)

# Stationary work surface under heap (verifier needs bridge)
create_conveyor(
    prim_path={{heap_surface.path}},
    position={{heap_surface.position}},
    size={{heap_surface.size}},
    surface_velocity={{heap_surface.surface_velocity}},
)

bulk_set_attribute(
    prim_paths=["/World/CubeHeap/Item_1", "/World/CubeHeap/Item_2", "/World/CubeHeap/Item_3", "/World/CubeHeap/Item_4", "/World/CubeHeap/Item_5"],
    attr="physxRigidBody:sleepThreshold",
    value=0.0,
)
for path in ["/World/CubeHeap/Item_1", "/World/CubeHeap/Item_2", "/World/CubeHeap/Item_3", "/World/CubeHeap/Item_4", "/World/CubeHeap/Item_5"]:
    apply_physics_material(prim_path=path, material_name="rubber")

create_bin(prim_path={{output_bin.path}}, position={{output_bin.position}}, size={{output_bin.size}})

add_proximity_sensor(sensor_path={{pick_sensor.path}}, position={{pick_sensor.position}}, size={{pick_sensor.size}})

setup_pick_place_controller(
    robot_path={{primary_robot.path}},
    target_source="curobo",
    sensor_path={{pick_sensor.path}},
    belt_path={{heap_surface.path}},
    source_paths=["/World/CubeHeap/Item_1", "/World/CubeHeap/Item_2", "/World/CubeHeap/Item_3", "/World/CubeHeap/Item_4", "/World/CubeHeap/Item_5"],
    destination_path={{output_bin.path}},
    planning_obstacles=["/World/Table", {{heap_surface.path}}, {{output_bin.path}}],
)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-59 — Vision-gated bin-picking duo
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-59", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 2,
            "n_handoffs": 0,
            "destination_kind": "bin",
            "uses_conveyor_transport": False,
        },
        "structural_tags": [
            "isaac:perception.vision_classifier",
            "isaac:topology.dual_robot",
            "isaac:coordination.mutex",
            "isaac:workpiece.heap_zone",
            "isaac:routing.color_based_sort",
            "isaac:sensor.camera",
        ],
    },
    "roles": {
        "robot_a": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "robot_b": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "heap_surface": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "red_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "blue_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "camera": {
            "constraints": ["camera"],
            "expected_count": 1,
            "required": True,
        },
        "sensor_a": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "sensor_b": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "workpieces": {
            "constraints": ["cube"],
            "min": 4,
            "max": 4,
            "unordered": False,
        },
    },
    "role_defaults": {
        "robot_a": {
            "path": "/World/FrankaA",
            "class": "franka_panda",
            "position": [-0.5, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "robot_b": {
            "path": "/World/FrankaB",
            "class": "franka_panda",
            "position": [0.5, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "heap_surface": {
            "path": "/World/HeapSurface",
            "position": [0, 0.3, 0.78],
            "size": [0.30, 0.30, 0.05],
            "surface_velocity": [0.001, 0, 0],
        },
        "red_bin": {
            "path": "/World/RedBin",
            "position": [-0.5, -0.5, 0.75],
            "size": [0.20, 0.20, 0.15],
        },
        "blue_bin": {
            "path": "/World/BlueBin",
            "position": [0.5, -0.5, 0.75],
            "size": [0.20, 0.20, 0.15],
        },
        "camera": {
            "path": "/World/Cam",
            "position": [0, 0.3, 1.5],
            "look_at": [0, 0.3, 0.85],
        },
        "sensor_a": {
            "path": "/World/SensorA",
            "position": [-0.2, 0.3, 0.85],
            "size": [0.10, 0.20, 0.06],
        },
        "sensor_b": {
            "path": "/World/SensorB",
            "position": [0.2, 0.3, 0.85],
            "size": [0.10, 0.20, 0.06],
        },
        "workpieces": [
            {"path": "/World/Cube_r1", "position": [-0.15, 0.30, 0.86], "material": "Red"},
            {"path": "/World/Cube_b1", "position": [0.15, 0.30, 0.86], "material": "Blue"},
            {"path": "/World/Cube_r2", "position": [-0.10, 0.45, 0.86], "material": "Red"},
            {"path": "/World/Cube_b2", "position": [0.10, 0.15, 0.86], "material": "Blue"},
        ],
    },
    "code_template": """\
# DomeLight + Ground + Cell + Table
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1500.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Cell", prim_type="Xform")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.5, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

robot_wizard(
    robot_name={{robot_a.class}},
    dest_path={{robot_a.path}},
    position={{robot_a.position}},
    orientation={{robot_a.orientation}},
)
robot_wizard(
    robot_name={{robot_b.class}},
    dest_path={{robot_b.path}},
    position={{robot_b.position}},
    orientation={{robot_b.orientation}},
)

# Stationary surface under heap (verifier needs bridge)
create_conveyor(
    prim_path={{heap_surface.path}},
    position={{heap_surface.position}},
    size={{heap_surface.size}},
    surface_velocity={{heap_surface.surface_velocity}},
)

create_material(material_path="/World/Materials/Red",  shader_type="OmniPBR", diffuse_color=[1.0, 0.0, 0.0])
create_material(material_path="/World/Materials/Blue", shader_type="OmniPBR", diffuse_color=[0.0, 0.0, 1.0])

color_specs = [
    ({{workpieces[0].path}}, {{workpieces[0].position}}, {{workpieces[0].material}}),
    ({{workpieces[1].path}}, {{workpieces[1].position}}, {{workpieces[1].material}}),
    ({{workpieces[2].path}}, {{workpieces[2].position}}, {{workpieces[2].material}}),
    ({{workpieces[3].path}}, {{workpieces[3].position}}, {{workpieces[3].material}}),
]
for path, pos, mat in color_specs:
    create_prim(prim_path=path, prim_type="Cube", position=pos, size=0.05)
    for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
        apply_api_schema(prim_path=path, schema_name=api)
    assign_material(prim_path=path, material_path=f"/World/Materials/{mat}")

bulk_set_attribute(
    prim_paths=[c[0] for c in color_specs],
    attr="physxRigidBody:sleepThreshold",
    value=0.0,
)
for c in color_specs:
    apply_physics_material(prim_path=c[0], material_name="rubber")

# Bins on each side
create_bin(prim_path={{red_bin.path}},  position={{red_bin.position}}, size={{red_bin.size}})
assign_material(prim_path={{red_bin.path}}, material_path="/World/Materials/Red")
create_bin(prim_path={{blue_bin.path}}, position={{blue_bin.position}}, size={{blue_bin.size}})
assign_material(prim_path={{blue_bin.path}}, material_path="/World/Materials/Blue")

# Mutex on heap
setup_robot_claim_mutex(
    mutex_path="/World/HeapMutex",
    resource_path={{heap_surface.path}},
    robots=[{{robot_a.path}}, {{robot_b.path}}],
)

create_prim(prim_path={{camera.path}}, prim_type="Camera", position={{camera.position}})
set_camera_look_at(camera_path={{camera.path}}, target={{camera.look_at}})

add_proximity_sensor(sensor_path={{sensor_a.path}}, position={{sensor_a.position}}, size={{sensor_a.size}})
add_proximity_sensor(sensor_path={{sensor_b.path}}, position={{sensor_b.position}}, size={{sensor_b.size}})

# Robot A — vision-gated, picks red cubes only
setup_pick_place_with_vision(
    robot_path={{robot_a.path}},
    cube_paths=[c[0] for c in color_specs],
    class_labels=["red cube", "blue cube"],
    camera_path={{camera.path}},
    destination_map={"red": {{red_bin.path}}},
    destination_path={{red_bin.path}},
    sensor_path={{sensor_a.path}},
    belt_path={{heap_surface.path}},
    planning_obstacles=["/World/Table", {{heap_surface.path}}, {{red_bin.path}}, {{blue_bin.path}}],
    vision_precomputed={
        {{workpieces[0].path}}: "red",
        {{workpieces[1].path}}: "blue",
        {{workpieces[2].path}}: "red",
        {{workpieces[3].path}}: "blue",
    },
)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-60 — Recirculation loop demo (no robot)
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-60", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 0,
            "n_handoffs": 0,
            "destination_kind": "loop",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:topology.recirculation_loop",
            "isaac:transport.conveyor",
            "isaac:topology.no_robot",
            "isaac:transport.closed_loop",
        ],
    },
    "roles": {
        "loop": {
            "constraints": ["recirculation_loop"],
            "expected_count": 1,
            "required": True,
        },
        "test_cube": {
            "constraints": ["cube"],
            "expected_count": 1,
            "required": True,
        },
    },
    "role_defaults": {
        "loop": {
            "path": "/World/Loop",
            "center": [0, 0, 0.78],
            "length": 2.0,
            "width": 0.6,
            "velocity": 0.2,
        },
        "test_cube": {
            "path": "/World/Cube_1",
            "position": [0, 0.3, 0.835],
        },
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

# Recirculation loop
create_recirculation_loop(
    loop_path={{loop.path}},
    center={{loop.center}},
    length={{loop.length}},
    width={{loop.width}},
    velocity={{loop.velocity}},
)

# Test cube on Top segment
create_prim(prim_path={{test_cube.path}}, prim_type="Cube", position={{test_cube.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{test_cube.path}}, schema_name=api)
bulk_set_attribute(prim_paths=[{{test_cube.path}}], attr="physxRigidBody:sleepThreshold", value=0.0)
apply_physics_material(prim_path={{test_cube.path}}, material_name="rubber")""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-61 — Cortex-Franka block-stacking
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-61", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 1,
            "n_handoffs": 0,
            "destination_kind": "bin",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:execution.cortex_behavior_tree",
            "isaac:robot.fixed_base.arm",
            "isaac:transport.conveyor",
            "isaac:topology.single_station",
            "isaac:coordination.moving_obstacle",
        ],
    },
    "roles": {
        "primary_robot": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "input_conveyor": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "stack_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "workpieces": {
            "constraints": ["cube"],
            "min": 3,
            "max": 3,
            "unordered": False,
        },
    },
    "role_defaults": {
        "primary_robot": {
            "path": "/World/Franka",
            "class": "franka_panda",
            "position": [0, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "input_conveyor": {
            "path": "/World/ConveyorBelt",
            "position": [0.0, 0.4, 0.78],
            "size": [3.0, 0.4, 0.05],
            "surface_velocity": [0.2, 0, 0],
        },
        "stack_bin": {
            "path": "/World/StackBin",
            "position": [0, -0.4, 0.75],
            "size": [0.2, 0.2, 0.15],
        },
        "workpieces": [
            {"path": "/World/Cube_1", "position": [-1.4, 0.4, 0.835]},
            {"path": "/World/Cube_2", "position": [-1.15, 0.4, 0.835]},
            {"path": "/World/Cube_3", "position": [-0.9, 0.4, 0.835]},
        ],
    },
    "code_template": """\
# DomeLight + Ground + Cell + Table
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Cell", prim_type="Xform")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.0, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

robot_wizard(
    robot_name={{primary_robot.class}},
    dest_path={{primary_robot.path}},
    position={{primary_robot.position}},
    orientation={{primary_robot.orientation}},
)

create_conveyor(
    prim_path={{input_conveyor.path}},
    position={{input_conveyor.position}},
    size={{input_conveyor.size}},
    surface_velocity={{input_conveyor.surface_velocity}},
)

{{#each workpieces}}
create_prim(prim_path={{this.path}}, prim_type="Cube", position={{this.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{this.path}}, schema_name=api)
{{/each}}

bulk_set_attribute(prim_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}], attr="physxRigidBody:sleepThreshold", value=0.0)
{{#each workpieces}}
apply_physics_material(prim_path={{this.path}}, material_name="rubber")
{{/each}}

create_bin(prim_path={{stack_bin.path}}, position={{stack_bin.position}}, size={{stack_bin.size}})

# Cortex framework wrapper
setup_cortex_behavior(
    robot_path={{primary_robot.path}},
    robot_kind="franka",
    behavior_module="isaacsim.cortex.behaviors.franka.peck_demo",
    obstacles=["/World/Table", {{stack_bin.path}}],
)

# Register cubes as dynamic obstacles for runtime collision avoidance
{{#each workpieces}}
register_moving_obstacle(
    robot_path={{primary_robot.path}},
    obstacle_path={{this.path}},
)
{{/each}}""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-65 — Two-cell kit-tray relay
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-65", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 2,
            "n_handoffs": 1,
            "destination_kind": "bin",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:topology.kit_tray_relay",
            "isaac:topology.dual_robot",
            "isaac:coordination.handoff_signal",
            "isaac:coordination.mutex",
            "isaac:workpiece.kit_tray",
            "isaac:transport.conveyor",
        ],
    },
    "roles": {
        "robot_a": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "robot_b": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "input_conveyor": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "kit_tray": {
            "constraints": ["kit_tray"],
            "expected_count": 1,
            "required": True,
        },
        "handoff_bridge": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "output_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "sensor_a": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "sensor_b": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "workpieces": {
            "constraints": ["cube"],
            "min": 4,
            "max": 4,
            "unordered": False,
        },
    },
    "role_defaults": {
        "robot_a": {
            "path": "/World/FrankaA",
            "class": "franka_panda",
            "position": [-0.6, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "robot_b": {
            "path": "/World/FrankaB",
            "class": "franka_panda",
            "position": [0.6, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "input_conveyor": {
            "path": "/World/ConveyorBelt",
            "position": [-0.6, 0.4, 0.78],
            "size": [3.0, 0.4, 0.05],
            "surface_velocity": [0.2, 0, 0],
        },
        "kit_tray": {
            "path": "/World/KitTray",
            "position": [0, -0.3, 0.775],
            "tray_size": [0.30, 0.30, 0.05],
            "slot_layout": "grid_2x2",
            "slot_size": 0.05,
            "slot_spacing": 0.10,
        },
        "handoff_bridge": {
            "path": "/World/HandoffBridge",
            "position": [0, -0.3, 0.81],
            "size": [0.40, 0.30, 0.02],
            "surface_velocity": [0.001, 0, 0],
        },
        "output_bin": {
            "path": "/World/OutBin",
            "position": [0.6, -0.5, 0.75],
            "size": [0.3, 0.3, 0.15],
        },
        "sensor_a": {
            "path": "/World/SensorA",
            "position": [-0.2, 0.4, 0.835],
            "size": [0.06, 0.06, 0.06],
        },
        "sensor_b": {
            "path": "/World/SensorB",
            "position": [0.0, -0.3, 0.85],
            "size": [0.06, 0.06, 0.06],
        },
        "workpieces": [
            {"path": "/World/Cube_1", "position": [-1.7, 0.4, 0.835], "drop_target": [-0.05, -0.35, 0.825]},
            {"path": "/World/Cube_2", "position": [-1.4, 0.4, 0.835], "drop_target": [0.05, -0.35, 0.825]},
            {"path": "/World/Cube_3", "position": [-1.1, 0.4, 0.835], "drop_target": [-0.05, -0.25, 0.825]},
            {"path": "/World/Cube_4", "position": [-0.8, 0.4, 0.835], "drop_target": [0.05, -0.25, 0.825]},
        ],
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Cell", prim_type="Xform")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.5, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

robot_wizard(
    robot_name={{robot_a.class}},
    dest_path={{robot_a.path}},
    position={{robot_a.position}},
    orientation={{robot_a.orientation}},
)
robot_wizard(
    robot_name={{robot_b.class}},
    dest_path={{robot_b.path}},
    position={{robot_b.position}},
    orientation={{robot_b.orientation}},
)

create_conveyor(
    prim_path={{input_conveyor.path}},
    position={{input_conveyor.position}},
    size={{input_conveyor.size}},
    surface_velocity={{input_conveyor.surface_velocity}},
)

{{#each workpieces}}
create_prim(prim_path={{this.path}}, prim_type="Cube", position={{this.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{this.path}}, schema_name=api)
{{/each}}

bulk_set_attribute(prim_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}], attr="physxRigidBody:sleepThreshold", value=0.0)
{{#each workpieces}}
apply_physics_material(prim_path={{this.path}}, material_name="rubber")
{{/each}}

# Kit tray (handoff station)
create_kit_tray(
    tray_path={{kit_tray.path}},
    position={{kit_tray.position}},
    tray_size={{kit_tray.tray_size}},
    slot_layout={{kit_tray.slot_layout}},
    slot_size={{kit_tray.slot_size}},
    slot_spacing={{kit_tray.slot_spacing}},
)

# Stationary surface (verifier bridge)
create_conveyor(
    prim_path={{handoff_bridge.path}},
    position={{handoff_bridge.position}},
    size={{handoff_bridge.size}},
    surface_velocity={{handoff_bridge.surface_velocity}},
)

# Handoff signal
setup_robot_handoff_signal(
    handoff_path="/World/Handoff",
    position=[0, -0.3, 0.825],
    robot_a={{robot_a.path}},
    robot_b={{robot_b.path}},
)

# Mutex on handoff station
setup_robot_claim_mutex(
    mutex_path="/World/HandoffMutex",
    resource_path={{kit_tray.path}},
    robots=[{{robot_a.path}}, {{robot_b.path}}],
)

# Output bin
create_bin(prim_path={{output_bin.path}}, position={{output_bin.position}}, size={{output_bin.size}})

add_proximity_sensor(sensor_path={{sensor_a.path}}, position={{sensor_a.position}}, size={{sensor_a.size}})
add_proximity_sensor(sensor_path={{sensor_b.path}}, position={{sensor_b.position}}, size={{sensor_b.size}})

# Robot A — fills kit tray with cubes
setup_pick_place_controller(
    robot_path={{robot_a.path}},
    target_source="curobo",
    sensor_path={{sensor_a.path}},
    belt_path={{input_conveyor.path}},
    source_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}],
    destination_path={{kit_tray.path}},
    drop_targets={
        {{workpieces[0].path}}: {{workpieces[0].drop_target}},
        {{workpieces[1].path}}: {{workpieces[1].drop_target}},
        {{workpieces[2].path}}: {{workpieces[2].drop_target}},
        {{workpieces[3].path}}: {{workpieces[3].drop_target}},
    },
    mutex_path="/World/HandoffMutex",
    planning_obstacles=["/World/Table", {{input_conveyor.path}}, {{kit_tray.path}}, {{handoff_bridge.path}}, {{output_bin.path}}],
)

# Robot B — picks cubes from tray and places at OutBin
setup_pick_place_controller(
    robot_path={{robot_b.path}},
    target_source="curobo",
    sensor_path={{sensor_b.path}},
    belt_path={{handoff_bridge.path}},
    source_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}],
    destination_path={{output_bin.path}},
    mutex_path="/World/HandoffMutex",
    planning_obstacles=["/World/Table", {{input_conveyor.path}}, {{kit_tray.path}}, {{handoff_bridge.path}}, {{output_bin.path}}],
)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-67 — Leader/follower rotary station
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-67", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 2,
            "n_handoffs": 1,
            "destination_kind": "bin",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:topology.rotary_table",
            "isaac:topology.dual_robot",
            "isaac:coordination.mutex",
            "isaac:transport.conveyor",
            "isaac:coordination.leader_follower",
        ],
    },
    "roles": {
        "robot_a": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "robot_b": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "input_conveyor": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "rotary_table": {
            "constraints": ["rotary_table"],
            "expected_count": 1,
            "required": True,
        },
        "output_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "sensor_a": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "sensor_b": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "workpieces": {
            "constraints": ["cube"],
            "min": 4,
            "max": 4,
            "unordered": False,
        },
    },
    "role_defaults": {
        "robot_a": {
            "path": "/World/FrankaA",
            "class": "franka_panda",
            "position": [0, 0.5, 0.75],
            "orientation": [0.7071068, 0, 0, -0.7071068],
        },
        "robot_b": {
            "path": "/World/FrankaB",
            "class": "franka_panda",
            "position": [0, -0.5, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "input_conveyor": {
            "path": "/World/ConveyorBelt",
            "position": [0.0, 0.7, 0.78],
            "size": [3.0, 0.3, 0.05],
            "surface_velocity": [0.2, 0, 0],
        },
        "rotary_table": {
            "path": "/World/RotaryTable",
            "position": [0, 0, 0.81],
            "radius": 0.20,
            "height": 0.05,
            "angular_velocity_deg": 30.0,
            "drop_target_a": [0, 0.15, 0.86],
            "drop_target_b": [0, -0.7, 0.95],
        },
        "output_bin": {
            "path": "/World/OutBin",
            "position": [0, -0.7, 0.75],
            "size": [0.3, 0.3, 0.15],
        },
        "sensor_a": {
            "path": "/World/SensorA",
            "position": [0.4, 0.7, 0.835],
            "size": [0.06, 0.06, 0.06],
        },
        "sensor_b": {
            "path": "/World/SensorB",
            "position": [0, -0.2, 0.85],
            "size": [0.10, 0.06, 0.06],
        },
        "workpieces": [
            {"path": "/World/Cube_1", "position": [-1.4, 0.7, 0.835]},
            {"path": "/World/Cube_2", "position": [-1.15, 0.7, 0.835]},
            {"path": "/World/Cube_3", "position": [-0.9, 0.7, 0.835]},
            {"path": "/World/Cube_4", "position": [-0.65, 0.7, 0.835]},
        ],
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Cell", prim_type="Xform")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.5, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

robot_wizard(
    robot_name={{robot_a.class}},
    dest_path={{robot_a.path}},
    position={{robot_a.position}},
    orientation={{robot_a.orientation}},
)
robot_wizard(
    robot_name={{robot_b.class}},
    dest_path={{robot_b.path}},
    position={{robot_b.position}},
    orientation={{robot_b.orientation}},
)

# Conveyor on A's side (+Y)
create_conveyor(
    prim_path={{input_conveyor.path}},
    position={{input_conveyor.position}},
    size={{input_conveyor.size}},
    surface_velocity={{input_conveyor.surface_velocity}},
)

{{#each workpieces}}
create_prim(prim_path={{this.path}}, prim_type="Cube", position={{this.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{this.path}}, schema_name=api)
{{/each}}

bulk_set_attribute(prim_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}], attr="physxRigidBody:sleepThreshold", value=0.0)
{{#each workpieces}}
apply_physics_material(prim_path={{this.path}}, material_name="rubber")
{{/each}}

# Rotary table between robots
create_rotary_table(
    table_path={{rotary_table.path}},
    position={{rotary_table.position}},
    radius={{rotary_table.radius}},
    height={{rotary_table.height}},
    angular_velocity_deg={{rotary_table.angular_velocity_deg}},
)

# Mutex on rotary table
setup_robot_claim_mutex(
    mutex_path="/World/TableMutex",
    resource_path={{rotary_table.path}},
    robots=[{{robot_a.path}}, {{robot_b.path}}],
)

# Output bin behind B
create_bin(prim_path={{output_bin.path}}, position={{output_bin.position}}, size={{output_bin.size}})

add_proximity_sensor(sensor_path={{sensor_a.path}}, position={{sensor_a.position}}, size={{sensor_a.size}})
add_proximity_sensor(sensor_path={{sensor_b.path}}, position={{sensor_b.position}}, size={{sensor_b.size}})

# Robot A — places cubes on disc +Y edge
setup_pick_place_controller(
    robot_path={{robot_a.path}},
    target_source="curobo",
    sensor_path={{sensor_a.path}},
    belt_path={{input_conveyor.path}},
    source_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}],
    destination_path={{rotary_table.path}},
    drop_target={{rotary_table.drop_target_a}},
    mutex_path="/World/TableMutex",
    planning_obstacles=["/World/Table", {{input_conveyor.path}}, {{rotary_table.path}}, {{output_bin.path}}],
)

# Robot B — picks from disc -Y edge
setup_pick_place_controller(
    robot_path={{robot_b.path}},
    target_source="curobo",
    sensor_path={{sensor_b.path}},
    belt_path=None,
    source_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}],
    destination_path={{output_bin.path}},
    drop_target={{rotary_table.drop_target_b}},
    mutex_path="/World/TableMutex",
    planning_obstacles=["/World/Table", {{input_conveyor.path}}, {{rotary_table.path}}, {{output_bin.path}}],
)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-73 — UR10 + Cortex demo_ur10_conveyor
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-73", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 1,
            "n_handoffs": 0,
            "destination_kind": "bin",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:robot.fixed_base.ur10",
            "isaac:execution.cortex_behavior_tree",
            "isaac:transport.conveyor",
            "isaac:topology.single_station",
            "isaac:robot.import_asset_library",
        ],
    },
    "roles": {
        "primary_robot": {
            "constraints": ["ur10"],
            "expected_count": 1,
            "required": True,
        },
        "input_conveyor": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "output_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "pick_sensor": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "workpieces": {
            "constraints": ["cube"],
            "min": 4,
            "max": 4,
            "unordered": False,
        },
    },
    "role_defaults": {
        "primary_robot": {
            "path": "/World/UR10",
            "class": "UR10",
            "position": [0, 0, 0.75],
            "robot_family": "ur10",
        },
        "input_conveyor": {
            "path": "/World/ConveyorBelt",
            "position": [-0.5, 0.4, 0.78],
            "size": [2.5, 0.4, 0.05],
            "surface_velocity": [0.2, 0, 0],
        },
        "output_bin": {
            "path": "/World/Bin",
            "position": [0.5, -0.3, 0.75],
            "size": [0.30, 0.30, 0.15],
            "drop_target": [0.5, -0.3, 0.95],
        },
        "pick_sensor": {
            "path": "/World/PickSensor",
            "position": [0.0, 0.4, 0.835],
            "size": [0.06, 0.06, 0.06],
        },
        "workpieces": [
            {"path": "/World/Cube_1", "position": [-1.6, 0.4, 0.835]},
            {"path": "/World/Cube_2", "position": [-1.4, 0.4, 0.835]},
            {"path": "/World/Cube_3", "position": [-1.2, 0.4, 0.835]},
            {"path": "/World/Cube_4", "position": [-1.0, 0.4, 0.835]},
        ],
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Cell", prim_type="Xform")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.5, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

# UR10 base on table
import_robot(file_path={{primary_robot.class}}, format="asset_library", dest_path={{primary_robot.path}})
teleport_prim(prim_path={{primary_robot.path}}, position={{primary_robot.position}})

# Active conveyor (v=0.2 m/s)
create_conveyor(
    prim_path={{input_conveyor.path}},
    position={{input_conveyor.position}},
    size={{input_conveyor.size}},
    surface_velocity={{input_conveyor.surface_velocity}},
)

# 4 cubes upstream of pick zone
{{#each workpieces}}
create_prim(prim_path={{this.path}}, prim_type="Cube", position={{this.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{this.path}}, schema_name=api)
{{/each}}

bulk_set_attribute(prim_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}], attr="physxRigidBody:sleepThreshold", value=0.0)
{{#each workpieces}}
apply_physics_material(prim_path={{this.path}}, material_name="rubber")
{{/each}}

# Output bin
create_bin(prim_path={{output_bin.path}}, position={{output_bin.position}}, size={{output_bin.size}})

# Pick sensor at conveyor pick zone
add_proximity_sensor(sensor_path={{pick_sensor.path}}, position={{pick_sensor.position}}, size={{pick_sensor.size}})

# UR10 cuRobo controller
setup_pick_place_controller(
    robot_path={{primary_robot.path}},
    robot_family={{primary_robot.robot_family}},
    target_source="curobo",
    sensor_path={{pick_sensor.path}},
    belt_path={{input_conveyor.path}},
    source_paths=[{{workpieces[0].path}}, {{workpieces[1].path}}, {{workpieces[2].path}}, {{workpieces[3].path}}],
    destination_path={{output_bin.path}},
    drop_target={{output_bin.drop_target}},
    planning_obstacles=["/World/Table", {{input_conveyor.path}}, {{output_bin.path}}],
)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-87 — ROS2-MoveIt2 Franka pick-place
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-87", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 1,
            "n_handoffs": 0,
            "destination_kind": "bin",
            "uses_conveyor_transport": False,
        },
        "structural_tags": [
            "isaac:bridge.ros2_control",
            "isaac:execution.external_moveit2",
            "isaac:robot.fixed_base.arm",
            "isaac:integration.topic_based_ros2",
            "isaac:topology.single_station",
        ],
    },
    "roles": {
        "primary_robot": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "workpiece": {
            "constraints": ["cube"],
            "expected_count": 1,
            "required": True,
        },
        "output_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
    },
    "role_defaults": {
        "primary_robot": {
            "path": "/World/Franka",
            "class": "franka_panda",
            "position": [0, 0, 0.75],
        },
        "workpiece": {
            "path": "/World/Cube",
            "position": [0.45, 0, 0.78],
            "scale": [0.025, 0.025, 0.025],
        },
        "output_bin": {
            "path": "/World/Bin",
            "position": [0, 0.4, 0.78],
            "size": [0.15, 0.15, 0.10],
        },
    },
    "code_template": """\
precheck_ros2_environment()
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.5, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")
robot_wizard(robot_name={{primary_robot.class}}, dest_path={{primary_robot.path}}, position={{primary_robot.position}})
create_prim(prim_path={{workpiece.path}}, prim_type="Cube", position={{workpiece.position}}, scale={{workpiece.scale}})
apply_api_schema(prim_path={{workpiece.path}}, schema_name="PhysicsRigidBodyAPI")
apply_api_schema(prim_path={{workpiece.path}}, schema_name="PhysicsCollisionAPI")
bulk_set_attribute(
    prim_paths=[{{workpiece.path}}],
    attr="physxRigidBody:sleepThreshold",
    value=0.0,
)
bulk_set_attribute(
    prim_paths=[{{workpiece.path}}],
    attr="physxRigidBody:solverPositionIterationCount",
    value=32,
)

create_bin(prim_path={{output_bin.path}}, position={{output_bin.position}}, size={{output_bin.size}})
setup_pick_place_controller(robot_path={{primary_robot.path}}, source_paths=[{{workpiece.path}}], destination_path={{output_bin.path}}, target_source="ros2_cmd")
setup_ros2_control_compat(robot_path={{primary_robot.path}}, controller_type="joint_trajectory_controller")
setup_ros2_bridge(profile="franka_moveit2", robot_path={{primary_robot.path}})
emit_ros2_control_yaml(robot_path={{primary_robot.path}}, controller_type="joint_trajectory_controller", output_path="/tmp/cp87_ros2_control.yaml")""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-NEW-amr-pickup-handoff — AMR pickup handoff
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-NEW-amr-pickup-handoff", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 1,
            "n_handoffs": 1,
            "destination_kind": "amr_bin",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:robot.mobile.amr",
            "isaac:robot.fixed_base.arm",
            "isaac:topology.arm_amr_handoff",
            "isaac:transport.conveyor",
            "isaac:coordination.dock_and_pickup",
        ],
    },
    "roles": {
        "primary_robot": {
            "constraints": ["franka_panda"],
            "expected_count": 1,
            "required": True,
        },
        "amr": {
            "constraints": ["wheeled_robot"],
            "expected_count": 1,
            "required": True,
        },
        "input_conveyor": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
        "amr_bin": {
            "constraints": ["bin"],
            "expected_count": 1,
            "required": True,
        },
        "pick_sensor": {
            "constraints": ["proximity_sensor"],
            "expected_count": 1,
            "required": True,
        },
        "workpiece": {
            "constraints": ["cube"],
            "expected_count": 1,
            "required": True,
        },
    },
    "role_defaults": {
        "primary_robot": {
            "path": "/World/Franka",
            "class": "franka_panda",
            "position": [0, 0, 0.75],
            "orientation": [0.7071068, 0, 0, 0.7071068],
        },
        "amr": {
            "path": "/World/Carter",
            "position": [0.6, -0.5, 0.10],
            "drive_type": "differential",
            "wheel_radius": 0.14,
            "wheel_base": 0.5,
        },
        "input_conveyor": {
            "path": "/World/ConveyorBelt",
            "position": [0.0, 0.4, 0.78],
            "size": [1.5, 0.3, 0.05],
            "surface_velocity": [0.15, 0, 0],
        },
        "amr_bin": {
            "path": "/World/AmrBin",
            "position": [0.6, -0.5, 0.75],
            "size": [0.20, 0.20, 0.10],
            "drop_target": [0.6, -0.5, 0.82],
        },
        "pick_sensor": {
            "path": "/World/PickSensor",
            "position": [0.4, 0.4, 0.835],
            "size": [0.06, 0.06, 0.06],
        },
        "workpiece": {
            "path": "/World/Cube_1",
            "position": [-0.5, 0.4, 0.835],
        },
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

create_prim(prim_path="/World/Table", prim_type="Cube", position=[0, 0, 0.375], scale=[1.0, 0.5, 0.375])
apply_api_schema(prim_path="/World/Table", schema_name="PhysicsCollisionAPI")

robot_wizard(
    robot_name={{primary_robot.class}},
    dest_path={{primary_robot.path}},
    position={{primary_robot.position}},
    orientation={{primary_robot.orientation}},
)

create_conveyor(
    prim_path={{input_conveyor.path}},
    position={{input_conveyor.position}},
    size={{input_conveyor.size}},
    surface_velocity={{input_conveyor.surface_velocity}},
)

create_prim(prim_path={{workpiece.path}}, prim_type="Cube", position={{workpiece.position}}, size=0.05)
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    apply_api_schema(prim_path={{workpiece.path}}, schema_name=api)
bulk_set_attribute(prim_paths=[{{workpiece.path}}], attr="physxRigidBody:sleepThreshold", value=0.0)
apply_physics_material(prim_path={{workpiece.path}}, material_name="rubber")

create_wheeled_robot(
    robot_path={{amr.path}},
    position={{amr.position}},
    drive_type={{amr.drive_type}},
    wheel_radius={{amr.wheel_radius}},
    wheel_base={{amr.wheel_base}})

create_bin(prim_path={{amr_bin.path}}, position={{amr_bin.position}}, size={{amr_bin.size}})

add_proximity_sensor(sensor_path={{pick_sensor.path}}, position={{pick_sensor.position}}, size={{pick_sensor.size}})

setup_pick_place_controller(
    robot_path={{primary_robot.path}},
    target_source="curobo",
    sensor_path={{pick_sensor.path}},
    belt_path={{input_conveyor.path}},
    source_paths=[{{workpiece.path}}],
    destination_path={{amr_bin.path}},
    drop_target={{amr_bin.drop_target}},
    planning_obstacles=["/World/Table", {{input_conveyor.path}}],
)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-NEW-cross-belt-sorter — 8-chute cross-belt sorter
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-NEW-cross-belt-sorter", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 0,
            "n_handoffs": 0,
            "destination_kind": "chute",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:topology.cross_belt_sorter",
            "isaac:transport.conveyor",
            "isaac:topology.no_robot",
            "isaac:routing.vision_label_divert",
            "isaac:topology.postal_sortation",
        ],
    },
    "roles": {
        "main_belt": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
    },
    "role_defaults": {
        "main_belt": {
            "path": "/World/MainBelt",
            "position": [0, 0.4, 0.78],
            "size": [4.0, 0.30, 0.05],
            "surface_velocity": [0.30, 0, 0],
        },
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

create_conveyor(
    prim_path={{main_belt.path}},
    position={{main_belt.position}},
    size={{main_belt.size}},
    surface_velocity={{main_belt.surface_velocity}},
)

for i in range(8):
    x = -1.5 + i*0.45
    create_conveyor(
        prim_path=f"/World/CrossBelt_{i+1}",
        position=[x, 0.4, 0.79],
        size=[0.20, 0.15, 0.04],
        surface_velocity=[0, (1 if i%2==0 else -1) * 0.30, 0],
    )
    side = -0.65 if i%2==0 else 0.85 if i%2==1 else 0
    create_bin(prim_path=f"/World/Chute_{i+1}", position=[x, side, 0.70], size=[0.25, 0.15, 0.10])

for i in range(8):
    path = f"/World/Cube_{i+1}"
    create_prim(prim_path=path, prim_type="Cube", position=[-2.0 + i*0.30, 0.4, 0.835], size=0.05)
    for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
        apply_api_schema(prim_path=path, schema_name=api)
    set_semantic_label(prim_path=path, label=f"label_{i+1}")
    bulk_set_attribute(prim_paths=[path], attr="physxRigidBody:sleepThreshold", value=0.0)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-NEW-multi-amr-corridor — Multi-AMR corridor navigation
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-NEW-multi-amr-corridor", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 3,
            "n_handoffs": 0,
            "destination_kind": "waypoint",
            "uses_conveyor_transport": False,
        },
        "structural_tags": [
            "isaac:robot.mobile.amr",
            "isaac:topology.multi_amr_fleet",
            "isaac:coordination.collision_avoidance",
            "isaac:topology.corridor_navigation",
            "isaac:coordination.moving_obstacle",
        ],
    },
    "roles": {
        "amr_fleet": {
            "constraints": ["wheeled_robot"],
            "min": 3,
            "max": 3,
            "unordered": False,
        },
    },
    "role_defaults": {
        "amr_fleet": [
            {"path": "/World/Carter_1", "position": [-2.0, 0, 0.10], "target": [2.0, 0, 0.10]},
            {"path": "/World/Carter_2", "position": [-0.5, 0, 0.10], "target": [-2.0, 0, 0.10]},
            {"path": "/World/Carter_3", "position": [1.0, 0, 0.10], "target": [2.0, 0.3, 0.10]},
        ],
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")

set_physics_scene_config(config={"enable_gpu_dynamics": False, "broadphase_type": "MBP"})

create_prim(prim_path="/World/CorridorL", prim_type="Cube", position=[0, -1.0, 0.50], scale=[5.0, 0.10, 1.0])
apply_api_schema(prim_path="/World/CorridorL", schema_name="PhysicsCollisionAPI")

create_prim(prim_path="/World/CorridorR", prim_type="Cube", position=[0, 1.0, 0.50], scale=[5.0, 0.10, 1.0])
apply_api_schema(prim_path="/World/CorridorR", schema_name="PhysicsCollisionAPI")

{{#each amr_fleet}}
create_wheeled_robot(
    robot_path={{this.path}},
    position={{this.position}},
    drive_type="differential",
    wheel_radius=0.14,
    wheel_base=0.5)
{{/each}}

register_moving_obstacle(robot_path={{amr_fleet[0].path}}, obstacle_path={{amr_fleet[1].path}})
register_moving_obstacle(robot_path={{amr_fleet[0].path}}, obstacle_path={{amr_fleet[2].path}})
register_moving_obstacle(robot_path={{amr_fleet[1].path}}, obstacle_path={{amr_fleet[0].path}})
register_moving_obstacle(robot_path={{amr_fleet[1].path}}, obstacle_path={{amr_fleet[2].path}})
register_moving_obstacle(robot_path={{amr_fleet[2].path}}, obstacle_path={{amr_fleet[0].path}})
register_moving_obstacle(robot_path={{amr_fleet[2].path}}, obstacle_path={{amr_fleet[1].path}})

navigate_to(robot_path={{amr_fleet[0].path}}, target_position={{amr_fleet[0].target}})
navigate_to(robot_path={{amr_fleet[1].path}}, target_position={{amr_fleet[1].target}})
navigate_to(robot_path={{amr_fleet[2].path}}, target_position={{amr_fleet[2].target}})""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-NEW-opcua-12conveyors — OPC-UA 12-conveyor live tag control
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-NEW-opcua-12conveyors", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 0,
            "n_handoffs": 0,
            "destination_kind": "none",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:bridge.opcua",
            "isaac:integration.live_tag_control",
            "isaac:topology.no_robot",
            "isaac:transport.conveyor",
            "isaac:topology.industrial_bridge_validation",
        ],
    },
    "roles": {
        "conveyor_grid": {
            "constraints": ["conveyor"],
            "min": 12,
            "max": 12,
            "unordered": True,
        },
    },
    "role_defaults": {
        "conveyor_grid": [
            {"path": f"/World/Conveyors/CV_{i:02d}",
             "position": [(i - 1) % 4 * 1.5 - 2.25, (i - 1) // 4 * 0.6 - 0.6, 0.78],
             "size": [1.2, 0.4, 0.05],
             "surface_velocity": [0.2, 0, 0]}
            for i in range(1, 13)
        ],
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
for i in range(1, 13):
    row = (i - 1) // 4
    col = (i - 1) % 4
    create_conveyor(prim_path=f"/World/Conveyors/CV_{i:02d}", position=[col*1.5 - 2.25, row*0.6 - 0.6, 0.78], size=[1.2, 0.4, 0.05], surface_velocity=[0.2, 0, 0])
opcua_bridge_attach(url="opc.tcp://127.0.0.1:4840", node_map={f"/World/Conveyors/CV_{i:02d}/run_command": f"ns=2;i={i+1}" for i in range(1, 13)}, rate_hz=1.0)""",
})

# ──────────────────────────────────────────────────────────────────────────────
# CP-NEW-plc-conveyor — PLC-in-the-loop Modbus conveyor
# ──────────────────────────────────────────────────────────────────────────────
migrate("CP-NEW-plc-conveyor", {
    "intent": {
        "pattern_hint": "other",
        "structural_features": {
            "n_robot_stations": 0,
            "n_handoffs": 0,
            "destination_kind": "none",
            "uses_conveyor_transport": True,
        },
        "structural_tags": [
            "isaac:bridge.modbus_tcp",
            "isaac:integration.plc_in_the_loop",
            "isaac:topology.no_robot",
            "isaac:transport.conveyor",
            "isaac:topology.industrial_bridge_validation",
        ],
    },
    "roles": {
        "plc_conveyor": {
            "constraints": ["conveyor"],
            "expected_count": 1,
            "required": True,
        },
    },
    "role_defaults": {
        "plc_conveyor": {
            "path": "/World/PLCConveyor",
            "position": [0.0, 0.0, 0.78],
            "size": [2.0, 0.4, 0.05],
            "surface_velocity": [0.3, 0, 0],
        },
    },
    "code_template": """\
create_prim(prim_path="/World/DomeLight", prim_type="DomeLight")
set_attribute(prim_path="/World/DomeLight", attr_name="inputs:intensity", value=1000.0)
create_prim(prim_path="/World/Ground", prim_type="Cube", position=[0, 0, -0.5], scale=[20, 20, 1])
apply_api_schema(prim_path="/World/Ground", schema_name="PhysicsCollisionAPI")
create_conveyor(prim_path={{plc_conveyor.path}}, position={{plc_conveyor.position}}, size={{plc_conveyor.size}}, surface_velocity={{plc_conveyor.surface_velocity}})
modbus_tcp_bridge_attach(host="127.0.0.1", port=5021, register_map={"/World/PLCConveyor/run_command": 0, "/World/PLCConveyor/jam_signal": 1, "/World/PLCConveyor/reset": 2, "/World/PLCConveyor/start_button": 3}, rate_hz=10.0, mode="client")""",
})

print("\nAll migrations applied.")
