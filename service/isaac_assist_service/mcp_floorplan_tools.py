"""MCP-facing floor-plan tools for external chat clients.

These helpers give MCP clients a stable semantic surface over Isaac Sim:
create or inspect a LayoutSpec, choose local USD assets, build dry-run Kit
code, launch a materialized scene variant, and verify spatial relations.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .multimodal.asset_resolution import list_local_asset_options, resolve_layout_assets
from .multimodal.relation_reasoning import normalize_relation_kind, normalize_spatial_relations
from .multimodal.sub_phase_81c_moveit_cumotion_bridge import (
    BridgeConfig,
    MoveItCuMotionBridge,
    PoseGoal,
    detect_planner_for_task,
)
from .multimodal.routes import (
    BuildRequest,
    CampaignLaunchRequest,
    CosmosObserveRequest,
    build_canvas,
    get_store,
    launch_canvas_campaign_variant,
    observe_canvas_from_cosmos,
)
from .multimodal.types import (
    Counts,
    Intent,
    LayoutSpec,
    Position,
    Size,
    Source,
    SpatialRelation,
    StructuralFeatures,
    TypedObject,
)
from .runtime_profiles import get_runtime_profile, runtime_scope_summary


_CLASS_ALIASES = {
    "franka": "franka_panda",
    "panda": "franka_panda",
    "robot": "franka_panda",
    "arm": "franka_panda",
    "table": "table_large",
    "counter": "table_large",
    "workbench": "table_large",
    "bowl": "bowl",
    "fruit": "fruit",
    "orange": "orange",
    "apple": "apple",
    "plate": "plate",
    "hamburger": "hamburger",
    "burger": "hamburger",
    "microwave": "microwave",
    "bin": "bin",
    "box": "cube_large",
    "cube": "cube",
    "conveyor": "conveyor_short",
    "camera": "camera_overhead",
}

_DEFAULT_SIZES = {
    "franka_panda": (0.45, 0.45),
    "table_large": (2.0, 1.0),
    "table_medium": (1.2, 0.8),
    "bowl": (0.22, 0.22),
    "plate": (0.26, 0.26),
    "fruit": (0.08, 0.08),
    "orange": (0.08, 0.08),
    "apple": (0.08, 0.08),
    "hamburger": (0.14, 0.12),
    "microwave": (0.55, 0.40),
    "bin": (0.4, 0.3),
    "cube": (0.05, 0.05),
    "cube_large": (0.25, 0.25),
    "conveyor_short": (1.5, 0.5),
    "camera_overhead": (0.1, 0.1),
}

_RELATION_PATTERNS = (
    ("inside", ("inside", "in")),
    ("on_top_of", ("on top of", "on", "onto")),
    ("mounted_to", ("mounted to", "mounted on")),
    ("beside", ("beside", "next to", "near")),
)


def mcp_floorplan_tool_schemas() -> List[Dict[str, Any]]:
    """Return MCP tool definitions for floor-plan scene creation."""

    return [
        {
            "name": "create_floor_plan_from_text",
            "description": (
                "Create a reviewable floor-plan LayoutSpec from a text scene description. "
                "Use this when an external chat client wants to create an Isaac Sim scene "
                "through the floor-plan semantic surface."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "description": {"type": "string"},
                    "objects": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional explicit objects: {class, name, x, y, asset_ref}.",
                    },
                    "relations": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional relations: {subject, relation, object}. Names or ids accepted.",
                    },
                },
                "required": ["session_id", "description"],
            },
        },
        {
            "name": "create_floor_plan_from_image",
            "description": (
                "Create a floor-plan proposal from an image via the configured Cosmos/Gemini "
                "reasoner path. Requires image_base64 and a running reasoner endpoint."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "image_base64": {"type": "string"},
                    "prompt": {"type": "string"},
                    "mime_type": {"type": "string"},
                },
                "required": ["session_id", "image_base64"],
            },
        },
        {
            "name": "create_franka_physics_pick_scene",
            "description": (
                "Create a full-physics Franka tabletop pick scene with rigid workpieces, "
                "static support fixtures, relation metadata, and an existing pick-place "
                "controller install plan. Default backend auto-selects cuRobo when available; "
                "cuMotion/MoveIt requests are recorded as bridge contracts."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "description": {"type": "string"},
                    "motion_backend": {
                        "type": "string",
                        "enum": [
                            "auto",
                            "curobo",
                            "cumotion",
                            "moveit_ompl",
                            "moveit_pilz",
                            "native",
                            "spline",
                        ],
                    },
                    "object_count": {"type": "integer"},
                    "dry_run": {"type": "boolean"},
                    "build": {"type": "boolean"},
                    "resolve_assets": {"type": "boolean"},
                    "generate_controller_code": {"type": "boolean"},
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "create_ros2_scene_harness",
            "description": (
                "Create a project-local ROS2 harness for a floor-plan scene. The tool "
                "checks the requested Isaac Sim runtime profile and ROS2 environment, "
                "creates or reuses a scene session, builds the scene in dry-run/live mode, "
                "optionally prepares a launch command, and writes a colcon-ready ROS2 "
                "package with scene contract, controller config, launch file, and demo node."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "session_id": {"type": "string"},
                    "scenario": {
                        "type": "string",
                        "description": "Harness scenario preset. Defaults to franka_warehouse_pick_place.",
                    },
                    "description": {"type": "string"},
                    "runtime_profile": {
                        "type": "string",
                        "description": "Isaac runtime profile, e.g. isaacsim-6.0 or isaacsim-5.1.",
                    },
                    "motion_backend": {"type": "string"},
                    "object_count": {"type": "integer"},
                    "workspace_root": {"type": "string"},
                    "build_scene": {"type": "boolean"},
                    "build_dry_run": {
                        "type": "boolean",
                        "description": "Keep scene build as generated code without Kit RPC mutation. Defaults to true.",
                    },
                    "launch_scene": {"type": "boolean"},
                    "launch_dry_run": {
                        "type": "boolean",
                        "description": "Dry-run the Isaac launch command without starting Isaac. Defaults to dry_run.",
                    },
                    "probe_ros2_omnigraph": {
                        "type": "boolean",
                        "description": "Include a ROS2 OmniGraph compatibility probe result in the generated contract. Defaults to true.",
                    },
                    "probe_dry_run": {
                        "type": "boolean",
                        "description": "Keep the ROS2 OmniGraph compatibility probe offline/read-only script generation. Defaults to true.",
                    },
                    "probe_timeout": {"type": "number"},
                    "force_launch": {
                        "type": "boolean",
                        "description": "Launch even if ROS2/Isaac precheck reports issues.",
                    },
                    "dry_run": {"type": "boolean"},
                    "wait": {"type": "boolean"},
                },
                "required": ["project_name"],
            },
        },
        {
            "name": "probe_ros2_omnigraph_compatibility",
            "description": (
                "Read-only compatibility probe for Isaac Sim ROS2 OmniGraph support. "
                "Default dry_run=true returns the expected node namespace and probe "
                "script without touching Isaac. With dry_run=false and Kit RPC alive, "
                "it inspects registered node types but does not create graph nodes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "runtime_profile": {
                        "type": "string",
                        "description": "Isaac runtime profile, e.g. isaacsim-6.0 or isaacsim-5.1.",
                    },
                    "dry_run": {"type": "boolean"},
                    "timeout": {"type": "number"},
                    "include_probe_code": {"type": "boolean"},
                },
            },
        },
        {
            "name": "probe_ros2_omnigraph_creation",
            "description": (
                "Disposable live-creation probe for Isaac Sim ROS2 OmniGraph support. "
                "Default dry_run=true returns the exact context-only graph creation script. "
                "With dry_run=false and Kit RPC alive, it creates and removes a temporary "
                "ROS2 probe graph under /World/IsaacAssistProbes. Real-target modes must "
                "first prove the requested target prim exists in the active stage."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "runtime_profile": {
                        "type": "string",
                        "description": "Isaac runtime profile, e.g. isaacsim-6.0 or isaacsim-5.1.",
                    },
                    "node_namespace": {
                        "type": "string",
                        "description": "Preferred ROS2 OmniGraph node namespace to try first.",
                    },
                    "probe_mode": {
                        "type": "string",
                        "description": (
                            "Safety gate to run. Supported values: context_only, "
                            "context_pubsub_no_targets, context_pubsub_dummy_target, "
                            "context_pubsub_dummy_target_tick, articulation_dummy_target, "
                            "subscribe_articulation_dummy_target_tick, "
                            "subscribe_articulation_real_target_tick."
                        ),
                    },
                    "probe_path": {
                        "type": "string",
                        "description": "Temporary graph path. Must stay under /World/IsaacAssistProbes/.",
                    },
                    "dummy_target_path": {
                        "type": "string",
                        "description": (
                            "Temporary target prim path for dummy-target probe modes. "
                            "Must stay under /World/IsaacAssistProbes/."
                        ),
                    },
                    "target_prim_path": {
                        "type": "string",
                        "description": (
                            "Existing real target prim path for real-target probe modes. "
                            "Defaults to /World/Franka and must not be under probes."
                        ),
                    },
                    "cleanup": {"type": "boolean"},
                    "allow_when_playing": {
                        "type": "boolean",
                        "description": (
                            "Allow tick-wiring probe while Isaac timeline is playing. "
                            "Defaults to false."
                        ),
                    },
                    "dry_run": {"type": "boolean"},
                    "timeout": {"type": "number"},
                    "include_probe_code": {"type": "boolean"},
                },
            },
        },
        {
            "name": "search_local_assets",
            "description": "Search configured local USD asset roots for selectable Isaac scene assets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "set_object_asset",
            "description": (
                "Set a reviewed USD asset reference on one object in a floor-plan session. "
                "The build resolver will prefer this asset over class defaults."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "object_id": {"type": "string"},
                    "asset_ref": {"type": "string"},
                    "asset_label": {"type": "string"},
                },
                "required": ["session_id", "object_id", "asset_ref"],
            },
        },
        {
            "name": "build_scene_from_floor_plan",
            "description": (
                "Build or dry-run the current LayoutSpec. Default dry_run=true returns generated "
                "Kit code and asset resolutions without mutating Isaac Sim."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "template_id": {"type": "string"},
                    "force_freeform": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "launch_scene_in_isaac",
            "description": (
                "Materialize and launch one scene variant in Isaac Sim. Default dry_run=true "
                "returns the launch command and files without starting Isaac."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "variant_index": {"type": "integer"},
                    "variant_id": {"type": "string"},
                    "workspace_root": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "wait": {"type": "boolean"},
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "verify_scene_relations",
            "description": (
                "Run deterministic relation reasoning on the current floor-plan LayoutSpec and "
                "return normalized relations plus diagnostics."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
    ]


def handles_floorplan_tool(name: str) -> bool:
    return name in {tool["name"] for tool in mcp_floorplan_tool_schemas()}


async def call_floorplan_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch one floor-plan MCP tool and return a JSON-serializable payload."""

    if name == "create_floor_plan_from_text":
        return await create_floor_plan_from_text(arguments)
    if name == "create_floor_plan_from_image":
        return await create_floor_plan_from_image(arguments)
    if name == "create_franka_physics_pick_scene":
        return await create_franka_physics_pick_scene(arguments)
    if name == "create_ros2_scene_harness":
        return await create_ros2_scene_harness(arguments)
    if name == "probe_ros2_omnigraph_compatibility":
        return await probe_ros2_omnigraph_compatibility(arguments)
    if name == "probe_ros2_omnigraph_creation":
        return await probe_ros2_omnigraph_creation(arguments)
    if name == "search_local_assets":
        return search_local_assets(arguments)
    if name == "set_object_asset":
        return await set_object_asset(arguments)
    if name == "build_scene_from_floor_plan":
        return await build_scene_from_floor_plan(arguments)
    if name == "launch_scene_in_isaac":
        return await launch_scene_in_isaac(arguments)
    if name == "verify_scene_relations":
        return verify_scene_relations(arguments)
    raise ValueError(f"unknown floor-plan MCP tool: {name}")


async def create_floor_plan_from_text(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = _required_str(arguments, "session_id")
    description = _required_str(arguments, "description")
    explicit_objects = arguments.get("objects") or []
    explicit_relations = arguments.get("relations") or []

    objects = _objects_from_explicit(explicit_objects)
    if not objects:
        objects = _objects_from_description(description)
    relations = _relations_from_explicit(explicit_relations, objects)
    relations.extend(_relations_from_description(description, objects))

    spec = _layout_spec(description, objects, relations)
    saved = await _save_new_revision(session_id, spec)
    relation_result = normalize_spatial_relations(saved)
    return _session_payload(session_id, saved, {
        "created_from": "text",
        "relation_diagnostics": relation_result.diagnostics_as_dicts(),
        "asset_resolutions": _asset_payload(saved.objects or []),
    })


async def create_floor_plan_from_image(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = _required_str(arguments, "session_id")
    image_base64 = _required_str(arguments, "image_base64")
    prompt = str(arguments.get("prompt") or "Reconstruct this robotics scene as an Isaac Sim floor plan.")
    mime_type = str(arguments.get("mime_type") or "image/png")
    _validate_base64(image_base64)

    response = await observe_canvas_from_cosmos(
        session_id,
        CosmosObserveRequest(
            prompt=prompt,
            image_base64=image_base64,
            mime_type=mime_type,
            input_kind="photo",
            parent_revision=get_store().get_revision(session_id),
        ),
    )
    return {"created_from": "image", **response}


async def create_franka_physics_pick_scene(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Create a physically configured Franka pick scene and controller plan."""

    session_id = _required_str(arguments, "session_id")
    description = str(
        arguments.get("description")
        or "Franka Panda picks rigid cubes from a table and places them in a bin."
    )
    motion_backend = str(arguments.get("motion_backend") or "auto").strip().lower()
    object_count = max(1, min(6, int(arguments.get("object_count") or 3)))
    dry_run = bool(arguments.get("dry_run", True))
    should_build = bool(arguments.get("build", False))
    resolve_assets = bool(arguments.get("resolve_assets", False))
    generate_controller_code = bool(arguments.get("generate_controller_code", False))

    spec = _franka_pick_scene_spec(
        description=description,
        motion_backend=motion_backend,
        object_count=object_count,
    )
    saved = await _save_new_revision(session_id, spec)
    build_response: Dict[str, Any] = {
        "status": "skipped",
        "message": (
            "Scene spec created. Set build=true or call build_scene_from_floor_plan "
            "after asset review to generate Kit code."
        ),
    }
    if should_build:
        build_response = await build_canvas(
            session_id,
            BuildRequest(template_id=None, force_freeform=False, dry_run=dry_run),
        )
    controller_plan = _franka_controller_plan(
        motion_backend=motion_backend,
        object_count=object_count,
        generate_controller_code=generate_controller_code,
    )
    relation_result = normalize_spatial_relations(saved)
    return _session_payload(session_id, saved, {
        "created_from": "franka_physics_pick_scene",
        "build": build_response,
        "controller_plan": controller_plan,
        "relation_diagnostics": relation_result.diagnostics_as_dicts(),
        "asset_resolutions": _asset_payload(saved.objects or []) if resolve_assets else [],
    })


async def create_ros2_scene_harness(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Create a project-local ROS2 harness for a generated floor-plan scene."""

    project_name = _required_str(arguments, "project_name")
    package_name = _ros2_package_name(project_name)
    session_id = str(arguments.get("session_id") or package_name).strip()
    scenario = str(arguments.get("scenario") or "franka_warehouse_pick_place").strip()
    if scenario not in {"franka_warehouse_pick_place", "franka_pick_place"}:
        raise ValueError(
            "create_ros2_scene_harness currently supports "
            "franka_warehouse_pick_place and franka_pick_place"
        )

    runtime_profile = get_runtime_profile(str(arguments.get("runtime_profile") or "isaacsim-6.0"))
    runtime = runtime_scope_summary(runtime_profile)
    ros2 = _ros2_environment_report()
    precheck_issues = _harness_precheck_issues(runtime, ros2)

    description = str(
        arguments.get("description")
        or "Warehouse Franka arm picks rigid workpieces from a table and places them in a crate."
    )
    motion_backend = str(arguments.get("motion_backend") or "curobo").strip().lower()
    object_count = max(1, min(6, int(arguments.get("object_count") or 3)))
    dry_run = bool(arguments.get("dry_run", True))
    build_dry_run = bool(arguments.get("build_dry_run", True))
    launch_dry_run = bool(arguments.get("launch_dry_run", dry_run))
    build_scene = bool(arguments.get("build_scene", True))
    launch_scene = bool(arguments.get("launch_scene", False))
    probe_ros2_omnigraph = bool(arguments.get("probe_ros2_omnigraph", True))
    probe_dry_run = bool(arguments.get("probe_dry_run", True))

    scene = await create_franka_physics_pick_scene({
        "session_id": session_id,
        "description": description,
        "motion_backend": motion_backend,
        "object_count": object_count,
        "dry_run": build_dry_run,
        "build": build_scene,
        "resolve_assets": True,
        "generate_controller_code": False,
    })
    build_response = scene.get("build", {"status": "skipped"})
    launch_response: Dict[str, Any] = {"status": "skipped"}
    if launch_scene:
        if precheck_issues and not bool(arguments.get("force_launch", False)):
            launch_response = {
                "status": "blocked_by_precheck",
                "issues": precheck_issues,
            }
        else:
            launch_response = await launch_scene_in_isaac({
                "session_id": session_id,
                "workspace_root": arguments.get("workspace_root"),
                "dry_run": launch_dry_run,
                "wait": bool(arguments.get("wait", False)),
            })

    controller = scene["spec"]["parameters"]["controller"]
    omnigraph_probe = (
        await probe_ros2_omnigraph_compatibility({
            "runtime_profile": runtime_profile.key,
            "dry_run": probe_dry_run,
            "timeout": float(arguments.get("probe_timeout") or 10.0),
            "include_probe_code": False,
        })
        if probe_ros2_omnigraph
        else {
            "status": "skipped",
            "recommendation": {
                "author_omnigraph": False,
                "connect_articulation_controller": False,
                "reason": "ROS2 OmniGraph compatibility probe was not requested.",
            },
        }
    )
    project_root = _harness_project_root(arguments.get("workspace_root"), package_name)
    files = _write_ros2_harness_project(
        project_root=project_root,
        package_name=package_name,
        project_name=project_name,
        session_id=session_id,
        scenario=scenario,
        description=description,
        runtime=runtime,
        ros2=ros2,
        precheck_issues=precheck_issues,
        controller=controller,
        scene=scene,
        build_response=build_response,
        launch_response=launch_response,
        omnigraph_probe=omnigraph_probe,
    )

    return {
        "status": "ready" if not precheck_issues else "needs_environment",
        "project_name": project_name,
        "package_name": package_name,
        "project_root": str(project_root),
        "session_id": session_id,
        "scenario": scenario,
        "runtime": runtime,
        "ros2": ros2,
        "precheck": {
            "ok": not precheck_issues,
            "issues": precheck_issues,
        },
        "scene": {
            "created_from": scene.get("created_from"),
            "revision": scene.get("revision"),
            "summary": scene.get("summary"),
            "relation_diagnostics": scene.get("relation_diagnostics", []),
        },
        "build": _compact_build_response(build_response),
        "launch": _compact_launch_response(launch_response),
        "ros2_omnigraph_probe": _compact_probe_response(omnigraph_probe),
        "files": files,
        "next_commands": [
            f"cd {project_root}",
            "colcon build --symlink-install",
            "source install/setup.bash",
            f"ros2 launch {package_name} warehouse_pick_place.launch.py",
        ],
    }


async def probe_ros2_omnigraph_compatibility(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Probe ROS2 OmniGraph support without creating graph nodes by default."""

    runtime_profile = get_runtime_profile(str(arguments.get("runtime_profile") or "isaacsim-6.0"))
    runtime = runtime_scope_summary(runtime_profile)
    dry_run = bool(arguments.get("dry_run", True))
    timeout = float(arguments.get("timeout") or 10.0)
    candidate_namespaces = _ros2_candidate_namespaces(runtime_profile.ros2_omnigraph_namespace)
    required_suffixes = [
        "ROS2Context",
        "ROS2PublishJointState",
        "ROS2SubscribeJointState",
    ]
    probe_code = _ros2_omnigraph_readonly_probe_code(candidate_namespaces, required_suffixes)

    response: Dict[str, Any] = {
        "status": "dry_run" if dry_run else "pending",
        "dry_run": dry_run,
        "runtime": runtime,
        "candidate_namespaces": candidate_namespaces,
        "required_node_suffixes": required_suffixes,
        "read_only": True,
        "graph_authoring_tested": False,
        "recommendation": {
            "author_omnigraph": False,
            "connect_articulation_controller": False,
            "reason": (
                "Keep generated scenes in ROS2 contract-marker mode until a "
                "separate live graph creation probe passes on this Isaac build."
            ),
        },
    }
    if bool(arguments.get("include_probe_code", False)) or dry_run:
        response["probe_code"] = probe_code
    if dry_run:
        return response

    try:
        from .chat.tools import kit_tools
    except Exception as exc:  # pragma: no cover - defensive optional import
        response.update({
            "status": "kit_tools_unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        })
        return response

    if not await kit_tools.is_kit_rpc_alive():
        response.update({
            "status": "kit_rpc_unavailable",
            "error": "Kit RPC /health did not respond ok on the configured port.",
        })
        return response

    result = await kit_tools.exec_sync(probe_code, timeout=timeout)
    response["kit_rpc"] = {
        "success": bool(result.get("success")),
        "output": result.get("output", ""),
    }
    readback = _extract_probe_json(str(result.get("output") or ""))
    if readback:
        response["readback"] = readback
        matching = [
            namespace
            for namespace, values in (readback.get("namespaces") or {}).items()
            if all(values.get(suffix) for suffix in required_suffixes)
        ]
        response["matching_namespaces"] = matching
        response["detected_namespace"] = matching[0] if matching else ""
        response["status"] = "ok" if matching else "missing_nodes"
        if matching:
            response["recommendation"]["reason"] = (
                f"Read-only probe found ROS2 node types under {matching[0]!r}. "
                "Graph creation remains deferred until the explicit creation probe passes."
            )
    else:
        response.update({
            "status": "probe_parse_failed" if result.get("success") else "probe_failed",
            "error": str(result.get("output") or "Kit RPC probe did not return JSON."),
        })
    return response


async def probe_ros2_omnigraph_creation(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Create and remove a tiny ROS2Context-only OmniGraph as a live safety probe."""

    runtime_profile = get_runtime_profile(str(arguments.get("runtime_profile") or "isaacsim-6.0"))
    runtime = runtime_scope_summary(runtime_profile)
    dry_run = bool(arguments.get("dry_run", True))
    timeout = float(arguments.get("timeout") or 10.0)
    cleanup = bool(arguments.get("cleanup", True))
    allow_when_playing = bool(arguments.get("allow_when_playing", False))
    probe_mode = str(arguments.get("probe_mode") or "context_only")
    supported_modes = {
        "context_only",
        "context_pubsub_no_targets",
        "context_pubsub_dummy_target",
        "context_pubsub_dummy_target_tick",
        "articulation_dummy_target",
        "subscribe_articulation_dummy_target_tick",
        "subscribe_articulation_real_target_tick",
    }
    if probe_mode not in supported_modes:
        raise ValueError(
            "probe_ros2_omnigraph_creation supports probe_mode values: "
            + ", ".join(sorted(supported_modes))
        )

    node_namespace = str(
        arguments.get("node_namespace") or runtime_profile.ros2_omnigraph_namespace
    )
    candidate_namespaces = _ros2_candidate_namespaces(node_namespace)
    default_probe_path = (
        "/World/IsaacAssistProbes/ArticulationControllerProbe"
        if probe_mode == "articulation_dummy_target"
        else "/World/IsaacAssistProbes/ROS2ContextCreationProbe"
    )
    probe_path = str(arguments.get("probe_path") or default_probe_path)
    if not probe_path.startswith("/World/IsaacAssistProbes/"):
        raise ValueError("probe_path must stay under /World/IsaacAssistProbes/")
    dummy_target_path = str(
        arguments.get("dummy_target_path") or "/World/IsaacAssistProbes/DummyJointTarget"
    )
    if not dummy_target_path.startswith("/World/IsaacAssistProbes/"):
        raise ValueError("dummy_target_path must stay under /World/IsaacAssistProbes/")
    target_prim_path = ""
    if probe_mode == "subscribe_articulation_real_target_tick":
        target_prim_path = str(arguments.get("target_prim_path") or "/World/Franka")
        if not target_prim_path.startswith("/World/"):
            raise ValueError("target_prim_path must be an absolute /World prim path")
        if target_prim_path.startswith("/World/IsaacAssistProbes/"):
            raise ValueError("target_prim_path must not be under /World/IsaacAssistProbes/")

    probe_code = _ros2_omnigraph_creation_probe_code(
        candidate_namespaces=candidate_namespaces,
        probe_path=probe_path,
        cleanup=cleanup,
        probe_mode=probe_mode,
        dummy_target_path=dummy_target_path,
        target_prim_path=target_prim_path,
        allow_when_playing=allow_when_playing,
    )
    response: Dict[str, Any] = {
        "status": "dry_run" if dry_run else "pending",
        "dry_run": dry_run,
        "runtime": runtime,
        "candidate_namespaces": candidate_namespaces,
        "probe_mode": probe_mode,
        "probe_path": probe_path,
        "dummy_target_path": dummy_target_path,
        "target_prim_path": target_prim_path,
        "cleanup": cleanup,
        "allow_when_playing": allow_when_playing,
        "read_only": False,
        "graph_authoring_tested": False,
        "touches_scene_assets": False,
        "touches_robot": probe_mode == "subscribe_articulation_real_target_tick",
        "touches_topics": False,
        "requires_existing_target": probe_mode == "subscribe_articulation_real_target_tick",
        "sets_topic_names": probe_mode in {
            "context_pubsub_dummy_target",
            "context_pubsub_dummy_target_tick",
            "subscribe_articulation_dummy_target_tick",
            "subscribe_articulation_real_target_tick",
        },
        "sets_target_prim": probe_mode in {
            "context_pubsub_dummy_target",
            "context_pubsub_dummy_target_tick",
            "articulation_dummy_target",
            "subscribe_articulation_dummy_target_tick",
            "subscribe_articulation_real_target_tick",
        },
        "uses_dummy_target": probe_mode in {
            "context_pubsub_dummy_target",
            "context_pubsub_dummy_target_tick",
            "articulation_dummy_target",
            "subscribe_articulation_dummy_target_tick",
        },
        "wires_tick": probe_mode in {
            "context_pubsub_dummy_target_tick",
            "subscribe_articulation_dummy_target_tick",
            "subscribe_articulation_real_target_tick",
        },
        "requires_timeline_stopped": probe_mode in {
            "context_pubsub_dummy_target_tick",
            "subscribe_articulation_dummy_target_tick",
            "subscribe_articulation_real_target_tick",
        },
        "creates_articulation_controller": probe_mode in {
            "articulation_dummy_target",
            "subscribe_articulation_dummy_target_tick",
            "subscribe_articulation_real_target_tick",
        },
        "connects_joint_command_outputs": probe_mode in {
            "subscribe_articulation_dummy_target_tick",
            "subscribe_articulation_real_target_tick",
        },
        "recommendation": {
            "author_omnigraph": False,
            "connect_articulation_controller": False,
            "reason": (
                "This context-only probe is the first live authoring gate. "
                "Keep full ROS2 graph authoring and articulation-controller wiring "
                "disabled until publish/subscribe node creation also passes."
            ),
        },
    }
    if bool(arguments.get("include_probe_code", False)) or dry_run:
        response["probe_code"] = probe_code
    if dry_run:
        return response

    try:
        from .chat.tools import kit_tools
    except Exception as exc:  # pragma: no cover - defensive optional import
        response.update({
            "status": "kit_tools_unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        })
        return response

    if not await kit_tools.is_kit_rpc_alive():
        response.update({
            "status": "kit_rpc_unavailable",
            "error": "Kit RPC /health did not respond ok on the configured port.",
        })
        return response

    result = await kit_tools.exec_sync(probe_code, timeout=timeout)
    response["graph_authoring_tested"] = True
    response["kit_rpc"] = {
        "success": bool(result.get("success")),
        "output": result.get("output", ""),
    }
    readback = _extract_creation_probe_json(str(result.get("output") or ""))
    if readback:
        response["readback"] = readback
        response["detected_namespace"] = readback.get("detected_namespace", "")
        response["created"] = bool(readback.get("created"))
        response["removed"] = bool(readback.get("removed"))
        response["dummy_target_created"] = bool(readback.get("dummy_target_created"))
        response["dummy_target_removed"] = bool(readback.get("dummy_target_removed"))
        response["target_prim_exists"] = bool(readback.get("target_prim_exists"))
        response["target_prim_type_name"] = readback.get("target_prim_type_name", "")
        response["active_stage_identifier"] = readback.get("stage_identifier", "")
        response["active_stage_real_path"] = readback.get("stage_real_path", "")
        response["timeline_playing"] = bool(readback.get("timeline_playing"))
        response["tick_wiring_created"] = bool(readback.get("tick_wiring_created"))
        response["articulation_controller_created"] = bool(
            readback.get("articulation_controller_created")
        )
        response["joint_command_outputs_connected"] = bool(
            readback.get("joint_command_outputs_connected")
        )
        response["joint_command_connection_paths"] = readback.get(
            "joint_command_connection_paths",
            [],
        )
        response["created_node_suffixes"] = readback.get("created_node_suffixes", [])
        response["created_node_names"] = readback.get("created_node_names", [])
        response["missing_node_suffixes"] = readback.get("missing_node_suffixes", [])
        response["missing_absolute_node_types"] = readback.get("missing_absolute_node_types", [])
        response["set_attrs_ok"] = readback.get("set_attrs_ok", [])
        response["set_attrs_failed"] = readback.get("set_attrs_failed", [])
        response["status"] = "ok" if readback.get("ok") else str(readback.get("status") or "failed")
        if readback.get("ok"):
            if probe_mode == "context_only":
                response["recommendation"]["reason"] = (
                    "Context-only ROS2 OmniGraph creation passed and cleaned up. "
                    "Next gate should create disposable publish/subscribe nodes with no "
                    "target prims before enabling the full scene graph."
                )
            else:
                if probe_mode == "context_pubsub_no_targets":
                    response["recommendation"]["reason"] = (
                        "Disposable ROS2 context plus joint-state publish/subscribe nodes "
                        "were created, connected to context, and cleaned up without topics, "
                        "target prims, ticks, or articulation-controller wiring. Next gate "
                        "can test topic and targetPrim assignment on a disposable prim."
                    )
                else:
                    if probe_mode == "context_pubsub_dummy_target":
                        response["recommendation"]["reason"] = (
                            "Disposable ROS2 publish/subscribe nodes accepted probe topicName "
                            "and targetPrim values against a fake prim, then cleaned up. Next "
                            "gate can test tick wiring on a disposable graph before any real "
                            "Franka or articulation-controller connection."
                        )
                    else:
                        if probe_mode == "context_pubsub_dummy_target_tick":
                            response["recommendation"]["reason"] = (
                                "Disposable ROS2 publish/subscribe nodes accepted tick wiring "
                                "while the timeline was stopped, then cleaned up. Next gate can "
                                "test an articulation-controller node in isolation before any "
                                "real Franka target is connected."
                            )
                        elif probe_mode == "articulation_dummy_target":
                            response["recommendation"]["reason"] = (
                                "A disposable IsaacArticulationController node accepted a fake "
                                "targetPrim and cleaned up. Next gate can connect disposable "
                                "ROS2 joint-command outputs into a disposable controller node, "
                                "still without a real Franka target."
                            )
                        elif probe_mode == "subscribe_articulation_dummy_target_tick":
                            response["recommendation"]["reason"] = (
                                "Disposable ROS2 joint-command outputs connected into a "
                                "fake-target IsaacArticulationController while the timeline was "
                                "stopped, then cleaned up. Next gate can test the same graph "
                                "against a real loaded robot target with execution still disabled."
                            )
                        else:
                            response["recommendation"]["reason"] = (
                                "A temporary ROS2 command graph targeted the loaded real robot "
                                "prim after confirming it exists in the active stage, then cleaned "
                                "up. Next gate can keep the graph resident and wire a minimal ROS2 "
                                "test command publisher only after the user confirms the stage."
                            )
    else:
        response.update({
            "status": "probe_parse_failed" if result.get("success") else "probe_failed",
            "error": str(result.get("output") or "Kit RPC probe did not return JSON."),
        })
    return response


def search_local_assets(arguments: Dict[str, Any]) -> Dict[str, Any]:
    query = str(arguments.get("query") or "")
    limit = int(arguments.get("limit") or 25)
    options = list_local_asset_options(query=query, limit=limit)
    return {
        "query": query,
        "count": len(options),
        "options": [
            {
                "label": item.label,
                "usd_ref": item.usd_ref,
                "source": item.source,
                "category": item.category,
                "relative_path": item.relative_path,
                "tags": list(item.tags),
                "score": item.score,
            }
            for item in options
        ],
    }


async def set_object_asset(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = _required_str(arguments, "session_id")
    object_id = _required_str(arguments, "object_id")
    asset_ref = _required_str(arguments, "asset_ref")
    asset_label = str(arguments.get("asset_label") or "")
    store = get_store()
    spec = _require_spec(session_id)
    objects = []
    found = False
    for obj in spec.objects or []:
        if obj.id != object_id:
            objects.append(obj)
            continue
        found = True
        metadata = dict(obj.metadata or {})
        metadata["reviewed_asset_ref"] = asset_ref
        if asset_label:
            metadata["reviewed_asset_label"] = asset_label
        metadata["reviewed_asset_source"] = "mcp"
        objects.append(obj.model_copy(update={"metadata": metadata}))
    if not found:
        raise ValueError(f"object_id {object_id!r} not found in session {session_id!r}")
    saved = await store.save_with_cas(
        session_id,
        spec.model_copy(update={"objects": objects}),
        spec.revision,
    )
    return _session_payload(session_id, saved, {"asset_resolutions": _asset_payload(saved.objects or [])})


async def build_scene_from_floor_plan(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = _required_str(arguments, "session_id")
    response = await build_canvas(
        session_id,
        BuildRequest(
            template_id=arguments.get("template_id"),
            force_freeform=bool(arguments.get("force_freeform", False)),
            dry_run=bool(arguments.get("dry_run", True)),
        ),
    )
    return response


async def launch_scene_in_isaac(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = _required_str(arguments, "session_id")
    response = await launch_canvas_campaign_variant(
        session_id,
        CampaignLaunchRequest(
            workspace_root=arguments.get("workspace_root"),
            variant_index=int(arguments.get("variant_index") or 1),
            variant_id=arguments.get("variant_id"),
            dry_run=bool(arguments.get("dry_run", True)),
            wait=bool(arguments.get("wait", False)),
        ),
    )
    return response


def verify_scene_relations(arguments: Dict[str, Any]) -> Dict[str, Any]:
    session_id = _required_str(arguments, "session_id")
    spec = _require_spec(session_id)
    result = normalize_spatial_relations(spec)
    return {
        "session_id": session_id,
        "revision": spec.revision,
        "valid": result.valid,
        "relations": [relation.as_dict() for relation in result.relations],
        "diagnostics": result.diagnostics_as_dicts(),
    }


async def _save_new_revision(session_id: str, spec: LayoutSpec) -> LayoutSpec:
    store = get_store()
    parent_revision = store.get_revision(session_id)
    return await store.save_with_cas(session_id, spec, parent_revision)


def _layout_spec(description: str, objects: List[TypedObject], relations: List[SpatialRelation]) -> LayoutSpec:
    counts = Counts(
        robots=sum(1 for obj in objects if obj.object_class in {"franka_panda", "ur5e", "ur10", "ur10e"}),
        conveyors=sum(1 for obj in objects if "conveyor" in obj.object_class),
        bins=sum(1 for obj in objects if obj.object_class in {"bin", "bin_large"}),
        cubes=sum(1 for obj in objects if "cube" in obj.object_class or obj.object_class in {"fruit", "hamburger"}),
        sensors=sum(1 for obj in objects if "camera" in obj.object_class or "lidar" in obj.object_class),
        humans=0,
    )
    features = StructuralFeatures(
        n_robot_stations=max(1, counts.robots),
        n_destinations=max(1, counts.bins or sum(1 for obj in objects if obj.object_class in {"bowl", "plate", "microwave"})),
        destination_kind="fixture",
        uses_conveyor_transport=counts.conveyors > 0,
    )
    return LayoutSpec(
        intent=Intent(
            pattern_hint="pick_place",
            counts=counts,
            structural_features=features,
            structural_tags=["user:mcp.floor_plan"],
        ),
        objects=objects,
        relations=relations,
        source=Source(
            modality="text",
            confidence=0.75,
            timestamp=datetime.now(timezone.utc),
            raw_input=description,
            metadata={"producer": "mcp_floorplan_tools"},
        ),
    )


def _franka_pick_scene_spec(
    *,
    description: str,
    motion_backend: str,
    object_count: int,
) -> LayoutSpec:
    objects: list[TypedObject] = [
        _typed_object(
            "table_large",
            0,
            1,
            name="Table",
            x=0.55,
            y=0.0,
            metadata={
                "physics": "static_collider",
                "support_surface_z_m": 0.75,
                "height_m": 0.75,
            },
            role_hint="workspace",
        ),
        _typed_object(
            "franka_panda",
            0,
            1,
            name="Franka",
            x=-0.45,
            y=0.0,
            metadata={
                "physics": "articulation",
                "mount": "table_edge",
                "base_z_m": 0.75,
                "requires_articulation_physics": True,
            },
            role_hint="robot",
        ),
        _typed_object(
            "bin",
            0,
            1,
            name="DropBin",
            x=0.95,
            y=-0.32,
            metadata={
                "physics": "static_collider",
                "interior_floor_z_m": 0.05,
                "height_m": 0.30,
            },
            role_hint="target",
        ),
        _typed_object(
            "camera_overhead",
            0,
            1,
            name="OverheadCamera",
            x=0.45,
            y=0.0,
            metadata={
                "physics": "sensor",
                "height_m": 2.2,
                "look_at": "/World/Table",
            },
            role_hint="sensor",
        ),
    ]
    for index in range(object_count):
        objects.append(
            _typed_object(
                "cube_small",
                index,
                object_count,
                name=f"PickObject_{index + 1}",
                x=0.38 + index * 0.12,
                y=0.18,
                metadata={
                    "physics": "dynamic_rigid_body",
                    "mass_kg": 0.05,
                    "collision": "convex",
                    "graspable": True,
                    "pick_order": index + 1,
                    "height_m": 0.05,
                },
                role_hint="pick",
            )
        )

    relations = [
        _relation("franka", "mounted_to", "table", "mcp_franka_pick_scene"),
        _relation("dropbin", "on_top_of", "table", "mcp_franka_pick_scene"),
        _relation("overheadcamera", "stacked_above", "table", "mcp_franka_pick_scene"),
    ]
    relations.extend(
        _relation(f"pickobject_{index + 1}", "on_top_of", "table", "mcp_franka_pick_scene")
        for index in range(object_count)
    )

    counts = Counts(
        robots=1,
        conveyors=0,
        bins=1,
        cubes=object_count,
        sensors=1,
        humans=0,
    )
    features = StructuralFeatures(
        n_robot_stations=1,
        n_destinations=1,
        destination_kind="single_bin",
        uses_conveyor_transport=False,
    )
    planner_backend = _planner_backend_for_motion_backend(motion_backend)
    return LayoutSpec(
        intent=Intent(
            pattern_hint="pick_place",
            counts=counts,
            structural_features=features,
            structural_tags=[
                "user:mcp.floor_plan",
                "user:physics.full_scene",
                "user:robot.franka_panda",
                f"user:motion_backend.{motion_backend}",
            ],
        ),
        objects=objects,
        relations=relations,
        source=Source(
            modality="text",
            confidence=0.9,
            timestamp=datetime.now(timezone.utc),
            raw_input=description,
            metadata={"producer": "mcp_floorplan_tools.franka_physics_pick_scene"},
        ),
        parameters={
            "physics": {
                "enabled": True,
                "gravity_mps2": 9.81,
                "require_collision_api": True,
                "require_rigid_body_api_for_workpieces": True,
                "static_supports": ["Table", "DropBin"],
            },
            "controller": {
                "robot_path": "/World/Franka",
                "source_paths": [f"/World/PickObject_{index + 1}" for index in range(object_count)],
                "destination_path": "/World/DropBin",
                "motion_backend": motion_backend,
                "live_pick_controller": "setup_pick_place_controller",
                "live_target_source": _pick_place_target_source(motion_backend),
                "planner_backend": planner_backend,
                "articulation_controller": {
                    "enabled": True,
                    "path": "/World/IsaacAssistControllers/FrankaPickPlaceController",
                    "type": "isaacsim.core.nodes.IsaacArticulationController",
                    "robot_path": "/World/Franka",
                },
                "ros2_control_graph": {
                    "enabled": True,
                    "path": "/World/ROS2ControlGraph",
                    "runtime_profile": "isaacsim-6.0",
                    "node_namespace": "isaacsim.ros2.nodes",
                    "fallback_node_namespace": "isaacsim.ros2.bridge",
                    "joint_states_topic": "/isaac_joint_states",
                    "joint_commands_topic": "/isaac_joint_commands",
                    "controller_type": "joint_trajectory_controller",
                    "profile": "franka_moveit2",
                    "author_omnigraph": False,
                    "omnigraph_policy": "defer_until_live_probe_passes",
                    "connect_articulation_controller": False,
                    "connection_policy": "safe_bridge_until_live_probe_passes",
                },
            },
        },
    )


def _objects_from_description(description: str) -> List[TypedObject]:
    text = description.lower()
    classes: list[str] = []
    for token, object_class in _CLASS_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}s?\b", text) and object_class not in classes:
            classes.append(object_class)
    if not classes:
        classes = ["table_large", "cube"]
    return [
        _typed_object(object_class, index, len(classes))
        for index, object_class in enumerate(classes)
    ]


def _objects_from_explicit(items: Iterable[Any]) -> List[TypedObject]:
    objects: list[TypedObject] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        object_class = str(item.get("class") or item.get("object_class") or "").strip()
        if not object_class:
            continue
        object_class = _CLASS_ALIASES.get(object_class.lower(), object_class)
        obj = _typed_object(
            object_class,
            index,
            max(1, len(list(items)) if hasattr(items, "__len__") else index + 1),
            name=item.get("name"),
            x=item.get("x"),
            y=item.get("y"),
        )
        metadata = dict(obj.metadata or {})
        asset_ref = item.get("asset_ref") or item.get("asset_path")
        if asset_ref:
            metadata["reviewed_asset_ref"] = str(asset_ref)
            metadata["reviewed_asset_source"] = "mcp"
        objects.append(obj.model_copy(update={"metadata": metadata}))
    return objects


def _typed_object(
    object_class: str,
    index: int,
    total: int,
    *,
    name: Any = None,
    x: Any = None,
    y: Any = None,
    metadata: Dict[str, Any] | None = None,
    role_hint: str | None = None,
) -> TypedObject:
    width, height = _DEFAULT_SIZES.get(object_class, (0.25, 0.25))
    safe_name = _safe_name(str(name or f"{object_class}_{index + 1}"))
    spacing = 0.65
    px = _float_or(x, (index - (total - 1) / 2.0) * spacing)
    py = _float_or(y, 0.0)
    return TypedObject(
        id=safe_name.lower(),
        **{"class": object_class},
        name=safe_name,
        position=Position(x=px, y=py),
        rotation=0.0,
        size=Size(w=width, h=height),
        notes="Created from MCP floor-plan request.",
        notes_sensitive=False,
        metadata=metadata or {},
        role_hint=role_hint,
        locked=False,
        layer="mcp",
    )


def _relations_from_description(description: str, objects: List[TypedObject]) -> List[SpatialRelation]:
    relations: list[SpatialRelation] = []
    text = description.lower()
    for subject in objects:
        for parent in objects:
            if subject.id == parent.id:
                continue
            for relation, phrases in _RELATION_PATTERNS:
                for phrase in phrases:
                    subject_terms = _terms_for(subject)
                    parent_terms = _terms_for(parent)
                    if any(_contains_relation_phrase(text, s, phrase, p) for s in subject_terms for p in parent_terms):
                        relations.append(_relation(subject.id, relation, parent.id, "mcp_text"))
                        break
    return _dedupe_relations(relations)


def _relations_from_explicit(items: Iterable[Any], objects: List[TypedObject]) -> List[SpatialRelation]:
    by_name = {obj.name.lower(): obj.id for obj in objects}
    by_id = {obj.id.lower(): obj.id for obj in objects}
    relations: list[SpatialRelation] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or item.get("subject_id") or "").lower()
        parent = str(item.get("object") or item.get("object_id") or "").lower()
        relation = normalize_relation_kind(str(item.get("relation") or "").strip())
        subject_id = by_id.get(subject) or by_name.get(subject)
        object_id = by_id.get(parent) or by_name.get(parent)
        if subject_id and object_id and relation:
            relations.append(_relation(subject_id, relation, object_id, "mcp_explicit"))
    return relations


def _dedupe_relations(relations: List[SpatialRelation]) -> List[SpatialRelation]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[SpatialRelation] = []
    for rel in relations:
        key = (rel.subject_id, rel.relation, rel.object_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rel)
    return deduped


def _contains_relation_phrase(text: str, subject: str, phrase: str, parent: str) -> bool:
    pattern = (
        rf"\b{re.escape(subject)}\b\s+"
        rf"{re.escape(phrase)}\s+"
        rf"(?:the\s+|a\s+|an\s+)?"
        rf"\b{re.escape(parent)}\b"
    )
    return re.search(pattern, text) is not None


def _relation(subject_id: str, relation: str, object_id: str, source: str) -> SpatialRelation:
    return SpatialRelation(
        subject_id=subject_id,
        relation=relation,
        object_id=object_id,
        confidence=0.8,
        source=source,
        metadata={},
    )


def _terms_for(obj: TypedObject) -> List[str]:
    terms = {
        obj.name.lower().replace("_", " "),
        obj.object_class.lower().replace("_", " "),
        obj.object_class.lower().split("_")[0],
    }
    if obj.object_class == "franka_panda":
        terms.update({"franka", "robot", "arm"})
    if obj.object_class == "table_large":
        terms.update({"table", "counter"})
    return [term for term in terms if term]


def _pick_place_target_source(motion_backend: str) -> str:
    if motion_backend in {"curobo", "auto", "native", "spline"}:
        return motion_backend
    if motion_backend in {"cumotion", "moveit_ompl", "moveit_pilz"}:
        return "curobo"
    return "auto"


def _planner_backend_for_motion_backend(motion_backend: str) -> str:
    if motion_backend in {"cumotion", "moveit_ompl", "moveit_pilz", "curobo_v2"}:
        return motion_backend
    if motion_backend == "curobo":
        return "curobo_v2"
    return detect_planner_for_task("fast_pick_place", has_obstacles=True)


def _franka_controller_plan(
    *,
    motion_backend: str,
    object_count: int,
    generate_controller_code: bool = False,
) -> Dict[str, Any]:
    target_source = _pick_place_target_source(motion_backend)
    args = {
        "robot_path": "/World/Franka",
        "robot_family": "franka",
        "source_paths": [f"/World/PickObject_{index + 1}" for index in range(object_count)],
        "destination_path": "/World/DropBin",
        "target_source": target_source,
        "end_effector_link": "panda_hand",
        "gripper_joint_1": "panda_finger_joint1",
        "gripper_joint_2": "panda_finger_joint2",
        "gripper_open": 0.04,
        "gripper_close": 0.0,
        "approach_height": 0.12,
        "lift_height": 0.20,
        "drop_height": 0.18,
        "planning_obstacles": ["/World/Table", "/World/DropBin"],
        "require_upright": True,
    }
    controller_code = ""
    controller_error = ""
    if generate_controller_code:
        try:
            from .chat.tools.handlers.pick_place import _gen_setup_pick_place_controller

            controller_code = _gen_setup_pick_place_controller(args)
        except Exception as exc:  # pragma: no cover - defensive for optional deps
            controller_error = f"{type(exc).__name__}: {exc}"

    bridge_config = BridgeConfig(
        planner=_planner_backend_for_motion_backend(motion_backend),  # type: ignore[arg-type]
        planning_group="panda_arm",
        planning_time_s=1.0,
        collision_check_enabled=True,
    )
    bridge = MoveItCuMotionBridge(bridge_config, dry_run=True)
    bridge_issues = bridge.validate_config()
    bridge_plan = bridge.plan_to_pose(
        PoseGoal(
            frame_id="world",
            position=(0.45, 0.18, 0.90),
            orientation_xyzw=(1.0, 0.0, 0.0, 0.0),
        )
    )

    return {
        "motion_backend_requested": motion_backend,
        "live_controller_tool": "setup_pick_place_controller",
        "live_target_source": target_source,
        "controller_args": args,
        "controller_code_generated": bool(controller_code),
        "controller_code": controller_code,
        "controller_error": controller_error,
        "moveit_cumotion_bridge": {
            "planner": bridge_config.planner,
            "planning_group": bridge_config.planning_group,
            "dry_run_valid": not bridge_issues and bridge_plan.success,
            "validation_issues": bridge_issues,
            "mock_plan_points": len(bridge_plan.trajectory),
            "note": (
                "cuMotion/MoveIt execution is a bridge contract in this repo; "
                "the live Isaac pickup path uses setup_pick_place_controller."
            ),
        },
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ros2_package_name(project_name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", project_name.strip().lower())
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "isaac_assist_scene"
    if not name[0].isalpha():
        name = f"scene_{name}"
    if not name.endswith("_harness"):
        name = f"{name}_harness"
    return name


def _harness_project_root(workspace_root: Any, package_name: str) -> Path:
    if workspace_root:
        root = Path(str(workspace_root)).expanduser()
        if not root.is_absolute():
            root = _repo_root() / root
    else:
        root = _repo_root() / "workspace" / "ros2_harnesses"
    return root / package_name


def _ros2_environment_report() -> Dict[str, Any]:
    ros_distro = os.environ.get("ROS_DISTRO") or _detect_ros_distro_from_opt()
    ros2_bin = shutil.which("ros2")
    if not ros2_bin and ros_distro:
        candidate = Path("/opt/ros") / ros_distro / "bin" / "ros2"
        if candidate.exists():
            ros2_bin = str(candidate)

    ros2_help_ok = False
    ros2_error = ""
    if ros2_bin:
        try:
            result = subprocess.run(
                [ros2_bin, "--help"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
            )
            ros2_help_ok = result.returncode == 0
            if result.returncode != 0:
                ros2_error = (result.stderr or result.stdout or "").strip()[:500]
        except Exception as exc:  # pragma: no cover - depends on host ROS install
            ros2_error = f"{type(exc).__name__}: {exc}"

    return {
        "ros_distro": ros_distro,
        "ros2_bin": ros2_bin or "",
        "ros2_help_ok": ros2_help_ok,
        "ros2_error": ros2_error,
        "ament_prefix_path": os.environ.get("AMENT_PREFIX_PATH", ""),
        "ros_domain_id": os.environ.get("ROS_DOMAIN_ID", "0"),
        "rmw_implementation": os.environ.get("RMW_IMPLEMENTATION", ""),
    }


def _detect_ros_distro_from_opt() -> str:
    opt = Path("/opt/ros")
    if not opt.exists():
        return ""
    preferred = ("jazzy", "humble", "iron", "rolling")
    existing = {path.name for path in opt.iterdir() if path.is_dir()}
    for distro in preferred:
        if distro in existing:
            return distro
    return sorted(existing)[0] if existing else ""


def _harness_precheck_issues(runtime: Dict[str, Any], ros2: Dict[str, Any]) -> List[str]:
    issues: list[str] = []
    if not Path(str(runtime.get("extension_folder", ""))).is_absolute():
        extension_folder = _repo_root() / str(runtime.get("extension_folder", ""))
    else:
        extension_folder = Path(str(runtime.get("extension_folder", "")))
    if not extension_folder.exists():
        issues.append(f"Isaac Assist extension folder not found: {extension_folder}")
    if runtime.get("profile") == "isaacsim-6.0" and runtime.get("ros2_omnigraph_namespace") != "isaacsim.ros2.nodes":
        issues.append("Isaac Sim 6.0 harness requires isaacsim.ros2.nodes OmniGraph namespace")
    if not ros2.get("ros_distro"):
        issues.append("ROS_DISTRO is not set and no /opt/ros distro was detected")
    if not ros2.get("ros2_bin"):
        issues.append("ros2 executable not found; source ROS2 before running the generated harness")
    if not ros2.get("ament_prefix_path"):
        issues.append("AMENT_PREFIX_PATH is not set; source /opt/ros/<distro>/setup.bash before launching Isaac Sim")
    return issues


def _ros2_candidate_namespaces(primary: str) -> List[str]:
    namespaces: list[str] = []
    for namespace in (primary, "isaacsim.ros2.nodes", "isaacsim.ros2.bridge"):
        if namespace and namespace not in namespaces:
            namespaces.append(namespace)
    return namespaces


def _ros2_omnigraph_readonly_probe_code(
    candidate_namespaces: List[str],
    required_suffixes: List[str],
) -> str:
    payload = {
        "candidate_namespaces": candidate_namespaces,
        "required_suffixes": required_suffixes,
    }
    return f"""\
import json

_probe_payload = json.loads({json.dumps(payload, sort_keys=True)!r})
_result = {{
    "ok": False,
    "namespaces": {{}},
    "registered_ros2_nodes": [],
    "error": "",
}}
try:
    import omni.graph.core as og
    _registered = set(str(_node) for _node in og.get_registered_nodes())
    _result["registered_ros2_nodes"] = sorted(
        _node for _node in _registered if ".ros2." in _node
    )
    for _namespace in _probe_payload["candidate_namespaces"]:
        _result["namespaces"][_namespace] = {{
            _suffix: f"{{_namespace}}.{{_suffix}}" in _registered
            for _suffix in _probe_payload["required_suffixes"]
        }}
    _result["ok"] = True
except Exception as _exc:
    _result["error"] = f"{{type(_exc).__name__}}: {{_exc}}"
print("ISAAC_ASSIST_ROS2_OMNIGRAPH_PROBE=" + json.dumps(_result, sort_keys=True))
"""


def _ros2_omnigraph_creation_probe_code(
    *,
    candidate_namespaces: List[str],
    probe_path: str,
    cleanup: bool,
    probe_mode: str,
    dummy_target_path: str,
    target_prim_path: str,
    allow_when_playing: bool,
) -> str:
    subscribe_articulation_modes = {
        "subscribe_articulation_dummy_target_tick",
        "subscribe_articulation_real_target_tick",
    }
    action_node_defs: list[dict[str, str]] = []
    node_defs: list[dict[str, str]] = []
    connections: list[list[str]] = []
    joint_command_connections: list[list[str]] = []
    required_suffixes: list[str] = []
    required_absolute_node_types: list[str] = []
    set_pairs: list[list[str]] = []
    probe_dummy_target_path = ""
    tick_node_names: list[str] = []
    articulation_node_names: list[str] = []
    if probe_mode != "articulation_dummy_target":
        node_defs.append({"name": "ROS2Context", "suffix": "ROS2Context"})
        required_suffixes.append("ROS2Context")
    if probe_mode in {
        "context_pubsub_no_targets",
        "context_pubsub_dummy_target",
        "context_pubsub_dummy_target_tick",
    }:
        node_defs.extend([
            {"name": "PublishJointState", "suffix": "ROS2PublishJointState"},
            {"name": "SubscribeJointState", "suffix": "ROS2SubscribeJointState"},
        ])
        connections.extend([
            ["ROS2Context.outputs:context", "PublishJointState.inputs:context"],
            ["ROS2Context.outputs:context", "SubscribeJointState.inputs:context"],
        ])
        required_suffixes.extend([
            "ROS2PublishJointState",
            "ROS2SubscribeJointState",
        ])
    if probe_mode in subscribe_articulation_modes:
        node_defs.append({
            "name": "SubscribeJointState",
            "suffix": "ROS2SubscribeJointState",
        })
        connections.append([
            "ROS2Context.outputs:context",
            "SubscribeJointState.inputs:context",
        ])
        required_suffixes.append("ROS2SubscribeJointState")
    if probe_mode == "context_pubsub_dummy_target_tick":
        action_node_defs.append({
            "name": "OnPlaybackTick",
            "node_type": "omni.graph.action.OnPlaybackTick",
        })
        tick_node_names.append("OnPlaybackTick")
        connections.extend([
            ["OnPlaybackTick.outputs:tick", "PublishJointState.inputs:execIn"],
            ["OnPlaybackTick.outputs:tick", "SubscribeJointState.inputs:execIn"],
        ])
        required_absolute_node_types.append("omni.graph.action.OnPlaybackTick")
    if probe_mode in subscribe_articulation_modes:
        action_node_defs.append({
            "name": "OnPlaybackTick",
            "node_type": "omni.graph.action.OnPlaybackTick",
        })
        tick_node_names.append("OnPlaybackTick")
        connections.extend([
            ["OnPlaybackTick.outputs:tick", "SubscribeJointState.inputs:execIn"],
            ["OnPlaybackTick.outputs:tick", "ArticulationController.inputs:execIn"],
        ])
        required_absolute_node_types.append("omni.graph.action.OnPlaybackTick")
    if probe_mode in {"articulation_dummy_target", *subscribe_articulation_modes}:
        action_node_defs.append({
            "name": "ArticulationController",
            "node_type": "isaacsim.core.nodes.IsaacArticulationController",
        })
        articulation_node_names.append("ArticulationController")
        required_absolute_node_types.append(
            "isaacsim.core.nodes.IsaacArticulationController"
        )
    if probe_mode in subscribe_articulation_modes:
        joint_command_connections.extend([
            ["SubscribeJointState.outputs:jointNames", "ArticulationController.inputs:jointNames"],
            ["SubscribeJointState.outputs:positionCommand", "ArticulationController.inputs:positionCommand"],
            ["SubscribeJointState.outputs:velocityCommand", "ArticulationController.inputs:velocityCommand"],
            ["SubscribeJointState.outputs:effortCommand", "ArticulationController.inputs:effortCommand"],
        ])
        connections.extend(joint_command_connections)
    if probe_mode in {"context_pubsub_dummy_target", "context_pubsub_dummy_target_tick"}:
        probe_dummy_target_path = dummy_target_path
        set_pairs.extend([
            ["PublishJointState.inputs:targetPrim", dummy_target_path],
            ["PublishJointState.inputs:topicName", "/isaac_assist_probe/joint_states"],
            ["SubscribeJointState.inputs:topicName", "/isaac_assist_probe/joint_commands"],
        ])
    if probe_mode == "articulation_dummy_target":
        probe_dummy_target_path = dummy_target_path
        set_pairs.append(["ArticulationController.inputs:targetPrim", dummy_target_path])
    if probe_mode == "subscribe_articulation_dummy_target_tick":
        probe_dummy_target_path = dummy_target_path
        set_pairs.extend([
            ["SubscribeJointState.inputs:topicName", "/isaac_assist_probe/joint_commands"],
            ["ArticulationController.inputs:targetPrim", dummy_target_path],
        ])
    if probe_mode == "subscribe_articulation_real_target_tick":
        set_pairs.extend([
            ["SubscribeJointState.inputs:topicName", "/isaac_assist_probe/joint_commands"],
            ["ArticulationController.inputs:targetPrim", target_prim_path],
        ])
    payload = {
        "candidate_namespaces": candidate_namespaces,
        "probe_path": probe_path,
        "cleanup": cleanup,
        "probe_mode": probe_mode,
        "dummy_target_path": probe_dummy_target_path,
        "target_prim_path": target_prim_path,
        "allow_when_playing": allow_when_playing,
        "action_node_defs": action_node_defs,
        "node_defs": node_defs,
        "connections": connections,
        "joint_command_connections": joint_command_connections,
        "tick_node_names": tick_node_names,
        "articulation_node_names": articulation_node_names,
        "required_suffixes": required_suffixes,
        "required_absolute_node_types": required_absolute_node_types,
        "set_pairs": set_pairs,
    }
    return f"""\
import json

_probe_payload = json.loads({json.dumps(payload, sort_keys=True)!r})
_result = {{
    "ok": False,
    "status": "pending",
    "detected_namespace": "",
    "probe_path": _probe_payload["probe_path"],
    "created": False,
    "removed": False,
    "cleanup": bool(_probe_payload["cleanup"]),
    "probe_mode": _probe_payload["probe_mode"],
    "dummy_target_path": _probe_payload["dummy_target_path"],
    "dummy_target_created": False,
    "dummy_target_removed": False,
    "target_prim_path": _probe_payload["target_prim_path"],
    "target_prim_exists": False,
    "target_prim_type_name": "",
    "stage_identifier": "",
    "stage_real_path": "",
    "timeline_playing": False,
    "tick_wiring_created": False,
    "articulation_controller_created": False,
    "joint_command_outputs_connected": False,
    "joint_command_connection_paths": _probe_payload["joint_command_connections"],
    "created_node_names": [],
    "created_node_suffixes": [],
    "missing_node_suffixes": [],
    "missing_absolute_node_types": [],
    "set_attrs_ok": [],
    "set_attrs_failed": [],
    "error": "",
}}
_parent_created = False
_target_path = None
try:
    from pxr import Sdf
    import omni.usd
    import omni.graph.core as og
    import omni.timeline

    _stage = omni.usd.get_context().get_stage()
    if _stage is None:
        raise RuntimeError("No active USD stage")
    _root_layer = _stage.GetRootLayer()
    _result["stage_identifier"] = str(getattr(_root_layer, "identifier", "") or "")
    _result["stage_real_path"] = str(getattr(_root_layer, "realPath", "") or "")
    _timeline = omni.timeline.get_timeline_interface()
    _result["timeline_playing"] = bool(_timeline and _timeline.is_playing())
    if _probe_payload["target_prim_path"]:
        _target_prim = _stage.GetPrimAtPath(_probe_payload["target_prim_path"])
        _result["target_prim_exists"] = bool(_target_prim and _target_prim.IsValid())
        if _result["target_prim_exists"]:
            _result["target_prim_type_name"] = str(_target_prim.GetTypeName())

    _registered = set(str(_node) for _node in og.get_registered_nodes())
    _required_suffixes = list(_probe_payload["required_suffixes"])
    _requires_ros2_namespace = bool(_required_suffixes)
    _missing_absolute = [
        _node_type for _node_type in _probe_payload["required_absolute_node_types"]
        if _node_type not in _registered
    ]
    _result["missing_absolute_node_types"] = _missing_absolute
    _ros2_ns = ""
    _missing = []
    if _requires_ros2_namespace:
        _missing = list(_required_suffixes)
        for _namespace in _probe_payload["candidate_namespaces"]:
            _candidate_missing = [
                _suffix for _suffix in _required_suffixes
                if f"{{_namespace}}.{{_suffix}}" not in _registered
            ]
            if not _candidate_missing:
                _ros2_ns = _namespace
                _missing = []
                break
            if len(_candidate_missing) < len(_missing):
                _missing = _candidate_missing
    _result["detected_namespace"] = _ros2_ns
    _result["missing_node_suffixes"] = _missing
    if _probe_payload["target_prim_path"] and not _result["target_prim_exists"]:
        _result["status"] = "target_missing"
    elif (
        _probe_payload["tick_node_names"]
        and _result["timeline_playing"]
        and not _probe_payload["allow_when_playing"]
    ):
        _result["status"] = "timeline_playing"
    elif _missing_absolute:
        _result["status"] = "missing_required_nodes"
    elif _requires_ros2_namespace and not _ros2_ns:
        _result["status"] = "missing_required_nodes"
    else:
        _keys = og.Controller.Keys
        _graph_path = _probe_payload["probe_path"]
        _path = Sdf.Path(_graph_path)
        _parent = _path.GetParentPath()
        if str(_parent) not in ("", "/") and not _stage.GetPrimAtPath(_parent).IsValid():
            _stage.DefinePrim(_parent, "Scope")
            _parent_created = True
        if _probe_payload["dummy_target_path"]:
            _target_path = Sdf.Path(_probe_payload["dummy_target_path"])
            if _stage.GetPrimAtPath(_target_path).IsValid():
                _stage.RemovePrim(_target_path)
            _stage.DefinePrim(_target_path, "Xform")
            _target_prim = _stage.GetPrimAtPath(_target_path)
            _result["dummy_target_created"] = bool(
                _target_prim and _target_prim.IsValid()
            )
        if _stage.GetPrimAtPath(_path).IsValid():
            _stage.RemovePrim(_path)
        _create_nodes = [
            (_node_def["name"], _node_def["node_type"])
            for _node_def in _probe_payload["action_node_defs"]
        ]
        if _requires_ros2_namespace:
            _create_nodes.extend([
                (_node_def["name"], f"{{_ros2_ns}}.{{_node_def['suffix']}}")
                for _node_def in _probe_payload["node_defs"]
            ])
        _connections = [
            tuple(_connection) for _connection in _probe_payload["connections"]
        ]
        _edit_request = {{
            _keys.CREATE_NODES: _create_nodes,
        }}
        if _connections:
            _edit_request[_keys.CONNECT] = _connections
        og.Controller.edit(
            _graph_path,
            _edit_request,
        )
        _graph_prim = _stage.GetPrimAtPath(_path)
        _created_node_names = []
        _created_suffixes = []
        for _node_name, _node_type in _create_nodes:
            _node_prim = _stage.GetPrimAtPath(Sdf.Path(f"{{_graph_path}}/{{_node_name}}"))
            if _node_prim and _node_prim.IsValid():
                _created_node_names.append(str(_node_name))
                _created_suffixes.append(str(_node_type).split(".")[-1])
        _result["created_node_names"] = _created_node_names
        _result["created_node_suffixes"] = _created_suffixes
        _result["tick_wiring_created"] = bool(
            _probe_payload["tick_node_names"]
            and all(_node_name in _created_node_names for _node_name in _probe_payload["tick_node_names"])
        )
        _result["articulation_controller_created"] = bool(
            _probe_payload["articulation_node_names"]
            and all(
                _node_name in _created_node_names
                for _node_name in _probe_payload["articulation_node_names"]
            )
        )
        for _attr_path, _attr_value in _probe_payload["set_pairs"]:
            _full_attr_path = f"{{_graph_path}}/{{_attr_path}}"
            try:
                og.Controller.attribute(_full_attr_path).set(_attr_value)
                _result["set_attrs_ok"].append(_attr_path)
            except Exception as _attr_exc:
                _result["set_attrs_failed"].append([
                    _attr_path,
                    f"{{type(_attr_exc).__name__}}: {{_attr_exc}}",
                ])
        _set_attrs_ok = (
            len(_result["set_attrs_ok"]) == len(_probe_payload["set_pairs"])
        )
        _dummy_target_ok = (
            not _probe_payload["dummy_target_path"] or _result["dummy_target_created"]
        )
        _namespaced_nodes_ok = all(
            _suffix in _created_suffixes for _suffix in _required_suffixes
        )
        _absolute_nodes_ok = all(
            str(_node_type).split(".")[-1] in _created_suffixes
            for _node_type in _probe_payload["required_absolute_node_types"]
        )
        _result["created"] = bool(
            _graph_prim and _graph_prim.IsValid()
            and _namespaced_nodes_ok
            and _absolute_nodes_ok
            and _set_attrs_ok
            and _dummy_target_ok
        )
        _result["joint_command_outputs_connected"] = bool(
            _result["created"] and _probe_payload["joint_command_connections"]
        )
        _result["status"] = "created" if _result["created"] else "create_failed"
        if _probe_payload["cleanup"]:
            _stage.RemovePrim(_path)
            if _target_path is not None:
                _stage.RemovePrim(_target_path)
                _result["dummy_target_removed"] = not _stage.GetPrimAtPath(_target_path).IsValid()
            if _parent_created:
                _stage.RemovePrim(_parent)
            _result["removed"] = not _stage.GetPrimAtPath(_path).IsValid()
        _result["ok"] = bool(
            _result["created"] and (
                not _probe_payload["cleanup"] or _result["removed"]
            )
        )
except Exception as _exc:
    _result["status"] = "exception"
    _result["error"] = f"{{type(_exc).__name__}}: {{_exc}}"
print("ISAAC_ASSIST_ROS2_OMNIGRAPH_CREATION_PROBE=" + json.dumps(_result, sort_keys=True))
"""


def _extract_probe_json(output: str) -> Dict[str, Any]:
    marker = "ISAAC_ASSIST_ROS2_OMNIGRAPH_PROBE="
    for line in reversed(output.splitlines()):
        if marker not in line:
            continue
        try:
            return json.loads(line.split(marker, 1)[1].strip())
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_creation_probe_json(output: str) -> Dict[str, Any]:
    marker = "ISAAC_ASSIST_ROS2_OMNIGRAPH_CREATION_PROBE="
    for line in reversed(output.splitlines()):
        if marker not in line:
            continue
        try:
            return json.loads(line.split(marker, 1)[1].strip())
        except json.JSONDecodeError:
            return {}
    return {}


def _write_ros2_harness_project(
    *,
    project_root: Path,
    package_name: str,
    project_name: str,
    session_id: str,
    scenario: str,
    description: str,
    runtime: Dict[str, Any],
    ros2: Dict[str, Any],
    precheck_issues: List[str],
    controller: Dict[str, Any],
    scene: Dict[str, Any],
    build_response: Dict[str, Any],
    launch_response: Dict[str, Any],
    omnigraph_probe: Dict[str, Any],
) -> List[Dict[str, Any]]:
    src_root = project_root / "src" / package_name
    package_dir = src_root / package_name
    config_dir = src_root / "config"
    launch_dir = src_root / "launch"
    resource_dir = src_root / "resource"
    generated_dir = project_root / "generated"
    for path in (package_dir, config_dir, launch_dir, resource_dir, generated_dir):
        path.mkdir(parents=True, exist_ok=True)

    contract = _scene_contract_payload(
        project_name=project_name,
        package_name=package_name,
        session_id=session_id,
        scenario=scenario,
        description=description,
        runtime=runtime,
        ros2=ros2,
        precheck_issues=precheck_issues,
        controller=controller,
        scene=scene,
        build_response=build_response,
        launch_response=launch_response,
        omnigraph_probe=omnigraph_probe,
    )
    written: list[Path] = []

    def write(path: Path, text: str, executable: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if executable:
            path.chmod(0o755)
        written.append(path)

    write(src_root / "package.xml", _package_xml(package_name, project_name))
    write(src_root / "setup.py", _setup_py(package_name))
    write(resource_dir / package_name, "")
    write(package_dir / "__init__.py", "")
    write(package_dir / "warehouse_pick_place_node.py", _warehouse_pick_place_node_py())
    write(launch_dir / "warehouse_pick_place.launch.py", _warehouse_launch_py(package_name))
    write(config_dir / "scene_contract.json", json.dumps(contract, indent=2, sort_keys=True) + "\n")
    write(config_dir / "ros2_control.yaml", _ros2_control_yaml(controller))
    write(project_root / "README.md", _harness_readme(project_name, package_name, contract))
    write(project_root / "run_harness.sh", _run_harness_sh(package_name), executable=True)

    generated_code = (
        (build_response.get("instantiation") or {}).get("generated_code")
        if isinstance(build_response, dict)
        else None
    )
    if generated_code:
        write(generated_dir / "scene_setup.py", str(generated_code))

    return [
        {
            "path": str(path),
            "relative_path": str(path.relative_to(project_root)),
        }
        for path in written
    ]


def _scene_contract_payload(
    *,
    project_name: str,
    package_name: str,
    session_id: str,
    scenario: str,
    description: str,
    runtime: Dict[str, Any],
    ros2: Dict[str, Any],
    precheck_issues: List[str],
    controller: Dict[str, Any],
    scene: Dict[str, Any],
    build_response: Dict[str, Any],
    launch_response: Dict[str, Any],
    omnigraph_probe: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schema_version": "isaac_assist.ros2_scene_harness.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project_name,
        "package_name": package_name,
        "session_id": session_id,
        "scenario": scenario,
        "description": description,
        "runtime": runtime,
        "ros2": ros2,
        "precheck": {
            "ok": not precheck_issues,
            "issues": precheck_issues,
        },
        "scene": {
            "created_from": scene.get("created_from"),
            "revision": scene.get("revision"),
            "summary": scene.get("summary"),
            "relation_diagnostics": scene.get("relation_diagnostics", []),
        },
        "controller": controller,
        "ros2_omnigraph_probe": _compact_probe_response(omnigraph_probe),
        "build": _compact_build_response(build_response),
        "launch": _compact_launch_response(launch_response),
    }


def _compact_build_response(build_response: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(build_response, dict):
        return {"status": "unknown"}
    instantiation = build_response.get("instantiation") or {}
    return {
        "status": build_response.get("status"),
        "ratified": build_response.get("ratified"),
        "revision": build_response.get("revision"),
        "diagnostics": build_response.get("diagnostics", []),
        "errors": build_response.get("errors", []),
        "instantiation": {
            "status": instantiation.get("status"),
            "message": instantiation.get("message"),
            "dry_run": instantiation.get("dry_run"),
            "has_generated_code": bool(instantiation.get("generated_code")),
            "relation_verification": instantiation.get("relation_verification"),
            "variant_summary": instantiation.get("variant_summary"),
        } if isinstance(instantiation, dict) else {},
    }


def _compact_launch_response(launch_response: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(launch_response, dict):
        return {"status": "unknown"}
    return {
        key: value
        for key, value in launch_response.items()
        if key in {"status", "dry_run", "command", "usd_path", "workspace_root", "variant_id", "pid", "log_path"}
    }


def _compact_probe_response(probe_response: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(probe_response, dict):
        return {"status": "unknown"}
    readback = probe_response.get("readback") if isinstance(probe_response.get("readback"), dict) else {}
    registered_nodes = readback.get("registered_ros2_nodes") if isinstance(readback, dict) else []
    compact: Dict[str, Any] = {
        "status": probe_response.get("status"),
        "dry_run": probe_response.get("dry_run"),
        "read_only": probe_response.get("read_only", True),
        "graph_authoring_tested": probe_response.get("graph_authoring_tested", False),
        "candidate_namespaces": probe_response.get("candidate_namespaces", []),
        "required_node_suffixes": probe_response.get("required_node_suffixes", []),
        "matching_namespaces": probe_response.get("matching_namespaces", []),
        "detected_namespace": probe_response.get("detected_namespace", ""),
        "recommendation": probe_response.get("recommendation", {}),
    }
    if isinstance(readback, dict) and readback:
        compact["readback"] = {
            "ok": readback.get("ok"),
            "namespaces": readback.get("namespaces", {}),
            "registered_ros2_node_count": len(registered_nodes or []),
            "error": readback.get("error", ""),
        }
    kit_rpc = probe_response.get("kit_rpc")
    if isinstance(kit_rpc, dict):
        compact["kit_rpc"] = {"success": bool(kit_rpc.get("success"))}
    if probe_response.get("error"):
        compact["error"] = probe_response.get("error")
    return compact


def _package_xml(package_name: str, project_name: str) -> str:
    return f"""<?xml version="1.0"?>
<package format="3">
  <name>{package_name}</name>
  <version>0.1.0</version>
  <description>ROS2 harness for {project_name} generated by Isaac Assist.</description>
  <maintainer email="user@example.com">Isaac Assist</maintainer>
  <license>Apache-2.0</license>
  <buildtool_depend>ament_python</buildtool_depend>
  <exec_depend>geometry_msgs</exec_depend>
  <exec_depend>rclpy</exec_depend>
  <exec_depend>sensor_msgs</exec_depend>
  <exec_depend>std_msgs</exec_depend>
  <exec_depend>trajectory_msgs</exec_depend>
  <test_depend>ament_lint_auto</test_depend>
  <test_depend>ament_lint_common</test_depend>
  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
"""


def _setup_py(package_name: str) -> str:
    return f"""from setuptools import find_packages, setup

package_name = {package_name!r}

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", ["config/scene_contract.json", "config/ros2_control.yaml"]),
        ("share/" + package_name + "/launch", ["launch/warehouse_pick_place.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Isaac Assist",
    maintainer_email="user@example.com",
    description="Project-local ROS2 harness generated by Isaac Assist.",
    license="Apache-2.0",
    entry_points={{
        "console_scripts": [
            "warehouse_pick_place_node = {package_name}.warehouse_pick_place_node:main",
        ],
    }},
)
"""


def _warehouse_launch_py(package_name: str) -> str:
    return f"""import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    pkg_share = get_package_share_directory({package_name!r})
    contract_path = os.path.join(pkg_share, "config", "scene_contract.json")
    return LaunchDescription([
        Node(
            package={package_name!r},
            executable="warehouse_pick_place_node",
            name="warehouse_pick_place_node",
            output="screen",
            parameters=[{{"contract_path": contract_path}}],
        ),
    ])
"""


def _warehouse_pick_place_node_py() -> str:
    return '''import json
from pathlib import Path

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32


PANDA_JOINTS = [
    "panda_joint1",
    "panda_joint2",
    "panda_joint3",
    "panda_joint4",
    "panda_joint5",
    "panda_joint6",
    "panda_joint7",
]


class WarehousePickPlaceNode(Node):
    """ROS2 side of an Isaac Assist pick-place harness.

    This node is intentionally small: it publishes a deterministic command
    sequence on the topics described by scene_contract.json. Planner-backed
    projects can replace _waypoints_from_contract with MoveIt/cuMotion output
    while keeping the package and launch shape unchanged.
    """

    def __init__(self):
        super().__init__("warehouse_pick_place_node")
        self.declare_parameter("contract_path", "")
        contract_path = self.get_parameter("contract_path").get_parameter_value().string_value
        self.contract = self._load_contract(contract_path)
        controller = self.contract.get("controller", {})
        graph = controller.get("ros2_control_graph", {})
        self.command_topic = graph.get("joint_commands_topic", "/isaac_joint_commands")
        self.state_topic = graph.get("joint_states_topic", "/isaac_joint_states")
        self.gripper_topic = controller.get("gripper_topic", "/isaac/robot/gripper_cmd")
        self.command_pub = self.create_publisher(JointState, self.command_topic, 10)
        self.gripper_pub = self.create_publisher(Float32, self.gripper_topic, 10)
        self.state_sub = self.create_subscription(JointState, self.state_topic, self._on_joint_state, 10)
        self.latest_state = None
        self.step_index = 0
        self.waypoints = self._waypoints_from_contract()
        self.timer = self.create_timer(1.0, self._tick)
        self.get_logger().info(
            f"Publishing Franka harness commands to {self.command_topic}; listening on {self.state_topic}"
        )

    def _load_contract(self, contract_path):
        if not contract_path:
            return {}
        path = Path(contract_path)
        if not path.exists():
            self.get_logger().warning(f"scene contract not found: {path}")
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _waypoints_from_contract(self):
        return [
            ("home_open", [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785], 0.04),
            ("pre_pick", [0.15, -0.65, 0.05, -2.15, 0.0, 1.55, 0.70], 0.04),
            ("grasp", [0.20, -0.50, 0.08, -2.05, 0.0, 1.45, 0.65], 0.0),
            ("lift", [0.10, -0.85, 0.10, -2.25, 0.0, 1.65, 0.75], 0.0),
            ("pre_drop", [-0.35, -0.60, -0.05, -2.00, 0.0, 1.45, 0.55], 0.0),
            ("release", [-0.35, -0.60, -0.05, -2.00, 0.0, 1.45, 0.55], 0.04),
            ("retreat", [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785], 0.04),
        ]

    def _on_joint_state(self, msg):
        self.latest_state = msg

    def _tick(self):
        label, positions, gripper = self.waypoints[self.step_index]
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = list(PANDA_JOINTS)
        msg.position = [float(value) for value in positions]
        self.command_pub.publish(msg)
        grip = Float32()
        grip.data = float(gripper)
        self.gripper_pub.publish(grip)
        self.get_logger().info(f"sent {label}: gripper={gripper:.3f}")
        self.step_index = (self.step_index + 1) % len(self.waypoints)


def main(args=None):
    rclpy.init(args=args)
    node = WarehousePickPlaceNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
'''


def _ros2_control_yaml(controller: Dict[str, Any]) -> str:
    graph = controller.get("ros2_control_graph", {}) if isinstance(controller, dict) else {}
    joint_states_topic = graph.get("joint_states_topic", "/isaac_joint_states")
    joint_commands_topic = graph.get("joint_commands_topic", "/isaac_joint_commands")
    controller_type = graph.get("controller_type", "joint_trajectory_controller")
    return f"""controller_manager:
  ros__parameters:
    update_rate: 100

    joint_state_broadcaster:
      type: joint_state_broadcaster/JointStateBroadcaster

    {controller_type}:
      type: joint_trajectory_controller/JointTrajectoryController

{controller_type}:
  ros__parameters:
    joints:
      - panda_joint1
      - panda_joint2
      - panda_joint3
      - panda_joint4
      - panda_joint5
      - panda_joint6
      - panda_joint7
    command_interfaces:
      - position
    state_interfaces:
      - position
      - velocity
    state_publish_rate: 100
    action_monitor_rate: 20

# The generated Isaac Assist scene publishes joint states on {joint_states_topic}
# and subscribes to joint commands on {joint_commands_topic}.
"""


def _harness_readme(project_name: str, package_name: str, contract: Dict[str, Any]) -> str:
    distro = contract.get("ros2", {}).get("ros_distro") or "jazzy"
    issues = contract.get("precheck", {}).get("issues", [])
    issue_text = "\n".join(f"- {issue}" for issue in issues) if issues else "- none"
    graph = contract.get("controller", {}).get("ros2_control_graph", {})
    probe = contract.get("ros2_omnigraph_probe", {})
    probe_status = probe.get("status", "unknown")
    probe_namespace = probe.get("detected_namespace") or "not detected"
    probe_recommendation = probe.get("recommendation", {}).get(
        "reason",
        "Run the compatibility probe before enabling live graph authoring.",
    )
    return f"""# {project_name}

Generated Isaac Assist ROS2 scene harness.

## Runtime

- Isaac profile: {contract.get("runtime", {}).get("profile")}
- Isaac Sim: {contract.get("runtime", {}).get("isaac_sim_version")}
- ROS2 distro: {distro}
- ROS2 command topic: {graph.get("joint_commands_topic", "/isaac_joint_commands")}
- ROS2 state topic: {graph.get("joint_states_topic", "/isaac_joint_states")}
- ROS2 OmniGraph authoring: {graph.get("omnigraph_policy", "defer_until_live_probe_passes")}
- ROS2 OmniGraph probe: {probe_status}; detected namespace: {probe_namespace}

## Precheck

{issue_text}

## Build And Run

```bash
source /opt/ros/{distro}/setup.bash
colcon build --symlink-install
source install/setup.bash
ros2 launch {package_name} warehouse_pick_place.launch.py
```

The ROS2 node publishes a deterministic Franka command sequence for smoke
testing the bridge. Replace the waypoint generator with MoveIt/cuMotion output
when the live planner is available.

Probe recommendation: {probe_recommendation}

Run the `probe_ros2_omnigraph_compatibility` MCP tool before enabling live
OmniGraph authoring. Then run `probe_ros2_omnigraph_creation` with
`dry_run=false` to confirm this Isaac build can create and remove a disposable
ROS2Context graph before connecting ROS2 commands to the articulation controller.
"""


def _run_harness_sh(package_name: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "${{ROS_DISTRO:-}}" ]]; then
    echo "ROS_DISTRO is not set. Source /opt/ros/<distro>/setup.bash first." >&2
    exit 2
fi
colcon build --symlink-install
source install/setup.bash
ros2 launch {package_name} warehouse_pick_place.launch.py
"""


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or not name[0].isalpha():
        name = f"Obj_{name or '1'}"
    return name


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _asset_payload(objects: Iterable[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "object_id": item.object_id,
            "object_class": item.object_class,
            "usd_ref": item.usd_ref,
            "source": item.source,
            "needs_review": item.needs_review,
        }
        for item in resolve_layout_assets(objects)
    ]


def _session_payload(session_id: str, spec: LayoutSpec, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = {
        "session_id": session_id,
        "revision": spec.revision,
        "spec": spec.model_dump(mode="json", by_alias=True),
        "summary": {
            "objects": len(spec.objects or []),
            "relations": len(spec.relations or []),
            "pattern": spec.intent.pattern_hint,
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _require_spec(session_id: str) -> LayoutSpec:
    spec = get_store().get_latest(session_id)
    if spec is None:
        raise ValueError(f"no LayoutSpec found for session {session_id!r}")
    return spec


def _required_str(arguments: Dict[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _validate_base64(value: str) -> None:
    raw = value.split(",", 1)[1] if value.lstrip().startswith("data:") and "," in value else value
    base64.b64decode(raw, validate=True)
