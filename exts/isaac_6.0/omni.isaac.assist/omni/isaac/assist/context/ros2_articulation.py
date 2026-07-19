"""Scene-attached ROS 2 joint control for any single Isaac articulation.

The graph is authored into the USD session layer. It follows the robot into
arbitrary environment stages without modifying the robot or scene on disk.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any


DEFAULT_GRAPH_PATH = "/HomeHeroROS2ArticulationBridge"
DEFAULT_COMMAND_TOPIC = "joint_commands"
DEFAULT_STATE_TOPIC = "joint_states"

_state: dict[str, Any] = {"configured": False, "reason": "not_started"}
_stage_identity: int | None = None
_auto_attach = None


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_").lower()
    if not value:
        return "robot"
    if value[0].isdigit():
        value = f"robot_{value}"
    return value


def _namespace_for(robot_root: str) -> str:
    override = os.environ.get("HOMEHERO_ROS2_NAMESPACE", "").strip()
    if override:
        return "/" + override.strip("/")

    components = [component for component in robot_root.split("/") if component]
    # Keep the external API stable across Amber revision/hand variant names.
    if any(component.lower().startswith("amber") for component in components):
        return "/sim/amber"
    return f"/sim/{_safe_name(components[0] if components else 'robot')}"


def _is_within(path: str, root: str) -> bool:
    normalized = root.rstrip("/") or "/"
    return path == normalized or path.startswith(normalized + "/")


def _discover(stage, robot_root: str | None) -> tuple[str, str]:
    from pxr import UsdPhysics

    scope = None
    if robot_root:
        scope = robot_root if robot_root.startswith("/") else f"/{robot_root}"
        if not stage.GetPrimAtPath(scope).IsValid():
            raise RuntimeError(f"Robot root does not exist: {scope}")

    articulation_roots = [
        str(prim.GetPath())
        for prim in stage.Traverse()
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI)
        and (scope is None or _is_within(str(prim.GetPath()), scope))
    ]
    if len(articulation_roots) != 1:
        where = f" below {scope}" if scope else " in the stage"
        raise RuntimeError(
            f"Expected exactly one articulation{where}; found {len(articulation_roots)}: "
            f"{articulation_roots}"
        )

    articulation_root = articulation_roots[0]
    if scope:
        logical_root = scope
    else:
        components = [component for component in articulation_root.split("/") if component]
        logical_root = f"/{components[0]}" if components else articulation_root
    return logical_root, articulation_root


def _command_safety(stage, logical_root: str, articulation_root: str) -> dict[str, Any]:
    """Return structural blockers for accepting ROS motion commands."""
    import math
    from pxr import PhysxSchema, Usd, UsdPhysics

    blockers = []
    root_prim = stage.GetPrimAtPath(articulation_root)
    self_collision_values = []
    newton_self_collision = root_prim.GetAttribute("newton:selfCollisionEnabled")
    if newton_self_collision.IsValid() and newton_self_collision.HasAuthoredValueOpinion():
        self_collision_values.append(bool(newton_self_collision.Get()))
    physx_articulation = PhysxSchema.PhysxArticulationAPI.Get(stage, root_prim.GetPath())
    if physx_articulation:
        physx_self_collision = physx_articulation.GetEnabledSelfCollisionsAttr()
        if physx_self_collision.HasAuthoredValueOpinion():
            self_collision_values.append(bool(physx_self_collision.Get()))
    if not self_collision_values or not all(self_collision_values):
        blockers.append("articulation self-collision is not enabled for every authored backend")

    initial_pose_profile = root_prim.GetAttribute("homehero:initialPoseProfile").Get()
    qualified = root_prim.GetAttribute("homehero:safetyQualified").Get()
    qualified_profile = root_prim.GetAttribute("homehero:safetyQualifiedProfile").Get()
    if not initial_pose_profile:
        blockers.append("no versioned startup-pose profile is authored")
    if qualified is not True:
        blockers.append("asset has not passed the live-physics command-safety gate")
    if initial_pose_profile and qualified_profile != initial_pose_profile:
        blockers.append("live-physics qualification does not match the startup-pose profile")

    rigid_bodies = set()
    collision_owners = set()
    placeholder_limits = []
    initial_pose = {
        "amber_left_upper_arm_roll_joint": math.pi / 2.0,
        "amber_left_elbow_pitch_joint": -math.pi / 2.0,
        "amber_right_upper_arm_roll_joint": math.pi / 2.0,
        "amber_right_elbow_pitch_joint": -math.pi / 2.0,
    }
    initial_pose_mismatches = []
    initial_pose_names = set()
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        path = str(prim.GetPath())
        if not _is_within(path, logical_root):
            continue
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_bodies.add(path)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            enabled = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
            if enabled is not False:
                owner = prim
                while owner.IsValid() and not owner.HasAPI(UsdPhysics.RigidBodyAPI):
                    owner = owner.GetParent()
                if owner.IsValid():
                    collision_owners.add(str(owner.GetPath()))
        if prim.IsA(UsdPhysics.RevoluteJoint) and any(
            token in prim.GetName()
            for token in ("shoulder", "upper_arm", "elbow", "forearm", "wrist")
        ):
            lower = prim.GetAttribute("physics:lowerLimit").Get()
            upper = prim.GetAttribute("physics:upperLimit").Get()
            if lower is None or upper is None or float(upper) - float(lower) >= 359.9:
                placeholder_limits.append(prim.GetName())
        if prim.GetName() in initial_pose:
            initial_pose_names.add(prim.GetName())
            actual_value = prim.GetAttribute("homehero:initialPosition").Get()
            actual = float(actual_value) if actual_value is not None else None
            if actual is None or not math.isclose(
                actual, initial_pose[prim.GetName()], rel_tol=1e-6, abs_tol=1e-6
            ):
                initial_pose_mismatches.append(prim.GetName())

    if any("amber" in component.lower() for component in logical_root.split("/")):
        initial_pose_mismatches.extend(sorted(set(initial_pose) - initial_pose_names))

    collisionless = sorted(rigid_bodies - collision_owners)
    if not rigid_bodies:
        blockers.append("no rigid bodies found below robot root")
    if collisionless:
        blockers.append(f"rigid bodies without enabled collision shapes: {collisionless[:5]}")
    if placeholder_limits:
        blockers.append(f"placeholder 360-degree arm limits: {sorted(placeholder_limits)}")
    if initial_pose_mismatches:
        blockers.append(f"outboard startup pose is not authored: {sorted(initial_pose_mismatches)}")
    return {
        "passed": not blockers,
        "blockers": blockers,
        "rigid_body_count": len(rigid_bodies),
        "collision_owner_count": len(collision_owners),
        "initial_pose_profile": initial_pose_profile,
        "safety_qualified": qualified is True,
        "safety_qualified_profile": qualified_profile,
    }


def get_ros2_articulation_state() -> dict[str, Any]:
    return dict(_state)


def configure_ros2_articulation(
    robot_root: str | None = None,
    namespace: str | None = None,
    graph_path: str = DEFAULT_GRAPH_PATH,
    publish_clock: bool = True,
    command_enabled: bool | None = None,
) -> dict[str, Any]:
    """Discover an articulation and attach ROS 2 command/feedback nodes."""

    global _stage_identity, _state

    import omni.graph.core as og
    import omni.usd
    import usdrt
    from pxr import Usd

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("Isaac Sim has no open USD stage")

    logical_root, articulation_root = _discover(stage, robot_root)
    namespace = "/" + (namespace or _namespace_for(logical_root)).strip("/")
    graph_path = "/" + graph_path.strip("/")
    command_requested = (
        os.environ.get("HOMEHERO_ENABLE_ROS2_COMMANDS", "").strip() == "1"
        if command_enabled is None
        else bool(command_enabled)
    )
    command_safety = _command_safety(stage, logical_root, articulation_root)
    commands_active = command_requested and command_safety["passed"]

    same_graph = (
        _stage_identity == id(stage)
        and _state.get("configured")
        and _state.get("graph_path") == graph_path
        and _state.get("articulation_root") == articulation_root
        and _state.get("namespace") == namespace
        and _state.get("command_enabled") == commands_active
        and stage.GetPrimAtPath(graph_path).IsValid()
    )
    if same_graph:
        _state = {
            **_state,
            "command_requested": command_requested,
            "command_safety": command_safety,
        }
        return dict(_state)

    if stage.GetPrimAtPath(graph_path).IsValid():
        raise RuntimeError(
            f"ROS 2 graph path already exists with another configuration: {graph_path}"
        )

    previous_target = stage.GetEditTarget()
    stage.SetEditTarget(Usd.EditTarget(stage.GetSessionLayer()))
    try:
        nodes = [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ReadSimTime", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("ReadJointState", "isaacsim.sensors.physics.IsaacReadJointState"),
            ("Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishJointState", "isaacsim.ros2.bridge.ROS2PublishJointState"),
        ]
        connections = [
            ("OnPlaybackTick.outputs:tick", "ReadJointState.inputs:execIn"),
            ("ReadJointState.outputs:execOut", "PublishJointState.inputs:execIn"),
            ("ReadJointState.outputs:jointNames", "PublishJointState.inputs:jointNames"),
            ("ReadJointState.outputs:jointPositions", "PublishJointState.inputs:jointPositions"),
            ("ReadJointState.outputs:jointVelocities", "PublishJointState.inputs:jointVelocities"),
            ("ReadJointState.outputs:jointEfforts", "PublishJointState.inputs:jointEfforts"),
            ("ReadJointState.outputs:jointDofTypes", "PublishJointState.inputs:jointDofTypes"),
            ("ReadJointState.outputs:stageMetersPerUnit", "PublishJointState.inputs:stageMetersPerUnit"),
            ("ReadJointState.outputs:sensorTime", "PublishJointState.inputs:sensorTime"),
            ("ReadSimTime.outputs:simulationTime", "PublishJointState.inputs:timeStamp"),
            ("Context.outputs:context", "PublishJointState.inputs:context"),
        ]
        values = [
            ("ReadJointState.inputs:prim", [usdrt.Sdf.Path(articulation_root)]),
            ("PublishJointState.inputs:nodeNamespace", namespace),
            ("PublishJointState.inputs:topicName", DEFAULT_STATE_TOPIC),
        ]
        if commands_active:
            nodes.extend(
                [
                    ("SubscribeJointState", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
                    ("ArticulationController", "isaacsim.core.nodes.IsaacArticulationController"),
                ]
            )
            connections.extend(
                [
                    ("OnPlaybackTick.outputs:tick", "SubscribeJointState.inputs:execIn"),
                    ("OnPlaybackTick.outputs:tick", "ArticulationController.inputs:execIn"),
                    ("Context.outputs:context", "SubscribeJointState.inputs:context"),
                    ("SubscribeJointState.outputs:jointNames", "ArticulationController.inputs:jointNames"),
                    ("SubscribeJointState.outputs:positionCommand", "ArticulationController.inputs:positionCommand"),
                    ("SubscribeJointState.outputs:velocityCommand", "ArticulationController.inputs:velocityCommand"),
                    ("SubscribeJointState.outputs:effortCommand", "ArticulationController.inputs:effortCommand"),
                ]
            )
            values.extend(
                [
                    ("ArticulationController.inputs:robotPath", articulation_root),
                    ("SubscribeJointState.inputs:nodeNamespace", namespace),
                    ("SubscribeJointState.inputs:topicName", DEFAULT_COMMAND_TOPIC),
                ]
            )
        if publish_clock:
            nodes.append(("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"))
            connections.extend(
                [
                    ("OnPlaybackTick.outputs:tick", "PublishClock.inputs:execIn"),
                    ("Context.outputs:context", "PublishClock.inputs:context"),
                    ("ReadSimTime.outputs:simulationTime", "PublishClock.inputs:timeStamp"),
                ]
            )
            # /clock is intentionally global; ROS use_sim_time consumers expect it there.
            values.extend(
                [
                    ("PublishClock.inputs:nodeNamespace", ""),
                    ("PublishClock.inputs:topicName", "/clock"),
                ]
            )

        og.Controller.edit(
            {"graph_path": graph_path, "evaluator_name": "execution"},
            {
                og.Controller.Keys.CREATE_NODES: nodes,
                og.Controller.Keys.CONNECT: connections,
                og.Controller.Keys.SET_VALUES: values,
            },
        )
    finally:
        stage.SetEditTarget(previous_target)

    _stage_identity = id(stage)
    _state = {
        "configured": True,
        "authored_layer": stage.GetSessionLayer().identifier,
        "stage": stage.GetRootLayer().realPath or stage.GetRootLayer().identifier,
        "robot_root": logical_root,
        "articulation_root": articulation_root,
        "graph_path": graph_path,
        "namespace": namespace,
        "joint_state_topic": f"{namespace}/{DEFAULT_STATE_TOPIC}",
        "joint_command_topic": f"{namespace}/{DEFAULT_COMMAND_TOPIC}" if commands_active else None,
        "command_requested": command_requested,
        "command_enabled": commands_active,
        "command_safety": command_safety,
        "clock_topic": "/clock" if publish_clock else None,
        "session_only": True,
    }
    return dict(_state)


class Ros2ArticulationAutoAttach:
    """Rebuild the transient bridge whenever a stage with one robot opens."""

    def __init__(self) -> None:
        self._subscription = None
        self._task = None

    def start(self) -> dict[str, Any]:
        import omni.usd

        if self._subscription is None:
            self._subscription = (
                omni.usd.get_context()
                .get_stage_event_stream()
                .create_subscription_to_pop(
                    self._on_stage_event, name="HomeHero ROS2 articulation auto-attach"
                )
            )
        self._schedule()
        return get_ros2_articulation_state()

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._subscription = None

    def _on_stage_event(self, event) -> None:
        import omni.usd

        if event.type in (
            int(omni.usd.StageEventType.OPENED),
            int(omni.usd.StageEventType.ASSETS_LOADED),
        ):
            self._schedule()

    def _schedule(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self._task = asyncio.ensure_future(self._attach_after_load())

    async def _attach_after_load(self) -> None:
        global _state

        import carb
        import omni.kit.app

        try:
            # OPENED can precede reference composition; wait for Kit to settle.
            for _ in range(3):
                await omni.kit.app.get_app().next_update_async()
            state = configure_ros2_articulation()
            carb.log_warn(f"[IsaacAssist] ROS2 articulation attached: {state}")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            _state = {"configured": False, "reason": str(exc)}
            carb.log_info(f"[IsaacAssist] ROS2 articulation auto-attach skipped: {exc}")
        finally:
            self._task = None


def get_ros2_articulation_auto_attach() -> Ros2ArticulationAutoAttach:
    global _auto_attach
    if _auto_attach is None:
        _auto_attach = Ros2ArticulationAutoAttach()
    return _auto_attach
