"""MCP-facing floor-plan tools for external chat clients.

These helpers give MCP clients a stable semantic surface over Isaac Sim:
create or inspect a LayoutSpec, choose local USD assets, build dry-run Kit
code, launch a materialized scene variant, and verify spatial relations.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
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
                    "joint_states_topic": "/isaac_joint_states",
                    "joint_commands_topic": "/isaac_joint_commands",
                    "controller_type": "joint_trajectory_controller",
                    "profile": "franka_moveit2",
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
