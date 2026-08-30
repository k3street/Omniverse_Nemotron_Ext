#!/usr/bin/env python3
"""Visible Gemini Robotics ER 2 coach test on a RoboLab manipulation scene.

The model is the stage-level embodied-reasoning coach. A semantic scene-role
binding selects the live movable object and target receptacle while registered
runtime tools execute model-issued bounded motion and actuator operations.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import cv2  # Must precede Isaac Lab imports.
import h5py
import numpy as np
import requests
import torch
from dotenv import load_dotenv
from isaaclab.app import AppLauncher


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env")

from manipulation_scene_roles import ManipulationSceneRoles

parser = argparse.ArgumentParser(
    description="Run all visible Gemini Robotics ER 2 tests on RoboLab's current DROID robot."
)
parser.add_argument("--task", default="BananaOnPlateTask")
parser.add_argument(
    "--movable-object-asset",
    default="banana",
    help="Scene asset bound to the semantic movable_object role.",
)
parser.add_argument(
    "--movable-object-label",
    help="Optional model-facing label; defaults to a humanized scene asset name.",
)
parser.add_argument(
    "--target-receptacle-asset",
    default="plate_large",
    help="Scene asset bound to the semantic target_receptacle role.",
)
parser.add_argument(
    "--target-receptacle-label",
    default="white plate",
    help="Model-facing label for the target receptacle.",
)
parser.add_argument(
    "--instruction",
    default=None,
    help=(
        "Natural-language instruction supplied to the model at every fresh "
        "observation-bound motion decision."
    ),
)
parser.add_argument("--model", default="gemini-robotics-er-2-preview")
parser.add_argument("--retry-steps", type=int, default=20)
parser.add_argument(
    "--motion-checkpoint-replans",
    type=int,
    default=3,
    help=(
        "Maximum fresh, observation-bound model attempts after a motion "
        "checkpoint tool call is rejected by the local safety gate."
    ),
)
parser.add_argument(
    "--disable-adaptive-ik",
    action="store_true",
    help="Replay the fixed demonstration instead of targeting live object poses.",
)
parser.add_argument("--adaptive-tolerance", type=float, default=0.012)
parser.add_argument("--adaptive-max-step", type=float, default=0.020)
parser.add_argument("--adaptive-max-iterations", type=int, default=24)
parser.add_argument("--adaptive-settle-steps", type=int, default=12)
parser.add_argument("--adaptive-max-joint-step", type=float, default=0.07)
parser.add_argument("--adaptive-damping", type=float, default=0.05)
parser.add_argument("--adaptive-orientation-tolerance-deg", type=float, default=4.0)
parser.add_argument("--adaptive-max-angle-step-deg", type=float, default=8.0)
parser.add_argument(
    "--coach-interval-iterations",
    type=int,
    default=10,
    help="Send a fresh mid-motion observation to Gemini every N local IK chunks.",
)
parser.add_argument(
    "--periodic-motion-observations",
    action=argparse.BooleanOptionalAction,
    default=False,
    help=(
        "Also poll Gemini at --coach-interval-iterations; disabled by default "
        "so a motion lease runs until completion or local invalidation."
    ),
)
parser.add_argument(
    "--maximum-model-target-correction",
    type=float,
    default=0.10,
    help=(
        "Safety envelope for one model-issued XYZ target correction in meters."
    ),
)
parser.add_argument(
    "--maximum-model-rotation-correction-deg",
    type=float,
    default=45.0,
    help=(
        "Safety envelope for one model-issued world-frame axis-angle target "
        "orientation correction in degrees."
    ),
)
parser.add_argument(
    "--maximum-pregrasp-axis-error-deg",
    type=float,
    default=12.0,
    help=(
        "Maximum measured support-plane angle between the parallel-jaw "
        "closing axis and an RGB-D oriented object-box axis before engage is "
        "advertised."
    ),
)
parser.add_argument("--maximum-grasp-drift", type=float, default=0.025)
parser.add_argument("--minimum-transport-lift", type=float, default=0.030)
parser.add_argument("--max-transport-recoveries", type=int, default=8)
parser.add_argument(
    "--max-lift-recovery-operations",
    type=int,
    default=32,
    help=(
        "Maximum fresh scheduler operations available to complete full "
        "move/actuate/lift recovery cycles."
    ),
)
parser.add_argument(
    "--max-failed-grasp-attempts",
    type=int,
    default=8,
    help=(
        "Maximum physically tested grasp poses whose measured lift outcome "
        "may fail before the episode is rejected."
    ),
)
parser.add_argument(
    "--failed-grasp-retry-minimum-translation",
    type=float,
    default=0.015,
    help=(
        "Minimum object-relative end-effector translation in meters which "
        "releases the failed-grasp retry lease."
    ),
)
parser.add_argument(
    "--failed-grasp-retry-minimum-orientation-deg",
    type=float,
    default=10.0,
    help=(
        "Minimum end-effector orientation change in degrees which releases "
        "the failed-grasp retry lease."
    ),
)
parser.add_argument("--recovery-hold-steps", type=int, default=24)
parser.add_argument("--recovery-stability-drift", type=float, default=0.008)
parser.add_argument("--recovery-set-down-clearance", type=float, default=0.006)
parser.add_argument(
    "--rgbd-safety",
    action=argparse.BooleanOptionalAction,
    default=True,
    help="Render depth and include a depth visualization/metrics at motion checkpoints.",
)
parser.add_argument(
    "--ros2-sensor-ingress",
    action=argparse.BooleanOptionalAction,
    default=False,
    help=(
        "Subscribe to normalized ROS 2 touch, force, RGB-D, tracked-object, "
        "collision-stop, and motion-feedback topics when ROS 2 is available; "
        "disabled by default while simulator-native sensing is used."
    ),
)
parser.add_argument(
    "--ros2-touch-topic", default="/isaac_assist/gripper_touch"
)
parser.add_argument(
    "--ros2-contact-wrench-topic",
    default="/isaac_assist/gripper_contact_wrench",
)
parser.add_argument(
    "--ros2-contact-status-topic",
    default="/isaac_assist/gripper_contact_status",
)
parser.add_argument(
    "--ros2-rgbd-status-topic",
    default="/isaac_assist/rgbd_collision_status",
)
parser.add_argument(
    "--ros2-safety-stop-topic", default="/isaac_assist/safety_stop"
)
parser.add_argument(
    "--ros2-tracked-object-status-topic",
    default="/isaac_assist/tracked_object_status",
)
parser.add_argument(
    "--ros2-motion-status-topic", default="/isaac_assist/motion_status"
)
parser.add_argument("--approach-clearance", type=float, default=0.10)
parser.add_argument("--lift-clearance", type=float, default=0.14)
parser.add_argument("--plate-hover-height", type=float, default=0.27)
parser.add_argument(
    "--movable-object-offset",
    "--banana-offset",
    dest="movable_object_offset",
    nargs=2,
    type=float,
    default=(0.0, 0.0),
    metavar=("DX", "DY"),
    help="Relocate the selected movable object in robot-root XY meters after reset.",
)
parser.add_argument(
    "--plate-offset",
    nargs=2,
    type=float,
    default=(0.0, 0.0),
    metavar=("DX", "DY"),
    help="Relocate the plate in robot-root XY meters after reset.",
)
parser.add_argument(
    "--movable-object-yaw-deg",
    "--banana-yaw-deg",
    dest="movable_object_yaw_deg",
    type=float,
    default=0.0,
    help="Rotate the selected movable object around world Z after reset.",
)
parser.add_argument(
    "--randomize-background",
    action="store_true",
    help="Choose a deterministic non-default RoboLab HDRI using --appearance-seed.",
)
parser.add_argument("--appearance-seed", type=int, default=0)
parser.add_argument(
    "--light-intensity",
    type=float,
    help="Override the RoboLab sphere-light intensity for appearance diversity.",
)
parser.add_argument(
    "--disable-residual-centering",
    action="store_true",
    help="Skip the fresh model-governed placement operation after transport.",
)
parser.add_argument("--center-tolerance", type=float, default=0.040)
parser.add_argument("--center-max-step", type=float, default=0.008)
parser.add_argument("--center-max-z-step", type=float, default=0.008)
parser.add_argument("--release-height", type=float, default=0.040)
parser.add_argument("--release-height-tolerance", type=float, default=0.012)
parser.add_argument("--plate-contact-height", type=float, default=0.025)
parser.add_argument("--center-max-iterations", type=int, default=48)
parser.add_argument("--center-settle-steps", type=int, default=16)
parser.add_argument("--center-max-joint-step", type=float, default=0.04)
parser.add_argument("--center-damping", type=float, default=0.05)
parser.add_argument(
    "--disable-release-retreat",
    action="store_true",
    help="Leave the open gripper at the release pose instead of verifying detachment.",
)
parser.add_argument("--retreat-distance", type=float, default=0.080)
parser.add_argument("--retreat-max-step", type=float, default=0.020)
parser.add_argument("--retreat-max-iterations", type=int, default=6)
parser.add_argument("--retreat-settle-steps", type=int, default=12)
parser.add_argument(
    "--demo",
    type=Path,
    default=REPO_ROOT / "artifacts" / "banana_on_plate_demos_v2" / "data.hdf5",
    help="Successful RoboLab demonstration supplying the local joint trajectory.",
)
parser.add_argument(
    "--linger-steps",
    type=int,
    default=90,
    help="Keep the final robot pose visible for this many physics steps.",
)
parser.add_argument("--timeout", type=float, default=120.0)
parser.add_argument("--model-max-retries", type=int, default=2)
parser.add_argument("--model-retry-backoff", type=float, default=2.0)
parser.add_argument(
    "--artifact-dir",
    type=Path,
    default=REPO_ROOT / "artifacts" / "gemini_robotics_er2_robolab",
)
parser.add_argument(
    "--training-episode-dir",
    type=Path,
    help="Successful Gemini completions are published here as HDF5 + two-camera video.",
)
parser.add_argument(
    "--episode-index",
    type=int,
    default=-1,
    help="Training episode index; -1 chooses the next unused index.",
)
parser.add_argument("--record-video-scale", type=float, default=0.5)
parser.add_argument(
    "--disable-training-recording",
    action="store_true",
    help="Run evaluation only; no successful completion is admitted as training data.",
)
parser.add_argument(
    "--disable-contact-telemetry",
    action="store_true",
    help="Disable the Sim 6 two-finger sensor and its success-admission gate.",
)
parser.add_argument("--minimum-contact-coverage", type=float, default=0.95)
parser.add_argument("--minimum-touch-samples", type=int, default=1)
parser.add_argument(
    "--maximum-actuator-interaction-distance",
    type=float,
    default=0.02,
    help=(
        "Runtime-configurable fingertip/object proximity for advertising an "
        "actuator transition when retained touch is unavailable."
    ),
)
parser.add_argument(
    "--actuator-feedback-position-change",
    type=float,
    default=0.05,
    help=(
        "Minimum normalized actuator-position change which, together with a "
        "tactile change, immediately returns control to the model."
    ),
)
parser.add_argument(
    "--actuator-feedback-force-change",
    type=float,
    default=0.25,
    help=(
        "Minimum tactile-force delta in newtons that counts as a significant "
        "post-actuation observation change."
    ),
)
parser.add_argument(
    "--disable-critic-guidance",
    action="store_true",
    help="Ignore phase-scoped lessons from the previous passive local critique.",
)
parser.add_argument(
    "--disable-world-intent-shadow",
    action="store_true",
    help=(
        "Skip the non-authoritative embodiment-neutral Gemini intent and "
        "causal goal-graph probes."
    ),
)
parser.add_argument(
    "--world-goal-revision-attempts",
    type=int,
    default=1,
    help=(
        "Maximum bounded Gemini graph revisions when an admitted graph has "
        "no activatable goal and exposes exact evidence blockers."
    ),
)
parser.add_argument(
    "--world-scope-revision-attempts",
    type=int,
    default=1,
    help=(
        "Maximum bounded graph corrections when a fresh task-membership "
        "audit conflicts with proposed entity scope."
    ),
)
parser.add_argument(
    "--shadow-plan-only",
    action="store_true",
    help=(
        "Run live scene observation, embodiment-neutral intent/goal-graph "
        "reasoning, and shadow goal selection, then exit before loading a "
        "demonstration, creating execution providers, or authorizing motion."
    ),
)
parser.add_argument(
    "--world-effect-runtime-lease-duration-s",
    type=float,
    default=120.0,
    help=(
        "Runtime-owned wall-clock deadman for one issued exact-invocation "
        "lease. Sensor invalidations remain active throughout; this window "
        "must cover one bounded rendered IK operation."
    ),
)
parser.add_argument(
    "--guarded-world-effect-execution",
    action="store_true",
    help=(
        "Execute a bounded sequence of validated world-effect invocations. "
        "Every operation receives a fresh-evidence, single-use permit and is "
        "followed by world-goal re-evaluation before any replan."
    ),
)
parser.add_argument(
    "--world-effect-max-operations",
    type=int,
    default=8,
    help=(
        "Runtime-owned maximum number of single-use guarded operations in one "
        "world-effect sequence. Model output cannot enlarge this bound."
    ),
)
parser.add_argument(
    "--world-effect-dispatch-evidence-max-age-s",
    type=float,
    default=0.75,
    help="Maximum age of the final fresh evidence used to mint a dispatch permit.",
)
parser.add_argument(
    "--world-effect-dispatch-permit-lifetime-s",
    type=float,
    default=0.75,
    help="Maximum lifetime of the single-use permit before handler entry.",
)
parser.add_argument(
    "--world-effect-preflight-settle-steps",
    type=int,
    default=12,
    help=(
        "Physics-only stabilization steps, with the robot held at its current "
        "joint state, before world-effect evidence and authority are created."
    ),
)
AppLauncher.add_app_launcher_args(parser)
args_cli, _ = parser.parse_known_args()
SCENE_ROLES = ManipulationSceneRoles.create(
    movable_object_asset=args_cli.movable_object_asset,
    movable_object_label=args_cli.movable_object_label,
    target_receptacle_asset=args_cli.target_receptacle_asset,
    target_receptacle_label=args_cli.target_receptacle_label,
)
args_cli.instruction = args_cli.instruction or SCENE_ROLES.default_instruction()
args_cli.enable_cameras = True
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app


import robolab.constants  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdPhysics  # noqa: E402
from isaaclab.utils.math import (  # noqa: E402
    convert_camera_frame_orientation_convention,
)
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402
from robolab.core.environments.config import parse_env_cfg  # noqa: E402
from robolab.core.observations.observation_utils import unpack_image_obs  # noqa: E402
from robolab.core.utils.vis_utils import visualize_axes  # noqa: E402
from robolab.core.utils.video_utils import VideoWriter  # noqa: E402
from robolab.registrations.droid.auto_env_registrations_abs_ik import (  # noqa: E402
    auto_register_droid_abs_ik_envs,
)
from robolab.robots.droid import DroidJointPositionActionCfg  # noqa: E402
from adaptive_pick_place import (  # noqa: E402
    apply_object_relative_grasp,
    derive_object_relative_grasp,
    derive_manipulation_feedback,
    live_phase_target,
    pregrasp_evidence_ready,
    quaternion_error_axis_angle_wxyz,
    quaternion_multiply_wxyz,
    yaw_quaternion_wxyz,
)
from residual_centering import (  # noqa: E402
    bounded_scalar_step,
    bounded_vector_step,
    bounded_xy_step,
    damped_least_squares_delta,
)
from franka_sensor_schema import (  # noqa: E402
    SENSOR_DIM,
    SIGNAL_SLICES,
    VALIDITY_DIM,
    SensorCaptureBuffer,
    empty_sensor_frame,
    sensor_frame_from_isaac_env,
    summarize_contact_telemetry,
)
from gemini_episode_dataset import GeminiEpisodeDatasetRecorder  # noqa: E402
from robolab_contact_telemetry import (  # noqa: E402
    contact_body_force_observation,
    contact_sensor_runtime_info,
    install_sim6_gripper_contact_sensor,
)
from observation_bound_motion_tools import (  # noqa: E402
    ActuatorFeedbackEventPolicy,
    ActuatorExecutorRegistry,
    ActuatorExecutorSpec,
    MotionExecutorRegistry,
    MotionExecutorSpec,
    MotionLeaseConditions,
    MotionToolValidationError,
    ObservationBoundActuatorGate,
    ObservationBoundMotionGate,
    ObservationBoundOperationGate,
    ObservationBoundTaskFeasibilityGate,
    OperationCandidate,
    assess_actuator_feedback_event,
    actuator_command_outcome_invalidation_reason,
    actuator_tool_schemas,
    compare_grasp_pose_to_failed_attempts,
    compare_target_to_stalled_recovery,
    failed_grasp_pose_lease_released,
    motion_report_yields_to_scheduler,
    motion_checkpoint_scheduler_handoff_reason,
    recovery_motion_handoff_from_report,
    motion_lease_source_errors,
    motion_tool_schemas,
    opposing_contact_force_capacity,
    operation_scheduler_tool_schemas,
    actuator_transition_is_admissible,
    retained_contact_supports_loaded_actuator,
    runtime_transition_admission,
    runtime_transition_motion_handoff,
    task_feasibility_tool_schema,
)
from rgbd_object_axis_tracking import (  # noqa: E402
    estimate_masked_object_axis,
    instance_mask_for_prim_label,
    sign_invariant_axis_error_deg,
)
from rgbd_collision_safety import (  # noqa: E402
    pregrasp_axis_alignment_observation,
    summarize_labeled_scene_geometry,
    transform_matrix_from_pose_xyzw,
)
from sensor_invalidation_registry import (  # noqa: E402
    PredicateResult,
    SensorObservation,
    SensorObservationSnapshot,
    SensorPredicateLease,
    SensorPredicateRegistry,
    SensorPredicateSpec,
)
from sensor_invalidation_ros2 import (  # noqa: E402
    ROS2SensorIngress,
    ROS2SensorIngressConfig,
    overlay_sensor_observations,
    start_ros2_sensor_ingress,
)
from transport_recovery import (  # noqa: E402
    SupportContactMonitor,
    assess_release_detachment,
    assess_recovery_hold,
    placement_completion_event,
    support_aligned_object_quaternion_wxyz,
)
from world_intent_contract import (  # noqa: E402
    WORLD_INTENT_SCHEMA_VERSION,
    WorldIntent,
    build_world_intent_prompt,
)
from world_goal_graph_contract import (  # noqa: E402
    SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION,
    WORLD_GOAL_GRAPH_SCHEMA_VERSION,
    WorldGoalGraph,
    build_world_goal_graph_prompt,
    semantic_scene_inventory_from_state,
    validate_world_goal_graph_entity_references,
    validate_world_goal_graph_revision,
)
from world_goal_graph_membership import (  # noqa: E402
    SceneMembershipLease,
    assess_world_goal_graph_scene_scope,
)
from world_goal_activation import (  # noqa: E402
    WorldGoalActivationGate,
    build_goal_activation_candidates,
    build_world_goal_activation_prompt,
    shadow_world_capability_registry,
)
from world_entity_physical_evidence import (  # noqa: E402
    build_entity_physical_evidence,
)
from world_effect_provider_registry import (  # noqa: E402
    RuntimeToolCapability,
    default_world_effect_provider_registry,
)
from world_effect_session import (  # noqa: E402
    WorldEffectSessionGate,
    build_world_effect_session_candidates,
    build_world_effect_session_prompt,
)
from world_effect_operation_plan import (  # noqa: E402
    PlanningToolFactory,
    PlanningToolFactoryCatalog,
    WorldEffectOperationGate,
    build_planning_world_effect_provider_instance,
    build_world_effect_operation_candidates,
    build_world_effect_operation_prompt,
)
from world_effect_execution_lease import (  # noqa: E402
    ShadowExecutionLeaseGate,
    build_shadow_execution_lease_candidates,
    build_shadow_execution_lease_prompt,
)
from world_effect_tool_invocation import (  # noqa: E402
    RUNTIME_TOOL_OBSERVATION_SCHEMA_VERSION,
    ShadowToolInvocationGate,
    WorldEffectToolInvocationError,
    build_shadow_tool_invocation_candidates,
    build_shadow_tool_invocation_prompt,
)
from world_effect_runtime_lease import (  # noqa: E402
    issue_world_effect_runtime_lease,
)
from world_effect_guarded_dispatch import (  # noqa: E402
    assess_fused_target_geometry,
    DispatchInvalidationEvent,
    GuardedWorldEffectDispatcher,
    RuntimeWorldEffectHandlerRegistry,
    build_fresh_dispatch_evidence,
    interaction_obstacle_geometry,
)
from world_effect_closed_loop import (  # noqa: E402
    WorldEffectSequenceBudget,
    assess_world_effect_progress,
)
from world_scope_membership_audit import (  # noqa: E402
    WorldScopeMembershipAuditGate,
    assess_world_goal_graph_membership_audit,
    build_world_scope_membership_audit_prompt,
    world_scope_membership_observation_id,
)
from world_predicate_evaluator_registry import (  # noqa: E402
    rgbd_world_predicate_evaluator_registry,
)
from service.isaac_assist_service.chat.llm_gemini import GeminiProvider  # noqa: E402


MODEL_ID = args_cli.model
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{MODEL_ID}:generateContent"
)

GRIPPER_BASE_TO_FINGERTIP_M = 0.149
SPATIAL_MOTION_CAPABILITY_TAGS = (
    "spatial.pose_target",
    "motion.observation_bound",
    "motion.invalidation_feedback",
)
REVERSIBLE_ATTACHMENT_CAPABILITY_TAGS = (
    "entity_attachment.acquire",
    "entity_attachment.release",
    "actuation.observation_bound",
)
DEFAULT_OBJECT_GRASP_OFFSET = torch.tensor(
    [0.0, 0.0, GRIPPER_BASE_TO_FINGERTIP_M], dtype=torch.float32
)
DEFAULT_DOWNWARD_GRASP_QUAT = torch.tensor(
    [0.555, 0.385, 0.616, -0.406], dtype=torch.float32
)
DEFAULT_DOWNWARD_GRASP_QUAT /= torch.linalg.norm(DEFAULT_DOWNWARD_GRASP_QUAT)
TOTAL_TESTS = 10
VALID_CRITIC_PHASES = {
    "global", "approach_object", "descend", "grasp", "lift", "above_plate",
    "place", "release"
}
ACTIVE_EPISODE_RECORDER: GeminiEpisodeDatasetRecorder | None = None
ACTIVE_SENSOR_MONITOR: SensorCaptureBuffer | None = None
ACTIVE_SENSOR_SAMPLE_INDEX = 0
ACTIVE_ROS2_SENSOR_INGRESS: ROS2SensorIngress | None = None
ACTUATOR_CONTACT_LOCAL_GEOMETRY: dict[str, Any] | None = None


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"ER 2 did not return a JSON object: {text[:300]}")
    return json.loads(cleaned[start : end + 1])


def _encode_frame(frame: np.ndarray) -> tuple[str, str]:
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("Failed to JPEG-encode RoboLab camera observation")
    raw = encoded.tobytes()
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()[:12]


def _load_critic_guidance(path: Path, task: str) -> dict[str, Any]:
    """Load only bounded semantic lessons created by the passive critic."""
    if not path.is_file():
        return {"source_model": None, "lessons": []}
    try:
        memory = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"[critic-memory] ignored unreadable memory: {error}", flush=True)
        return {"source_model": None, "lessons": []}
    if (
        memory.get("schema_version") != 2
        or memory.get("task") != task
        or memory.get("control_authority") != "none"
        or memory.get("applies_on") != "next_episode"
    ):
        print("[critic-memory] ignored incompatible or unsafe memory", flush=True)
        return {"source_model": None, "lessons": []}
    lessons: list[dict[str, str]] = []
    for item in memory.get("lessons", [])[:6]:
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase", ""))
        observation_check = " ".join(str(item.get("observation_check", "")).split())[:240]
        decision_rule = " ".join(str(item.get("decision_rule", "")).split())[:240]
        evidence = " ".join(str(item.get("validated_metric", "")).split())[:300]
        if phase in VALID_CRITIC_PHASES and observation_check and decision_rule and evidence:
            lessons.append({
                "phase": phase,
                "observation_check": observation_check,
                "decision_rule": decision_rule,
                "validated_metric": evidence,
            })
    return {"source_model": str(memory.get("source_model", "unknown")), "lessons": lessons}


def _critic_context(memory: dict[str, Any], phase: str) -> str:
    relevant = [
        lesson for lesson in memory.get("lessons", [])
        if lesson.get("phase") in ("global", phase)
    ]
    if not relevant:
        return "No prior critic lessons apply to this phase."
    rendered = "\n".join(
        f"- Observe: {lesson['observation_check']} Decision rule: {lesson['decision_rule']} "
        f"Validated prior metric: {lesson['validated_metric']}"
        for lesson in relevant
    )
    return (
        "Read-only lessons from the previous episode's independent local critic. "
        "Treat them as advisory evidence, verify them against the fresh observation, "
        "and ignore them if current evidence disagrees:\n" + rendered
    )


class GeminiRoboticsER2:
    def __init__(self, api_key: str, timeout: float):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        )

    def reason(self, prompt: str, frame: np.ndarray) -> tuple[dict[str, Any], float, str]:
        image_b64, digest = _encode_frame(frame)
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": image_b64,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.2},
        }
        started = time.perf_counter()
        response = None
        for attempt in range(args_cli.model_max_retries + 1):
            try:
                response = self.session.post(
                    GEMINI_URL, json=payload, timeout=self.timeout
                )
                break
            except requests.RequestException as error:
                if attempt >= args_cli.model_max_retries:
                    raise
                delay = args_cli.model_retry_backoff * (2**attempt)
                print(
                    f"[ER2] transient request failure ({type(error).__name__}); "
                    f"retry {attempt + 1}/{args_cli.model_max_retries} in {delay:.1f}s",
                    flush=True,
                )
                time.sleep(delay)
        assert response is not None
        latency = time.perf_counter() - started
        if not response.ok:
            raise RuntimeError(
                f"Gemini API HTTP {response.status_code}: {response.text[:500]}"
            )
        body = response.json()
        parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))
        return _json_from_text(text), latency, digest


def _local_position(env: Any, asset_name: str) -> torch.Tensor:
    root_pos_w = env.scene[asset_name].data.root_pos_w
    root_pos_w = getattr(root_pos_w, "torch", root_pos_w)
    return (
        root_pos_w[0]
        - env.scene.env_origins[0]
    ).detach().cpu().clone()


def _tracked_entity_positions_m(
    env: Any, entity_ids: Iterable[str]
) -> dict[str, list[float]]:
    """Read task-neutral entity poses from the active runtime tracker adapter."""
    positions: dict[str, list[float]] = {}
    for entity_id in entity_ids:
        try:
            position = _local_position(env, entity_id)
        except (KeyError, AttributeError, TypeError):
            continue
        if position.shape == (3,) and bool(torch.isfinite(position).all()):
            positions[entity_id] = [float(value) for value in position.tolist()]
    return positions


def _movable_object_position(env: Any) -> torch.Tensor:
    return _local_position(env, SCENE_ROLES.movable_object_asset)


def _target_receptacle_position(env: Any) -> torch.Tensor:
    return _local_position(env, SCENE_ROLES.target_receptacle_asset)


def _xyzw_to_wxyz(quaternion: torch.Tensor) -> torch.Tensor:
    return torch.cat((quaternion[-1:], quaternion[:3]))


def _wxyz_to_xyzw(quaternion: torch.Tensor) -> torch.Tensor:
    return torch.cat((quaternion[1:], quaternion[:1]))


def _local_quaternion(env: Any, asset_name: str) -> torch.Tensor:
    """Read an Isaac Sim 6 runtime quaternion in RoboLab recording order."""
    quaternion = env.scene[asset_name].data.root_quat_w
    quaternion = getattr(quaternion, "torch", quaternion)[0].detach().cpu().clone()
    return _xyzw_to_wxyz(quaternion)


def _movable_object_quaternion(env: Any) -> torch.Tensor:
    return _local_quaternion(env, SCENE_ROLES.movable_object_asset)


def _eef_position(env: Any) -> torch.Tensor:
    # Isaac Sim 6 exposes articulation buffers as ProxyArray; always take its
    # explicit torch view. The older FrameTransformer path also flips the nested
    # Robotiq transform in this source-build combination.
    robot = env.scene["robot"]
    index = robot.data.body_names.index("base_link")
    body_pos_w = robot.data.body_pos_w
    body_pos_w = getattr(body_pos_w, "torch", body_pos_w)
    root_pos_w = robot.data.root_pos_w
    root_pos_w = getattr(root_pos_w, "torch", root_pos_w)
    return (
        body_pos_w[0, index]
        - root_pos_w[0]
    ).detach().cpu().clone()


def _eef_quaternion(env: Any) -> torch.Tensor:
    robot = env.scene["robot"]
    index = robot.data.body_names.index("base_link")
    body_quat_w = robot.data.body_quat_w
    body_quat_w = getattr(body_quat_w, "torch", body_quat_w)
    return _xyzw_to_wxyz(body_quat_w[0, index].detach().cpu().clone())


def _step_env(env: Any, action: torch.Tensor):
    """Step once and mirror the executed transition into the active collector."""
    global ACTIVE_SENSOR_SAMPLE_INDEX
    result = env.step(action)
    sensor_frame = None
    if ACTIVE_SENSOR_MONITOR is not None:
        try:
            sensor_frame = sensor_frame_from_isaac_env(env)
        except Exception:
            sensor_frame = empty_sensor_frame()
        ACTIVE_SENSOR_MONITOR.append(sensor_frame, ACTIVE_SENSOR_SAMPLE_INDEX / 15.0)
        ACTIVE_SENSOR_SAMPLE_INDEX += 1
    if ACTIVE_EPISODE_RECORDER is not None:
        ACTIVE_EPISODE_RECORDER.append(
            env,
            action,
            result[0],
            eef_position=_eef_position(env).numpy(),
            eef_quaternion_wxyz=_eef_quaternion(env).numpy(),
            sensor_frame=sensor_frame,
        )
    return result


def _active_contact_summary() -> dict[str, Any]:
    if ACTIVE_SENSOR_MONITOR is None:
        values = np.zeros((0, SENSOR_DIM), dtype=np.float32)
        validity = np.zeros((0, VALIDITY_DIM), dtype=np.float32)
    else:
        values, validity, _ = ACTIVE_SENSOR_MONITOR.arrays()
    return summarize_contact_telemetry(
        values,
        validity,
        minimum_coverage=args_cli.minimum_contact_coverage,
        minimum_touch_samples=args_cli.minimum_touch_samples,
    )


def _current_contact_observation(env: Any) -> dict[str, Any]:
    """Expose the fresh contact sample to the same model observation."""
    try:
        frame = sensor_frame_from_isaac_env(env)
        force_valid = bool(frame.validity[5] > 0.5)
        touch_valid = bool(frame.validity[6] > 0.5)
        force = np.asarray(
            frame.values[SIGNAL_SLICES["gripper_contact_force"]],
            dtype=np.float32,
        )
        touch = bool(
            frame.values[SIGNAL_SLICES["gripper_touch"]][0] >= 0.5
        )
        return {
            "available": force_valid and touch_valid,
            "touch": touch if touch_valid else None,
            "net_force_xyz_n": force.tolist() if force_valid else None,
            "net_force_n": (
                float(np.linalg.vector_norm(force)) if force_valid else None
            ),
            "contact_bodies": contact_body_force_observation(env),
        }
    except Exception as error:
        return {
            "available": False,
            "touch": None,
            "net_force_xyz_n": None,
            "net_force_n": None,
            "contact_bodies": {
                "available": False,
                "frame": "world",
                "channels": [],
            },
            "error": f"{type(error).__name__}: {error}",
        }


def _set_sim6_camera_views(env: Any) -> None:
    """Use look-at poses instead of legacy Sim 5 camera quaternions."""
    origins = env.scene.env_origins
    views = {
        "over_shoulder_left_camera": ((0.05, 0.57, 0.66), (0.48, -0.05, 0.05)),
        "egocentric_mirrored_camera": ((1.50, 0.00, 1.00), (0.42, 0.00, 0.10)),
    }
    for name, (eye, target) in views.items():
        camera = env.scene.sensors[name]
        eye_offset = torch.tensor(eye, dtype=torch.float32, device=camera.device)
        target_offset = torch.tensor(target, dtype=torch.float32, device=camera.device)
        camera.set_world_poses_from_view(origins.to(camera.device) + eye_offset,
                                         origins.to(camera.device) + target_offset)
        camera._update_poses(None)


def _transform_asset_pose(
    env: Any,
    asset_name: str,
    offset_xy: tuple[float, float],
    *,
    yaw_degrees: float = 0.0,
) -> None:
    """Apply deterministic post-reset translation and world-Z rotation."""
    if offset_xy == (0.0, 0.0) and yaw_degrees == 0.0:
        return
    asset = env.scene[asset_name]
    root_pose_w = asset.data.root_pose_w
    root_pose_w = getattr(root_pose_w, "torch", root_pose_w).clone()
    root_pose_w[0, :2] += torch.tensor(
        offset_xy, dtype=root_pose_w.dtype, device=root_pose_w.device
    )
    if yaw_degrees != 0.0:
        current_wxyz = _xyzw_to_wxyz(root_pose_w[0, 3:7])
        yaw_wxyz = yaw_quaternion_wxyz(
            np.deg2rad(yaw_degrees), like=current_wxyz
        )
        root_pose_w[0, 3:7] = _wxyz_to_xyzw(
            quaternion_multiply_wxyz(yaw_wxyz, current_wxyz)
        )
    asset.write_root_pose_to_sim(root_pose_w)
    root_vel_w = asset.data.root_vel_w
    root_vel_w = getattr(root_vel_w, "torch", root_vel_w)
    asset.write_root_velocity_to_sim(torch.zeros_like(root_vel_w))


def _single_exterior_frame(obs: dict[str, Any]) -> np.ndarray:
    frame = obs["image_obs"]["over_shoulder_left_camera"][0]
    if isinstance(frame, torch.Tensor):
        frame = frame.detach().cpu().numpy()
    frame = np.asarray(frame)
    return np.ascontiguousarray(frame[..., :3].astype(np.uint8, copy=False))


def _rgbd_checkpoint_frame(
    env: Any, rgb_frame: np.ndarray
) -> tuple[np.ndarray, dict[str, Any] | None]:
    """Render a human/model-readable depth panel beside the checkpoint RGB."""
    if not args_cli.rgbd_safety:
        return rgb_frame, None
    sensor = env.scene.sensors["over_shoulder_left_camera"]
    depth = sensor.data.output.get("depth")
    if depth is None:
        return rgb_frame, {"available": False, "reason": "depth_output_missing"}
    depth = getattr(depth, "torch", depth)
    if isinstance(depth, torch.Tensor):
        depth = depth[0].detach().cpu().numpy()
    depth = np.asarray(depth).squeeze()
    valid = np.isfinite(depth) & (depth > 0.05) & (depth < 5.0)
    summary: dict[str, Any] = {
        "available": True,
        "shape": list(depth.shape),
        "valid_fraction": float(valid.mean()),
    }
    if bool(valid.any()):
        samples = depth[valid]
        summary.update(
            {
                "minimum_m": float(samples.min()),
                "q05_m": float(np.quantile(samples, 0.05)),
                "median_m": float(np.median(samples)),
            }
        )
    clipped = np.where(valid, np.clip(depth, 0.10, 2.0), 2.0)
    normalized = ((2.0 - clipped) / 1.9 * 255.0).astype(np.uint8)
    colored = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
    colored = cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)
    if colored.shape[:2] != rgb_frame.shape[:2]:
        colored = cv2.resize(
            colored,
            (rgb_frame.shape[1], rgb_frame.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    composite = np.concatenate((rgb_frame, colored), axis=1)
    return np.ascontiguousarray(composite), summary


def _rgbd_object_axis_observation(
    env: Any,
    *,
    prim_label_fragment: str,
    reference_axis: np.ndarray | None = None,
) -> dict[str, Any]:
    """Read a masked RGB-D principal axis without using rigid-body pose state."""
    sensor = env.scene.sensors["over_shoulder_left_camera"]
    depth_value = sensor.data.output.get("depth")
    instance_value = sensor.data.output.get("instance_id_segmentation_fast")
    if depth_value is None or instance_value is None:
        raise ValueError("RGB-D depth or instance-id output is unavailable")

    def _camera_numpy(value: Any) -> np.ndarray:
        value = getattr(value, "torch", value)
        if isinstance(value, torch.Tensor):
            value = value[0].detach().cpu().numpy()
        return np.asarray(value).squeeze()

    depth = _camera_numpy(depth_value)
    instance_ids = _camera_numpy(instance_value)
    info = (sensor.data.info or {}).get("instance_id_segmentation_fast")
    mask, identity = instance_mask_for_prim_label(
        instance_ids,
        info,
        prim_label_fragment,
    )
    intrinsics = getattr(sensor.data.intrinsic_matrices, "torch", None)
    if intrinsics is None:
        intrinsics = sensor.data.intrinsic_matrices
    if isinstance(intrinsics, torch.Tensor):
        intrinsics = intrinsics[0].detach().cpu().numpy()
    observation = estimate_masked_object_axis(
        depth,
        mask,
        np.asarray(intrinsics),
    )
    result = {
        "available": True,
        "source": "rgbd_instance_depth_major_axis",
        "prim_label_fragment": prim_label_fragment,
        **identity,
        **observation.to_dict(),
    }
    if reference_axis is not None:
        result["orientation_error_deg"] = sign_invariant_axis_error_deg(
            reference_axis,
            observation.major_axis_camera,
        )
    return result


def _rgbd_scene_geometry_observation(env: Any) -> dict[str, Any]:
    """Fuse current simulator RGB-D/instance data into robot-root geometry."""
    sensor = env.scene.sensors["over_shoulder_left_camera"]
    depth_value = sensor.data.output.get("depth")
    instance_value = sensor.data.output.get("instance_id_segmentation_fast")
    info = (sensor.data.info or {}).get("instance_id_segmentation_fast")
    id_to_labels = None if info is None else info.get("idToLabels")
    if depth_value is None or instance_value is None or not isinstance(
        id_to_labels, Mapping
    ):
        raise ValueError("RGB-D depth or instance labels are unavailable")

    def _tensor_numpy(value: Any) -> np.ndarray:
        value = getattr(value, "torch", value)
        if isinstance(value, torch.Tensor):
            value = value[0].detach().cpu().numpy()
        return np.asarray(value).squeeze()

    camera_position_data_w = _tensor_numpy(sensor.data.pos_w)
    camera_quaternion_data_xyzw = _tensor_numpy(sensor.data.quat_w_ros)
    robot = env.scene["robot"]
    robot_position_w = _tensor_numpy(robot.data.root_pos_w)
    robot_quaternion_xyzw = _tensor_numpy(robot.data.root_quat_w)
    robot_to_world = transform_matrix_from_pose_xyzw(
        robot_position_w,
        robot_quaternion_xyzw,
    )
    intrinsics = _tensor_numpy(sensor.data.intrinsic_matrices)
    candidates: dict[str, tuple[np.ndarray, np.ndarray]] = {
        "camera_data_ros_xyzw": (
            camera_position_data_w,
            camera_quaternion_data_xyzw,
        ),
        "camera_data_ros_wxyz_reordered_to_xyzw": (
            camera_position_data_w,
            camera_quaternion_data_xyzw[[1, 2, 3, 0]],
        ),
    }
    stage = omni.usd.get_context().get_stage()
    camera_prim = (
        stage.GetPrimAtPath("/World/envs/env_0/over_shoulder_left_camera")
        if stage is not None
        else None
    )
    if camera_prim is not None and camera_prim.IsValid():
        camera_to_world_usd = UsdGeom.XformCache(
            Usd.TimeCode.Default()
        ).GetLocalToWorldTransform(camera_prim)
        camera_position_usd_w = np.asarray(
            camera_to_world_usd.ExtractTranslation(), dtype=np.float64
        )
        camera_quaternion_gl = camera_to_world_usd.ExtractRotationQuat()
        camera_quaternion_gl_xyzw = np.asarray(
            [
                *camera_quaternion_gl.GetImaginary(),
                camera_quaternion_gl.GetReal(),
            ],
            dtype=np.float64,
        )
        camera_quaternion_usd_ros_xyzw = (
            convert_camera_frame_orientation_convention(
                torch.tensor(
                    camera_quaternion_gl_xyzw[None], dtype=torch.float32
                ),
                origin="opengl",
                target="ros",
            )[0]
            .detach()
            .cpu()
            .numpy()
        )
        candidates["usd_xform_opengl_to_ros_xyzw"] = (
            camera_position_usd_w,
            camera_quaternion_usd_ros_xyzw,
        )
    scored: list[
        tuple[float, str, dict[str, Any], np.ndarray, np.ndarray]
    ] = []
    for convention, (camera_position_w, quaternion_xyzw) in candidates.items():
        camera_to_world = transform_matrix_from_pose_xyzw(
            camera_position_w,
            quaternion_xyzw,
        )
        camera_to_robot = np.linalg.inv(robot_to_world) @ camera_to_world
        summary = summarize_labeled_scene_geometry(
            depth_m=_tensor_numpy(depth_value),
            instance_ids=_tensor_numpy(instance_value),
            id_to_labels=id_to_labels,
            intrinsics=intrinsics,
            camera_to_base=camera_to_robot,
        )
        role_residuals: list[dict[str, Any]] = []
        for asset_name in (
            SCENE_ROLES.movable_object_asset,
            SCENE_ROLES.target_receptacle_asset,
        ):
            geometry = next(
                (
                    item
                    for item in summary["geometries"]
                    if item["runtime_id"] == asset_name
                ),
                None,
            )
            if geometry is None:
                continue
            expected = _local_position(env, asset_name).numpy()
            observed = np.asarray(geometry["center_base_m"], dtype=np.float64)
            extent = np.asarray(
                geometry["visible_extent_base_m"], dtype=np.float64
            )
            residual_m = float(np.linalg.norm(observed - expected))
            normalized = residual_m / max(0.05, 0.5 * float(np.linalg.norm(extent)))
            role_residuals.append(
                {
                    "runtime_id": asset_name,
                    "root_to_visible_center_residual_m": residual_m,
                    "extent_normalized_residual": normalized,
                }
            )
        score = (
            float(np.mean([item["extent_normalized_residual"] for item in role_residuals]))
            if role_residuals
            else float("inf")
        )
        summary["semantic_role_calibration"] = role_residuals
        scored.append(
            (score, convention, summary, camera_position_w, quaternion_xyzw)
        )
    score, convention, summary, camera_position_w, quaternion_xyzw = min(
        scored, key=lambda item: item[0]
    )
    summary["camera_pose_robot_root"] = {
        "position_m": (
            np.linalg.inv(robot_to_world)
            @ np.array([*camera_position_w.tolist(), 1.0])
        )[:3].tolist(),
        "quaternion_order_interpretation": convention,
        "camera_data_raw_quaternion": camera_quaternion_data_xyzw.tolist(),
        "selected_quaternion_xyzw": quaternion_xyzw.tolist(),
    }
    summary["calibration_score"] = score
    role_calibration_valid = bool(
        summary["semantic_role_calibration"]
        and all(
            item["extent_normalized_residual"] <= 1.5
            for item in summary["semantic_role_calibration"]
        )
    )
    summary["calibration_valid"] = bool(
        np.isfinite(score) and score <= 1.5 and role_calibration_valid
    )
    if not summary["calibration_valid"]:
        summary["available"] = False
        summary["error"] = (
            "RGB-D/base-frame calibration failed semantic-role residual checks"
        )
    return summary


def _rotate_vector_wxyz(
    quaternion_wxyz: torch.Tensor, vector: torch.Tensor
) -> torch.Tensor:
    quaternion = quaternion_wxyz / torch.linalg.vector_norm(quaternion_wxyz)
    xyz = quaternion[1:]
    twice_cross = 2.0 * torch.linalg.cross(xyz, vector)
    return vector + quaternion[0] * twice_cross + torch.linalg.cross(xyz, twice_cross)


def _contact_geometry_local_from_usd(
    contact_body_names: Sequence[str],
) -> dict[str, Any] | None:
    """Resolve configured contact-shape centers in the controlled-frame basis."""
    global ACTUATOR_CONTACT_LOCAL_GEOMETRY
    if ACTUATOR_CONTACT_LOCAL_GEOMETRY is not None:
        return ACTUATOR_CONTACT_LOCAL_GEOMETRY
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return None
    robot_prim = stage.GetPrimAtPath("/World/envs/env_0/robot")
    if not robot_prim.IsValid():
        return None
    contact_prims: dict[str, Usd.Prim] = {}
    controlled_frame_prim: Usd.Prim | None = None
    contact_names = set(contact_body_names)
    for prim in Usd.PrimRange(robot_prim):
        if prim.GetName() in contact_names:
            contact_prims.setdefault(prim.GetName(), prim)
        if (
            prim.GetName() == "base_link"
            and "Robotiq_2F_85" in str(prim.GetPath())
        ):
            controlled_frame_prim = prim
    if controlled_frame_prim is None or len(contact_prims) < 2:
        return None
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    base_to_world = xform_cache.GetLocalToWorldTransform(controlled_frame_prim)
    world_to_base = base_to_world.GetInverse()
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
    )
    centers_local: dict[str, list[float]] = {}
    for body_name in contact_body_names:
        prim = contact_prims.get(body_name)
        if prim is None:
            continue
        world_range = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        center_world = world_range.GetMidpoint()
        center_local = world_to_base.Transform(center_world)
        centers_local[body_name] = [float(value) for value in center_local]
    if len(centers_local) < 2:
        return None
    ordered = [
        torch.tensor(centers_local[name], dtype=torch.float32)
        for name in contact_body_names
        if name in centers_local
    ]
    stacked = torch.stack(ordered)
    center_local = torch.mean(stacked, dim=0)
    separation_local = ordered[1] - ordered[0]
    span = float(torch.linalg.vector_norm(separation_local))
    if span <= 1.0e-6:
        return None
    ACTUATOR_CONTACT_LOCAL_GEOMETRY = {
        "contact_body_centers_local_m": centers_local,
        "contact_center_local_m": center_local.tolist(),
        "closing_axis_local": (separation_local / span).tolist(),
        "contact_body_geometry_center_span_m": span,
    }
    return ACTUATOR_CONTACT_LOCAL_GEOMETRY


def _actuator_contact_geometry(env: Any, eef: torch.Tensor) -> dict[str, Any]:
    """Describe the configured contact bodies without a model-side embodiment."""
    runtime_info = contact_sensor_runtime_info(env)
    contact_body_names = list(runtime_info.get("body_names") or [])
    local_geometry = _contact_geometry_local_from_usd(contact_body_names)
    if local_geometry is None:
        return {
            "available": False,
            "source": "configured_contact_prim_bounds",
            "frame": "robot_root",
        }
    eef_quaternion = _eef_quaternion(env)
    contact_center_offset = _rotate_vector_wxyz(
        eef_quaternion,
        torch.tensor(local_geometry["contact_center_local_m"]),
    )
    contact_center = eef + contact_center_offset
    closing_axis = _rotate_vector_wxyz(
        eef_quaternion,
        torch.tensor(local_geometry["closing_axis_local"]),
    )
    body_centers_root: dict[str, list[float]] = {}
    for body_name, local_center in local_geometry[
        "contact_body_centers_local_m"
    ].items():
        body_centers_root[body_name] = (
            eef
            + _rotate_vector_wxyz(
                eef_quaternion,
                torch.tensor(local_center, dtype=torch.float32),
            )
        ).tolist()
    result: dict[str, Any] = {
        "available": True,
        "source": "configured_contact_prim_bounds_plus_live_controlled_frame",
        "frame": "robot_root",
        "controlled_frame_xyz_m": eef.tolist(),
        "contact_center_xyz_m": contact_center.tolist(),
        "controlled_frame_to_contact_center_m": contact_center_offset.tolist(),
        "contact_body_geometry_centers_xyz_m": body_centers_root,
        "closing_axis_robot_root": closing_axis.tolist(),
        **local_geometry,
    }
    return result


def _state(env: Any, initial_object_z: float) -> dict[str, Any]:
    movable_object = _movable_object_position(env)
    target_receptacle = _target_receptacle_position(env)
    eef = _eef_position(env)
    fingertip = eef + torch.tensor([0.0, 0.0, -GRIPPER_BASE_TO_FINGERTIP_M])
    robot = env.scene["robot"]
    finger_index = robot.data.joint_names.index("finger_joint")
    joint_pos = robot.data.joint_pos
    joint_pos = getattr(joint_pos, "torch", joint_pos)
    finger_joint_rad = float(joint_pos[0, finger_index].detach().cpu())
    closed_fraction = max(0.0, min(1.0, finger_joint_rad / (np.pi / 4)))
    fingertip_distance = float(torch.linalg.vector_norm(fingertip - movable_object))
    object_lift = float(movable_object[2]) - initial_object_z
    target_xy_error = float(
        torch.linalg.vector_norm(target_receptacle[:2] - movable_object[:2])
    )
    height_above_target = float(movable_object[2] - target_receptacle[2])
    feedback = derive_manipulation_feedback(
        gripper_closed_fraction=closed_fraction,
        fingertip_object_distance_m=fingertip_distance,
        object_lift_m=object_lift,
        object_target_xy_error_m=target_xy_error,
        object_height_above_target_m=height_above_target,
        contact_height_m=args_cli.plate_contact_height,
    )
    goal_relation_satisfied = bool(
        feedback["object_target_contact_proxy"]
        or (
            target_xy_error <= args_cli.center_tolerance
            and abs(height_above_target - args_cli.release_height)
            <= args_cli.release_height_tolerance
        )
    )
    try:
        rgbd_scene_geometry = _rgbd_scene_geometry_observation(env)
    except (KeyError, ValueError, np.linalg.LinAlgError) as exc:
        rgbd_scene_geometry = {
            "available": False,
            "source": "synchronized_rgbd_instance_geometry",
            "frame": "robot_root",
            "error": str(exc),
            "geometries": [],
        }
    actuator_contact_geometry = _actuator_contact_geometry(env, eef)
    try:
        pregrasp_axis_alignment = pregrasp_axis_alignment_observation(
            scene_geometry=rgbd_scene_geometry,
            actuator_geometry=actuator_contact_geometry,
            object_runtime_id=SCENE_ROLES.movable_object_asset,
            maximum_error_deg=args_cli.maximum_pregrasp_axis_error_deg,
        )
    except (TypeError, ValueError) as exc:
        pregrasp_axis_alignment = {
            "available": False,
            "source": (
                "rgbd_oriented_footprint_plus_runtime_contact_geometry"
            ),
            "object_runtime_id": SCENE_ROLES.movable_object_asset,
            "error": str(exc),
        }
    return {
        "scene_roles": SCENE_ROLES.to_dict(),
        "movable_object_xyz": movable_object.tolist(),
        "movable_object_quaternion_wxyz": _movable_object_quaternion(env).tolist(),
        "target_receptacle_xyz": target_receptacle.tolist(),
        "eef_gripper_base_xyz": eef.tolist(),
        "eef_gripper_base_quaternion_wxyz": _eef_quaternion(env).tolist(),
        "gripper_fingertip_center_xyz": fingertip.tolist(),
        "gripper_base_to_fingertip_m": GRIPPER_BASE_TO_FINGERTIP_M,
        "fingertip_object_distance_m": fingertip_distance,
        "actuator_contact_geometry": actuator_contact_geometry,
        "rgbd_scene_geometry": rgbd_scene_geometry,
        "pregrasp_axis_alignment": pregrasp_axis_alignment,
        "finger_joint_rad": finger_joint_rad,
        "gripper_closed_fraction": closed_fraction,
        "current_contact": _current_contact_observation(env),
        "object_lift_m": object_lift,
        "object_target_xy_error_m": target_xy_error,
        "object_height_above_target_m": height_above_target,
        "gripper_closure_obstructed": feedback["gripper_closure_obstructed"],
        "grasp_candidate": feedback["grasp_candidate"],
        "grasp_confirmed": feedback["grasp_confirmed"],
        "object_target_contact_proxy": feedback["object_target_contact_proxy"],
        "goal_relation": {
            "satisfied": goal_relation_satisfied,
            "source": "runtime_task_relation_adapter",
            "evidence": {
                "target_contact_proxy": feedback[
                    "object_target_contact_proxy"
                ],
                "target_xy_error_m": target_xy_error,
                "maximum_target_xy_error_m": args_cli.center_tolerance,
                "object_height_above_target_m": height_above_target,
                "target_release_height_m": args_cli.release_height,
                "release_height_tolerance_m": (
                    args_cli.release_height_tolerance
                ),
            },
        },
    }


def _runtime_scene_entity_physical_evidence(
    env: Any,
    state: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Publish mobility and mass for every entity in the current inventory."""

    entity_ids: set[str] = set()
    geometries = state.get("rgbd_scene_geometry", {}).get("geometries", [])
    if isinstance(geometries, list):
        entity_ids.update(
            str(item["runtime_id"]).strip()
            for item in geometries
            if isinstance(item, Mapping)
            and isinstance(item.get("runtime_id"), str)
            and str(item["runtime_id"]).strip()
        )
    scene_roles = state.get("scene_roles", {})
    if isinstance(scene_roles, Mapping):
        entity_ids.update(
            str(role["asset"]).strip()
            for role in scene_roles.values()
            if isinstance(role, Mapping)
            and isinstance(role.get("asset"), str)
            and str(role["asset"]).strip()
        )

    stage = omni.usd.get_context().get_stage()
    rigid_objects = getattr(env.scene, "rigid_objects", {})
    deformable_objects = getattr(env.scene, "deformable_objects", {})
    result: dict[str, dict[str, Any]] = {}
    for entity_id in sorted(entity_ids):
        prim_path = f"/World/envs/env_0/scene/{entity_id}"
        root = stage.GetPrimAtPath(prim_path) if stage is not None else None
        prim_observed = bool(root is not None and root.IsValid())
        rigid_body_records: list[dict[str, Any]] = []
        if prim_observed:
            for prim in Usd.PrimRange(root):
                if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    continue
                rigid_body_api = UsdPhysics.RigidBodyAPI(prim)
                raw_enabled = rigid_body_api.GetRigidBodyEnabledAttr().Get()
                raw_kinematic = rigid_body_api.GetKinematicEnabledAttr().Get()
                rigid_body_records.append(
                    {
                        "prim_path": str(prim.GetPath()),
                        "enabled": (
                            True if raw_enabled is None else bool(raw_enabled)
                        ),
                        "kinematic": (
                            False
                            if raw_kinematic is None
                            else bool(raw_kinematic)
                        ),
                    }
                )

        runtime_asset = (
            rigid_objects.get(entity_id)
            if isinstance(rigid_objects, Mapping)
            else None
        )
        mass_kg: float | None = None
        if runtime_asset is not None:
            try:
                body_mass = getattr(runtime_asset.data.body_mass, "torch", None)
                if body_mass is None:
                    body_mass = runtime_asset.data.body_mass
                if not isinstance(body_mass, torch.Tensor):
                    body_mass = torch.as_tensor(body_mass)
                candidate_mass = float(
                    torch.sum(body_mass[0].detach().cpu())
                )
                if math.isfinite(candidate_mass) and candidate_mass >= 0.0:
                    mass_kg = candidate_mass
            except (AttributeError, IndexError, TypeError, ValueError):
                mass_kg = None

        result[entity_id] = build_entity_physical_evidence(
            entity_id=entity_id,
            prim_path=prim_path if prim_observed else None,
            rigid_body_records=rigid_body_records,
            registered_dynamic=(
                isinstance(rigid_objects, Mapping)
                and entity_id in rigid_objects
            ),
            registered_deformable=(
                isinstance(deformable_objects, Mapping)
                and entity_id in deformable_objects
            ),
            prim_observed=prim_observed,
            mass_kg=mass_kg,
            mass_source=(
                "live_physx_body_mass" if mass_kg is not None else None
            ),
        )
    return result


def _runtime_task_capability_evidence(
    env: Any,
    state: dict[str, Any],
    motion_registry: MotionExecutorRegistry,
    actuator_registry: ActuatorExecutorRegistry,
) -> dict[str, Any]:
    """Publish task-neutral physical evidence from active runtime adapters."""

    def torch_view(value: Any) -> torch.Tensor:
        value = getattr(value, "torch", value)
        if not isinstance(value, torch.Tensor):
            value = torch.as_tensor(value)
        return value.detach().cpu()

    def subtree_material_evidence(prim_path: str) -> dict[str, Any]:
        stage = omni.usd.get_context().get_stage()
        root = stage.GetPrimAtPath(prim_path) if stage is not None else None
        if root is None or not root.IsValid():
            return {
                "available": False,
                "source": "active_usd_physics_materials",
            }
        static_values: list[float] = []
        dynamic_values: list[float] = []
        combine_modes: set[str] = set()
        for prim in Usd.PrimRange(root):
            static_value = prim.GetAttribute("physics:staticFriction").Get()
            dynamic_value = prim.GetAttribute("physics:dynamicFriction").Get()
            combine_mode = prim.GetAttribute(
                "physxMaterial:frictionCombineMode"
            ).Get()
            if isinstance(static_value, (int, float)) and math.isfinite(
                float(static_value)
            ):
                static_values.append(float(static_value))
            if isinstance(dynamic_value, (int, float)) and math.isfinite(
                float(dynamic_value)
            ):
                dynamic_values.append(float(dynamic_value))
            if isinstance(combine_mode, str) and combine_mode:
                combine_modes.add(combine_mode)
        return {
            "available": bool(static_values or dynamic_values),
            "source": "active_usd_physics_materials",
            "minimum_static_friction": (
                min(static_values) if static_values else None
            ),
            "minimum_dynamic_friction": (
                min(dynamic_values) if dynamic_values else None
            ),
            "combine_modes": sorted(combine_modes),
        }

    def live_opposing_force_capability(
        *,
        actuator_geometry: Mapping[str, Any],
        joint_effort_limit: float,
        dynamic_friction: float | None,
        gravity_m_s2: float | None,
    ) -> dict[str, Any]:
        try:
            contact_centers = actuator_geometry.get(
                "contact_body_geometry_centers_xyz_m"
            )
            closing_axis = actuator_geometry.get("closing_axis_robot_root")
            if not isinstance(contact_centers, Mapping) or len(contact_centers) != 2:
                raise MotionToolValidationError(
                    "two runtime contact-body geometry centers are required"
                )
            robot = env.scene["robot"]
            finger_joint_index = robot.data.joint_names.index("finger_joint")
            jacobian = torch_view(robot.data.body_link_jacobian_w)[0]
            body_positions_w = torch_view(robot.data.body_pos_w)[0]
            root_position_w = torch_view(robot.data.root_pos_w)[0]
            root_quaternion_xyzw = torch_view(robot.data.root_quat_w)[0]
            root_quaternion_wxyz = _xyzw_to_wxyz(root_quaternion_xyzw)
            jacobian_joint_index = finger_joint_index + robot.num_base_dofs
            point_jacobians: list[list[float]] = []
            for body_name, center_root in contact_centers.items():
                body_index = robot.data.body_names.index(body_name)
                jacobian_body_index = (
                    body_index - 1 if robot.is_fixed_base else body_index
                )
                body_jacobian_column = jacobian[
                    jacobian_body_index, :, jacobian_joint_index
                ]
                point_root = torch.tensor(center_root, dtype=torch.float32)
                point_w = root_position_w + _rotate_vector_wxyz(
                    root_quaternion_wxyz, point_root
                )
                radius_w = point_w - body_positions_w[body_index]
                point_linear_jacobian = (
                    body_jacobian_column[:3]
                    + torch.cross(
                        body_jacobian_column[3:], radius_w, dim=0
                    )
                )
                point_jacobians.append(point_linear_jacobian.tolist())
            axis_root = torch.tensor(closing_axis, dtype=torch.float32)
            axis_w = _rotate_vector_wxyz(root_quaternion_wxyz, axis_root)
            result = opposing_contact_force_capacity(
                joint_effort_limit=joint_effort_limit,
                contact_point_linear_jacobian_columns=point_jacobians,
                closing_axis=axis_w.tolist(),
                effective_dynamic_friction=dynamic_friction,
                gravity_m_s2=gravity_m_s2,
            )
            result["available"] = True
            result["friction_source"] = (
                "conservative_minimum_of_active_contact_materials"
                if dynamic_friction is not None
                else None
            )
            return result
        except (
            AttributeError,
            IndexError,
            KeyError,
            MotionToolValidationError,
            TypeError,
            ValueError,
        ) as error:
            return {
                "available": False,
                "source": "live_contact_point_jacobian_virtual_work",
                "error": str(error),
            }

    movable_asset = env.scene[SCENE_ROLES.movable_object_asset]
    try:
        body_mass = torch_view(movable_asset.data.body_mass)
        object_mass_kg = float(torch.sum(body_mass[0]))
    except (AttributeError, IndexError, TypeError, ValueError):
        object_mass_kg = None
    gravity = getattr(getattr(env.sim, "cfg", None), "gravity", None)
    if (
        isinstance(gravity, Sequence)
        and not isinstance(gravity, (str, bytes))
        and len(gravity) == 3
    ):
        gravity_magnitude = float(np.linalg.norm(np.asarray(gravity, dtype=float)))
    else:
        gravity_magnitude = None
    object_weight_n = (
        object_mass_kg * gravity_magnitude
        if object_mass_kg is not None and gravity_magnitude is not None
        else None
    )
    actuator_material = subtree_material_evidence(
        "/World/envs/env_0/robot"
    )
    object_material = subtree_material_evidence(
        f"/World/envs/env_0/scene/{SCENE_ROLES.movable_object_asset}"
    )
    dynamic_friction_values = [
        float(value)
        for value in (
            actuator_material.get("minimum_dynamic_friction"),
            object_material.get("minimum_dynamic_friction"),
        )
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    ]
    effective_dynamic_friction = (
        min(dynamic_friction_values) if dynamic_friction_values else None
    )

    geometries = state.get("rgbd_scene_geometry", {}).get("geometries", [])

    def visible_geometry(runtime_id: str) -> dict[str, Any] | None:
        return next(
            (
                dict(item)
                for item in geometries
                if isinstance(item, Mapping)
                and item.get("runtime_id") == runtime_id
            ),
            None,
        )

    robot = env.scene["robot"]
    finger_index = robot.data.joint_names.index("finger_joint")
    joint_pos = torch_view(robot.data.joint_pos)[0]
    joint_limits = torch_view(robot.data.soft_joint_pos_limits)[0]
    effort_limits = torch_view(robot.data.joint_effort_limits)[0]
    motion_joint_margins: list[float] = []
    for index in range(len(joint_pos)):
        if index == finger_index:
            continue
        lower = float(joint_limits[index, 0])
        upper = float(joint_limits[index, 1])
        width = upper - lower
        if not math.isfinite(width) or width <= 0.0:
            continue
        position = float(joint_pos[index])
        motion_joint_margins.append(
            max(0.0, min(position - lower, upper - position) / width)
        )
    actuator_geometry = state.get("actuator_contact_geometry", {})
    force_capability = live_opposing_force_capability(
        actuator_geometry=actuator_geometry,
        joint_effort_limit=float(effort_limits[finger_index]),
        dynamic_friction=effective_dynamic_friction,
        gravity_m_s2=gravity_magnitude,
    )
    eef_xyz = np.asarray(state["eef_gripper_base_xyz"], dtype=np.float64)
    object_xyz = np.asarray(state["movable_object_xyz"], dtype=np.float64)
    target_xyz = np.asarray(state["target_receptacle_xyz"], dtype=np.float64)
    return {
        "source": "active_simulator_and_runtime_capability_adapters",
        "identity_fields_in_contract": False,
        "motion": {
            "registered_executors": [
                {
                    "executor_id": spec.executor_id,
                    "configuration_schema": spec.configuration_schema,
                }
                for spec in motion_registry.specs()
            ],
            "controlled_dof_count": len(motion_joint_margins),
            "minimum_normalized_joint_limit_margin": (
                min(motion_joint_margins) if motion_joint_margins else None
            ),
            "current_eef_base_radius_m": float(np.linalg.norm(eef_xyz)),
            "movable_object_base_radius_m": float(np.linalg.norm(object_xyz)),
            "target_receptacle_base_radius_m": float(np.linalg.norm(target_xyz)),
            "eef_to_movable_object_root_m": float(
                np.linalg.norm(eef_xyz - object_xyz)
            ),
            "eef_to_target_receptacle_root_m": float(
                np.linalg.norm(eef_xyz - target_xyz)
            ),
            "rated_workspace_envelope": None,
            "rated_workspace_envelope_status": "not_published_by_active_adapter",
        },
        "actuator": {
            "registered_executors": [
                {
                    "executor_id": spec.executor_id,
                    "command_schema": spec.command_schema,
                    "configuration_schema": spec.configuration_schema,
                }
                for spec in actuator_registry.specs()
            ],
            "contact_body_count": len(
                actuator_geometry.get("contact_body_geometry_centers_xyz_m", {})
            ),
            "open_contact_body_center_span_m": actuator_geometry.get(
                "contact_body_geometry_center_span_m"
            ),
            "closing_axis_robot_root": actuator_geometry.get(
                "closing_axis_robot_root"
            ),
            "controlled_joint_position_limits": (
                joint_limits[finger_index].tolist()
            ),
            "sim_joint_effort_limit": float(effort_limits[finger_index]),
            "sim_joint_effort_unit": "rotary_joint_torque_n_m",
            "command_retention": "position_target_held_each_environment_step",
            "contact_material": actuator_material,
            "continuous_opposing_force_capability": force_capability,
            "continuous_normal_force_capacity_n": force_capability.get(
                "total_opposing_normal_force_n"
            ),
            "continuous_normal_force_capacity_status": (
                "derived_from_live_contact_jacobians"
                if force_capability.get("available")
                else "not_published_by_active_adapter"
            ),
            "rated_payload_limit_kg": None,
            "rated_payload_limit_status": "not_published_by_active_adapter",
            "physics_derived_payload_capacity_kg": force_capability.get(
                "physics_derived_payload_capacity_kg"
            ),
        },
        "movable_object": {
            "mass_kg": object_mass_kg,
            "gravity_m_s2": gravity_magnitude,
            "weight_n": object_weight_n,
            "contact_material": object_material,
            "visible_rgbd_geometry": visible_geometry(
                SCENE_ROLES.movable_object_asset
            ),
        },
        "target_receptacle": {
            "visible_rgbd_geometry": visible_geometry(
                SCENE_ROLES.target_receptacle_asset
            )
        },
        "evidence_limitations": [
            *(["rated_workspace_envelope"]),
            *(
                []
                if force_capability.get("available")
                else ["continuous_normal_force_capacity_n"]
            ),
            "rated_payload_limit_kg",
        ],
    }


def _scene_prompt(
    state: dict[str, Any],
    capability_evidence: dict[str, Any],
    observation_id: str,
    critic_context: str,
) -> str:
    return f"""You are a visual physical-feasibility governor.
Human instruction: {args_cli.instruction}
Semantic scene roles:
{json.dumps(SCENE_ROLES.to_dict(), indent=2)}

Fresh simulator state in robot-root coordinates (meters):
{json.dumps(state, indent=2)}

Runtime-published physical capability evidence:
{json.dumps(capability_evidence, indent=2)}

Fresh observation token: {observation_id}

Before any motion, answer four independent physical questions through exactly
one assess_task_feasibility tool call: can the interaction and destination be
reached, can the active actuator form the required grasp geometry, can it
sustain the object's load continuously, and can the whole instruction be
completed under the observed constraints? Object visibility or arm reach alone
does not prove grasp or payload feasibility. Compare the visible object extents
and axes with the actuator contact span and closing axis. Distinguish a held
position command and joint effort limit from a measured/rated continuous normal
grip-force capacity. Do not convert torque to fingertip force without a
published transmission model. A capacity derived by the runtime from live
contact-point Jacobians and virtual work is admissible simulator evidence; cite
its assumptions and compare its friction-supported load with object weight.
The lack of a manufacturer-rated payload is still a limitation but does not by
itself invalidate a fully available simulator-derived capacity. Other missing
essential evidence remains unknown; do not guess it or mark an unknown category
feasible. motion_authorized may be true only when
both scene roles are visible and all four feasibility categories are feasible.
List missing measurements in required_runtime_evidence and physical blockers in
blocking_reasons. recommended_operations describes a capability-level plan, not
a fixed phase schedule or joint command.

Inspect the attached current multi-camera RGB-D image and issue only the native
tool call. Do not output joint commands or an embodiment-specific plan.

{critic_context}"""


def _choose_observation_bound_task_feasibility(
    provider: GeminiProvider,
    *,
    frame: np.ndarray,
    state: dict[str, Any],
    capability_evidence: dict[str, Any],
    critic_context: str,
) -> tuple[dict[str, Any], float, str]:
    """Ask the model one fail-closed, fresh-observation feasibility question."""
    encoded, digest = _encode_frame(frame)
    observation_id = f"task-feasibility:{digest}"
    gate = ObservationBoundTaskFeasibilityGate(observation_id=observation_id)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a physical task-feasibility governor. Respond with "
                "exactly one runtime-advertised native tool call."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": _scene_prompt(
                        state,
                        capability_evidence,
                        observation_id,
                        critic_context,
                    ),
                },
            ],
        },
    ]
    started = time.perf_counter()
    response = asyncio.run(
        asyncio.wait_for(
            provider.complete(
                messages,
                {
                    "tools": task_feasibility_tool_schema(observation_id),
                    "tool_choice": "required",
                },
            ),
            timeout=args_cli.timeout,
        )
    )
    latency = time.perf_counter() - started
    tool_calls = response.tool_calls or []
    if len(tool_calls) != 1:
        raise RuntimeError(
            "task feasibility model must issue exactly one native tool call; "
            f"received {len(tool_calls)}"
        )
    outcome = gate.dispatch(tool_calls[0])
    result = outcome.to_dict()
    result["scene_ok"] = bool(
        outcome.movable_object_visible and outcome.target_receptacle_visible
    )
    result["assessment"] = outcome.reason
    result["recommended_sequence"] = list(outcome.recommended_operations)
    return result, latency, digest


def _stage_prompt(
    phase: str,
    state: dict[str, Any],
    nominal_target: torch.Tensor,
    nominal_quaternion_wxyz: torch.Tensor,
    gripper_closed: bool,
    critic_context: str,
) -> str:
    distance = float(
        torch.linalg.norm(torch.tensor(state["eef_gripper_base_xyz"]) - nominal_target)
    )
    orientation_error_degrees = float(
        torch.rad2deg(
            torch.linalg.vector_norm(
                quaternion_error_axis_angle_wxyz(
                    nominal_quaternion_wxyz,
                    torch.tensor(state["eef_gripper_base_quaternion_wxyz"]),
                )
            )
        )
    )
    return f"""You are Gemini Robotics ER 2 acting as a closed-loop robot coach.
Human instruction: {args_cli.instruction}
Semantic scene roles: {json.dumps(SCENE_ROLES.to_dict())}
Current phase: {phase}
This is a FRESH observation captured after the previous phase completed.

Privileged simulator state in robot-root coordinates (meters):
{json.dumps(state, indent=2)}
Calibrated nominal Cartesian target: {nominal_target.tolist()}
Current EEF-to-target distance: {distance:.4f} m
Object-relative target quaternion (wxyz): {nominal_quaternion_wxyz.tolist()}
Current orientation error: {orientation_error_degrees:.2f} degrees
Requested gripper state: {"closed" if gripper_closed else "open"}

Capability-reported tool geometry distinguishes eef_gripper_base_xyz from
gripper_fingertip_center_xyz and gives their measured offset. Judge interaction
at the reported contact geometry, not from mounting-base height, and do not
assume the initial orientation is the only valid grasp orientation.
The actuator_contact_geometry is measured from the runtime-configured contact
bodies. Compare its closing_axis_robot_root with rgbd_scene_geometry object axes
and extents when choosing a new grasp orientation. RGB-D scene geometry is
visible-surface evidence in robot-root coordinates; keep the actuator geometry
outside non-task scene surfaces while allowing only the contact needed by the
human instruction.
The measured gripper_closed_fraction is authoritative: values near 1.0 mean
fully closed and values near 0.0 mean fully open. After the grasp phase, the
close command has already been issued; a fraction around 0.10-0.50 can mean the
the movable object is physically blocking further finger travel and is positive contact
evidence, not an open-gripper command. It is not sufficient proof of retention.
When current_contact.contact_bodies is available for a multi-contact clamp,
compare the named per-body force directions: opposing multi-body contact can
support a pinch, while one active body or predominantly same-direction pressure
can indicate surface contact. At phase "lift", execute the lift test only when
the image, actuator position, and per-body contact evidence together support
retention. Explicitly use pairwise_force_direction_cosine and
force_magnitude_ratio_min_over_max in the assessment when they are available;
positive cosine is same-direction pressure, not an opposing pinch. Otherwise
retry so the fresh motion tool can alter position and/or
orientation before another engagement.
The fused grasp_candidate field means closure was obstructed near the movable object;
grasp_confirmed means the movable object measurably followed the lift. If
object_target_contact_proxy is true, the object has reached the target envelope:
do not request more lowering, and execute release when XY placement is valid.

Inspect the attached current multi-camera image. The calibrated executor will
move toward this live-pose Cartesian target with bounded local Jacobian IK; you
are not controlling individual joints or individual simulator frames.

For phase "grasp", set grasp_ready=true only when the image and measured distance
support closing around the movable object.

{critic_context}

Return ONLY JSON:
{{
  "decision": "execute" or "retry" or "abort",
  "grasp_ready": true or false,
  "confidence": 0.0,
  "assessment": "brief visual/state reasoning"
}}"""


def _motion_checkpoint_prompt(
    phase: str,
    state: dict[str, Any],
    checkpoint: dict[str, Any],
) -> str:
    return f"""You are Gemini Robotics ER 2 supervising a robot during motion.
Human instruction: {args_cli.instruction}
Semantic scene roles: {json.dumps(SCENE_ROLES.to_dict())}
Current phase: {phase}

This is a FRESH mid-motion observation, not a phase-boundary image.
Current simulator state:
{json.dumps(state, indent=2)}

Local safety/checkpoint metrics:
{json.dumps(checkpoint, indent=2)}

The local monitor has immediate stop authority. Inspect the image for gripper
slip, dropped object, unexpected contact, or an obstructed route. Return ONLY:
{{
  "decision": "execute" or "pause_regrasp" or "complete" or "abort",
  "confidence": 0.0,
  "assessment": "brief fresh visual/state reasoning"
}}
When current_contact.contact_bodies is available, use its named per-body force
directions to distinguish opposing retained contact from one-body or
same-direction surface pressure. Aggregate touch alone does not prove that an
object is retained. Use pairwise_force_direction_cosine and
force_magnitude_ratio_min_over_max when available; positive cosine means the
forces are same-direction rather than opposing.
When RGB-D safety is enabled, the attached image is RGB on the left and a
near-to-far TURBO depth visualization on the right; numeric depth summaries
are included above. Use execute only when it is safe to continue the current
bounded motion. Use complete when the image and state show that the movable object is
already on the target receptacle and no more grasp/transport motion is needed."""


def _local_dls_executor_registry(
    trackable_object_ids: Sequence[str] = (),
) -> MotionExecutorRegistry:
    """Register the currently available executor and its configurable surface."""
    normalized_object_ids = sorted(
        {
            str(object_id)
            for object_id in trackable_object_ids
            if isinstance(object_id, str) and object_id
        }
    )
    configuration_properties: dict[str, Any] = {
        "position_tolerance_m": {
            "type": "number", "minimum": 0.001, "maximum": 0.05,
        },
        "translation_step_limit_m": {
            "type": "number", "minimum": 0.001, "maximum": 0.05,
        },
        "maximum_iterations": {
            "type": "integer", "minimum": 1, "maximum": 400,
        },
        "settle_steps": {
            "type": "integer", "minimum": 1, "maximum": 60,
        },
        "joint_step_limit_rad": {
            "type": "number", "minimum": 0.001, "maximum": 0.20,
        },
        "damping": {
            "type": "number", "minimum": 0.001, "maximum": 1.0,
        },
        "orientation_tolerance_deg": {
            "type": "number", "minimum": 0.1, "maximum": 30.0,
        },
        "rotation_step_limit_deg": {
            "type": "number", "minimum": 0.1, "maximum": 30.0,
        },
        "minimum_progress_m": {
            "type": "number", "minimum": 0.0001, "maximum": 0.01,
        },
        "maximum_stalled_observations": {
            "type": "integer", "minimum": 2, "maximum": 20,
        },
        "require_contact": {"type": "boolean"},
        "minimum_contact_force_n": {
            "type": "number", "minimum": 0.0, "maximum": 100.0,
        },
        "maximum_tracked_pose_error_m": {
            "type": "number", "minimum": 0.001, "maximum": 0.30,
        },
        "maximum_tracked_orientation_error_deg": {
            "type": "number", "minimum": 1.0, "maximum": 90.0,
        },
        "minimum_observed_clearance_m": {
            "type": "number", "minimum": 0.0, "maximum": 0.50,
        },
    }
    if normalized_object_ids:
        configuration_properties["tracked_object_id"] = {
            "type": "string",
            "enum": normalized_object_ids,
            "description": (
                "Runtime-advertised object whose RGB-D orientation lease is "
                "being configured."
            ),
        }
    registry = MotionExecutorRegistry()
    registry.register(
        MotionExecutorSpec(
            executor_id="bounded_dls_ik",
            tool_name="execute_bounded_dls_ik",
            description=(
                "Execute the current world-space target with bounded damped "
                "least-squares inverse kinematics. The target controls the pose "
                "reported as eef_gripper_base_xyz in observed state; it is not "
                "the fingertip or object position. Account for the observed "
                "gripper_base_to_fingertip_m offset before correcting it. "
                "Optionally correct the target and configure this invocation "
                "from the fresh observation."
            ),
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": configuration_properties,
            },
            capability_tags=SPATIAL_MOTION_CAPABILITY_TAGS,
            invocation_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_position_m": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "target_quaternion_wxyz": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                },
                "required": [
                    "target_position_m",
                    "target_quaternion_wxyz",
                ],
                "x-runtime-constraints": {
                    "coordinate_frame": "robot_root",
                    "workspace_min_m": [-0.75, -0.75, 0.02],
                    "workspace_max_m": [0.90, 0.90, 1.40],
                    "maximum_displacement_m": 0.80,
                    "maximum_grounding_offset_m": 0.35,
                    "maximum_alignment_error_deg": 15.0,
                },
            },
        )
    )
    return registry


def _local_binary_actuator_registry() -> ActuatorExecutorRegistry:
    """Register the current runtime actuator without changing the protocol."""
    registry = ActuatorExecutorRegistry()
    registry.register(
        ActuatorExecutorSpec(
            executor_id="binary_end_effector_clamp",
            tool_name="execute_binary_end_effector_clamp",
            description=(
                "Command the current binary end-effector clamp from fresh "
                "visual, actuator, and contact evidence. Engage closes the "
                "clamp, disengage opens it, and maintain preserves its current "
                "command. Configure how long the command settles before the "
                "next observation."
            ),
            command_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": ["engage", "disengage", "maintain"],
                    }
                },
                "required": ["state"],
            },
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "settle_steps": {
                        "type": "integer",
                        "minimum": 8,
                        "maximum": 120,
                    }
                },
            },
            capability_tags=REVERSIBLE_ATTACHMENT_CAPABILITY_TAGS,
        )
    )
    return registry


def _motion_lease_conditions_from_config(
    config: dict[str, Any],
) -> MotionLeaseConditions:
    """Build the generic lease contract from one executor's optional settings."""
    return MotionLeaseConditions(
        require_contact=bool(config.get("require_contact", False)),
        minimum_contact_force_n=float(config.get("minimum_contact_force_n", 0.0)),
        maximum_tracked_pose_error_m=config.get(
            "maximum_tracked_pose_error_m"
        ),
        minimum_observed_clearance_m=config.get(
            "minimum_observed_clearance_m"
        ),
    )


def _contact_retained_predicate(
    values: dict[str, Any], parameters: dict[str, Any]
) -> PredicateResult:
    touch = values["gripper.touch"]
    force = values["gripper.contact_force_n"]
    minimum = float(parameters.get("minimum_force_n", 0.0))
    if not isinstance(touch, bool):
        raise ValueError("gripper.touch must be boolean")
    if touch is not True:
        return PredicateResult(False, "contact_lost", {"touch": touch})
    if isinstance(force, bool) or not isinstance(force, (int, float)):
        raise ValueError("gripper.contact_force_n must be numeric")
    if float(force) < minimum:
        return PredicateResult(
            False,
            "contact_force_below_lease_minimum",
            {"contact_force_n": float(force), "minimum_force_n": minimum},
        )
    return PredicateResult(
        True,
        "contact_retained",
        {"touch": touch, "contact_force_n": float(force)},
    )


def _numeric_bound_predicate(
    values: dict[str, Any],
    parameters: dict[str, Any],
    *,
    channel: str,
    bound_name: str,
    valid_reason: str,
    invalid_reason: str,
    maximum: bool,
) -> PredicateResult:
    observed = values[channel]
    bound = parameters[bound_name]
    if (
        isinstance(observed, bool)
        or not isinstance(observed, (int, float))
        or isinstance(bound, bool)
        or not isinstance(bound, (int, float))
    ):
        raise ValueError("numeric predicate values must be numbers")
    observed = float(observed)
    bound = float(bound)
    if not math.isfinite(observed) or not math.isfinite(bound):
        raise ValueError("numeric predicate values must be finite")
    valid = observed <= bound if maximum else observed >= bound
    return PredicateResult(
        valid,
        valid_reason if valid else invalid_reason,
        {channel: observed, bound_name: bound},
    )


def _motion_progress_predicate(
    values: dict[str, Any], parameters: dict[str, Any]
) -> PredicateResult:
    stalled = values["motion.stalled_observation_count"]
    maximum = parameters["maximum_stalled_observations"]
    if (
        isinstance(stalled, bool)
        or not isinstance(stalled, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
    ):
        raise ValueError("motion stall counts must be integers")
    valid = stalled < maximum
    return PredicateResult(
        valid,
        "motion_progress_observed" if valid else "motion_progress_stalled",
        {
            "stalled_observation_count": stalled,
            "maximum_stalled_observations": maximum,
        },
    )


def _collision_stop_clear_predicate(
    values: dict[str, Any], _parameters: dict[str, Any]
) -> PredicateResult:
    stopped = values["scene.collision_stop"]
    if not isinstance(stopped, bool):
        raise ValueError("scene collision-stop observation must be boolean")
    return PredicateResult(
        not stopped,
        "collision_path_clear" if not stopped else "rgbd_collision_stop_requested",
        {"scene.collision_stop": stopped},
    )


def _motion_sensor_predicate_registry() -> SensorPredicateRegistry:
    """Register available local predicate plugins; the lease core stays generic."""
    registry = SensorPredicateRegistry()
    registry.register(
        SensorPredicateSpec(
            predicate_id="scene.no_collision_stop",
            description=(
                "The ROS 2 RGB-D collision monitor has not requested a stop."
            ),
            required_channels=("scene.collision_stop",),
            maximum_age_s=0.5,
            evaluator=_collision_stop_clear_predicate,
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="motion.progress_not_stalled",
            description=(
                "Fresh robot kinematic feedback continues to reduce target error."
            ),
            required_channels=("motion.stalled_observation_count",),
            maximum_age_s=0.5,
            evaluator=_motion_progress_predicate,
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="gripper.contact_retained",
            description="Touch and minimum contact force remain observed.",
            required_channels=("gripper.touch", "gripper.contact_force_n"),
            maximum_age_s=0.5,
            evaluator=_contact_retained_predicate,
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="object.translation_within_error",
            description="Tracked object translation remains inside its lease.",
            required_channels=("object.tracked_translation_error_m",),
            maximum_age_s=0.5,
            evaluator=lambda values, parameters: _numeric_bound_predicate(
                values,
                parameters,
                channel="object.tracked_translation_error_m",
                bound_name="maximum_error_m",
                valid_reason="tracked_translation_within_tolerance",
                invalid_reason="tracked_pose_error_exceeded",
                maximum=True,
            ),
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="object.orientation_within_error",
            description=(
                "RGB-D tracked object major-axis orientation remains inside "
                "its model-issued tolerance."
            ),
            required_channels=("rgbd.object_orientation_error_deg",),
            maximum_age_s=0.5,
            evaluator=lambda values, parameters: _numeric_bound_predicate(
                values,
                parameters,
                channel="rgbd.object_orientation_error_deg",
                bound_name="maximum_error_deg",
                valid_reason="tracked_orientation_within_tolerance",
                invalid_reason="rgbd_object_orientation_error_exceeded",
                maximum=True,
            ),
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="scene.clearance_above_minimum",
            description="Observed object-to-support clearance remains sufficient.",
            required_channels=("scene.observed_clearance_m",),
            maximum_age_s=0.5,
            evaluator=lambda values, parameters: _numeric_bound_predicate(
                values,
                parameters,
                channel="scene.observed_clearance_m",
                bound_name="minimum_clearance_m",
                valid_reason="observed_clearance_sufficient",
                invalid_reason="observed_clearance_below_lease_minimum",
                maximum=False,
            ),
        )
    )
    return registry


def _motion_sensor_predicate_leases(
    config: dict[str, Any],
) -> tuple[SensorPredicateLease, ...]:
    leases: list[SensorPredicateLease] = []
    leases.append(
        SensorPredicateLease(
            "motion.progress_not_stalled",
            {
                "maximum_stalled_observations": int(
                    config.get("maximum_stalled_observations", 3)
                )
            },
        )
    )
    if bool(config.get("require_contact", False)):
        leases.append(
            SensorPredicateLease(
                "gripper.contact_retained",
                {"minimum_force_n": float(config.get("minimum_contact_force_n", 0.0))},
            )
        )
    if config.get("maximum_tracked_pose_error_m") is not None:
        leases.append(
            SensorPredicateLease(
                "object.translation_within_error",
                {"maximum_error_m": float(config["maximum_tracked_pose_error_m"])},
            )
        )
    if config.get("maximum_tracked_orientation_error_deg") is not None:
        leases.append(
            SensorPredicateLease(
                "object.orientation_within_error",
                {
                    "maximum_error_deg": float(
                        config["maximum_tracked_orientation_error_deg"]
                    ),
                    "tracked_object_id": config.get("tracked_object_id"),
                },
            )
        )
    if config.get("minimum_observed_clearance_m") is not None:
        leases.append(
            SensorPredicateLease(
                "scene.clearance_above_minimum",
                {
                    "minimum_clearance_m": float(
                        config["minimum_observed_clearance_m"]
                    )
                },
            )
        )
    return tuple(leases)


def _motion_governor_prompt(
    *,
    instruction: str,
    observation_id: str,
    state: dict[str, Any],
    motion_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> str:
    """Build a tool-only motion decision request without task-specific rules."""
    return f"""Govern the next bounded world-space movement using the attached
fresh observation and exactly one advertised tool.

Human instruction:
{instruction}

Fresh observation token: {observation_id}
Observed world state:
{json.dumps(state, indent=2)}
Current motion context:
{json.dumps(motion_context, indent=2)}
RGB-D summary:
{json.dumps(rgbd_summary, indent=2)}

The image contains RGB on the left and depth on the right when depth is
available. Select a registered executor tool to continue or correct the current
world-space pose target. A correction may change XYZ and/or orientation;
rotation_delta_axis_angle_deg is a world-frame axis-angle vector whose direction
is the rotation axis and whose magnitude is degrees. Select hold_motion when a
new observation is required before movement, or abort_motion when movement is
unsafe. Executor settings are
optional and must stay inside their advertised schema. Ground any target
correction and configuration change in current evidence. Measured contact and
touch in observed world state are current physical evidence; interpret them
together with the requested actuator state instead of requiring an unloaded
actuator to reach its full travel. When current_contact.contact_bodies is
available for a multi-contact clamp, compare the named per-body force
directions: opposing contact can support retention, while one active body or
predominantly same-direction pressure can indicate surface contact. Aggregate
touch alone does not prove a secure grasp. Use the reported
pairwise_force_direction_cosine and force_magnitude_ratio_min_over_max instead
of estimating them from raw vectors; positive cosine is same-direction
pressure. Configure a sufficiently long
maximum_iterations horizon for the target. When the observed state indicates
that a new interaction pose is needed, compare actuator_contact_geometry's
closing_axis_robot_root and contact-body span with the visible object axes and
extents in rgbd_scene_geometry. Change orientation as well as translation when
the current closing direction cannot produce opposing contact. Keep the swept
actuator geometry outside visible non-task scene surfaces; visible RGB-D geometry
is sensor evidence, not an object-specific grasp prescription. When the observed
state indicates
that pregrasp_axis_alignment is available but aligned=false, use one of its
candidate_yaw_correction_deg values as the evidence for a world-Z axis-angle
rotation correction. The local scheduler will continue withholding engagement
until a fresh RGB-D observation measures the jaw axis within the configured
tolerance; a translation-only correction cannot satisfy that angular gate.
When the observed state indicates
that an object is being carried, advertise only the invariants needed for this
motion through require_contact, minimum_contact_force_n,
maximum_tracked_pose_error_m, and minimum_observed_clearance_m. These are lease
conditions evaluated locally while the model is not being polled. When an
RGB-D tracked-orientation source is available for a carried object, also set a
maximum_tracked_orientation_error_deg appropriate to the observed geometry so
object rotation or slip interrupts the lease, and identify that object with
tracked_object_id from the runtime-advertised enum. The
lease_condition_sources map in motion context is authoritative: never include a
condition whose corresponding source is null or unavailable. Do not emit prose
or JSON outside the single native tool call.

An invalidated lease is a stopped recovery checkpoint, not automatically a
terminal task failure. Inspect the fresh evidence and choose the next safe
solution. If contact, actuator feedback, and visual tracking show that a carried
object remains stably grasped while the current route or clearance is invalid,
correct the target to restore safe clearance and continue. If the grasp is
unstable, detached, or cannot be established from current evidence, hold for a
fresh operation decision instead of blindly continuing. When
previous_motion_tool_outcome is present, correct that rejected proposal while
preserving the local safety bounds.

When a grasp attempt produced contact but the object did not follow the lift,
do not assume that contact proved a secure grasp. Compare the fresh RGB-D image,
contact-force direction, physical actuator position, and failed-attempt evidence.
Use a bounded translation and/or rotation to choose a materially different
grasp pose before requesting another clamp engagement when the previous geometry
was ineffective. This rule is geometry- and outcome-based; it does not encode an
object-specific grasp.

When previous_recovery_motion_outcome is present, its attempted target and
measured residual are physical execution evidence. If its lease invalidation
contains motion_progress_stalled, do not repeat an effectively identical target:
change the translation and/or orientation enough to create a materially distinct
approach supported by the fresh RGB-D/state evidence. A target below or through
an observed support surface is not a valid recovery. Abort when no bounded safe
alternative exists.

When scheduler_decision explicitly dispatches continue.runtime_motion after a
previous hold, the fresh operation decision has already resolved whether to
wait for another operation. Do not repeat hold_motion solely because the same
state is still visible. Use the current evidence to select a bounded movement
that can safely change that state and advance the instruction, or select
abort_motion when no such movement is safe. The recovery movement itself is
your decision; it is not supplied by a phase-specific controller.

{critic_context}"""


def _motion_registry_for_observation_sources(
    registry: MotionExecutorRegistry,
    source_context: dict[str, Any],
) -> MotionExecutorRegistry:
    """Advertise only lease settings backed by this observation's sensors."""
    unavailable_fields: set[str] = set()
    if source_context.get("contact") is None:
        unavailable_fields.update(("require_contact", "minimum_contact_force_n"))
    if source_context.get("tracked_pose") is None:
        unavailable_fields.add("maximum_tracked_pose_error_m")
    orientation_sources = source_context.get("tracked_orientation")
    if not isinstance(orientation_sources, dict) or not orientation_sources:
        unavailable_fields.update(
            ("maximum_tracked_orientation_error_deg", "tracked_object_id")
        )
    if source_context.get("observed_clearance") is None:
        unavailable_fields.add("minimum_observed_clearance_m")
    filtered = MotionExecutorRegistry()
    for spec in registry.specs():
        schema = json.loads(json.dumps(spec.configuration_schema))
        properties = schema.get("properties", {})
        for field in unavailable_fields:
            properties.pop(field, None)
        filtered.register(
            MotionExecutorSpec(
                executor_id=spec.executor_id,
                tool_name=spec.tool_name,
                description=spec.description,
                configuration_schema=schema,
                capability_tags=spec.capability_tags,
                invocation_schema=spec.invocation_schema,
            )
        )
    return filtered


def _choose_observation_bound_motion_tool(
    provider: GeminiProvider,
    registry: MotionExecutorRegistry,
    *,
    instruction: str,
    observation_prefix: str,
    frame: np.ndarray,
    state: dict[str, Any],
    current_target: torch.Tensor,
    motion_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> tuple[dict[str, Any], float, str]:
    """Ask the selected model to invoke one fresh-observation motion tool."""
    encoded, digest = _encode_frame(frame)
    observation_id = f"{observation_prefix}:{digest}"
    source_context = motion_context.get("lease_condition_sources", {})
    if not isinstance(source_context, dict):
        source_context = {}
    observation_registry = _motion_registry_for_observation_sources(
        registry,
        source_context,
    )
    current_target_quaternion = motion_context.get(
        "current_target_quaternion_wxyz",
        state.get("eef_gripper_base_quaternion_wxyz"),
    )
    gate = ObservationBoundMotionGate(
        observation_id=observation_id,
        current_target_m=current_target.tolist(),
        current_target_quaternion_wxyz=current_target_quaternion,
        maximum_correction_m=args_cli.maximum_model_target_correction,
        maximum_rotation_correction_deg=(
            args_cli.maximum_model_rotation_correction_deg
        ),
        registry=observation_registry,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual motion governor. Every movement decision must "
                "be expressed as exactly one of the runtime-advertised tools."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": _motion_governor_prompt(
                        instruction=instruction,
                        observation_id=observation_id,
                        state=state,
                        motion_context=motion_context,
                        rgbd_summary=rgbd_summary,
                        critic_context=critic_context,
                    ),
                },
            ],
        },
    ]
    started = time.perf_counter()
    response = asyncio.run(
        asyncio.wait_for(
            provider.complete(
                messages,
                {
                    "tools": motion_tool_schemas(
                        observation_id, observation_registry
                    ),
                    "tool_choice": "required",
                },
            ),
            timeout=args_cli.timeout,
        )
    )
    latency = time.perf_counter() - started
    tool_calls = response.tool_calls or []
    if len(tool_calls) != 1:
        error = (
            "model must issue exactly one motion tool call; "
            f"received {len(tool_calls)}"
        )
        return (
            {
                "decision": "retry",
                "grasp_ready": False,
                "confidence": 0.0,
                "assessment": f"Motion tool rejected by safety gate: {error}",
                "motion_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": None,
                    "arguments": None,
                    "error": error,
                    "model_text": str(response.text or "")[:500],
                },
                "target_xyz_m": current_target.tolist(),
                "target_quaternion_wxyz": list(current_target_quaternion),
                "executor_id": None,
                "executor_config": {},
            },
            latency,
            digest,
        )
    try:
        outcome = gate.dispatch(tool_calls[0])
    except MotionToolValidationError as error:
        function = tool_calls[0].get("function", {})
        return (
            {
                "decision": "retry",
                "grasp_ready": False,
                "confidence": 0.0,
                "assessment": f"Motion tool rejected by safety gate: {error}",
                "motion_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": function.get("name"),
                    "arguments": function.get("arguments"),
                    "error": str(error),
                },
                "target_xyz_m": current_target.tolist(),
                "target_quaternion_wxyz": list(current_target_quaternion),
                "executor_id": None,
                "executor_config": {},
            },
            latency,
            digest,
        )
    if outcome.action == "execute":
        lease_conditions = _motion_lease_conditions_from_config(
            dict(outcome.executor_config)
        )
        source_errors = motion_lease_source_errors(
            lease_conditions,
            contact_available=source_context.get("contact") is not None,
            tracked_pose_available=source_context.get("tracked_pose") is not None,
            observed_clearance_available=(
                source_context.get("observed_clearance") is not None
            ),
        )
        if (
            outcome.executor_config.get(
                "maximum_tracked_orientation_error_deg"
            )
            is not None
        ):
            tracked_object_id = outcome.executor_config.get("tracked_object_id")
            orientation_sources = source_context.get("tracked_orientation")
            if not isinstance(tracked_object_id, str) or not tracked_object_id:
                source_errors = (
                    *source_errors,
                    "tracked_object_id is required for an RGB-D orientation lease",
                )
            elif not isinstance(orientation_sources, dict) or (
                orientation_sources.get(tracked_object_id) is None
            ):
                source_errors = (
                    *source_errors,
                    "RGB-D tracked-orientation source is unavailable for "
                    f"{tracked_object_id!r}",
                )
        if source_errors:
            error = "lease references unavailable observations: " + "; ".join(
                source_errors
            )
            rejected_tool = outcome.to_dict()
            rejected_tool.update({"status": "rejected", "error": error})
            return (
                {
                    "decision": "retry",
                    "grasp_ready": False,
                    "confidence": 0.0,
                    "assessment": f"Motion tool rejected by safety gate: {error}",
                    "motion_tool": rejected_tool,
                    "target_xyz_m": current_target.tolist(),
                    "target_quaternion_wxyz": list(current_target_quaternion),
                    "executor_id": None,
                    "executor_config": {},
                },
                latency,
                digest,
            )
    decision = {
        "decision": (
            "execute"
            if outcome.action == "execute"
            else "retry" if outcome.action == "hold" else "abort"
        ),
        "grasp_ready": outcome.action == "execute",
        "confidence": outcome.confidence,
        "assessment": outcome.reason,
        "motion_tool": outcome.to_dict(),
        "target_xyz_m": list(outcome.target_after_m),
        "target_quaternion_wxyz": list(
            outcome.target_after_quaternion_wxyz
        ),
        "executor_id": outcome.executor_id,
        "executor_config": dict(outcome.executor_config),
    }
    return decision, latency, digest


def _actuator_governor_prompt(
    *,
    instruction: str,
    observation_id: str,
    state: dict[str, Any],
    actuator_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> str:
    """Build a task-neutral actuator decision request."""
    return f"""Govern the next bounded actuator transition using the attached
fresh observation and exactly one advertised tool.

Human instruction:
{instruction}

Fresh observation token: {observation_id}
Observed world state:
{json.dumps(state, indent=2)}
Current actuator context:
{json.dumps(actuator_context, indent=2)}
RGB-D summary:
{json.dumps(rgbd_summary, indent=2)}

The image contains RGB on the left and depth on the right when depth is
available. Choose a registered actuator executor only when its command advances
the human instruction and is safe in the fresh observed state. Select
hold_actuation when another observation is required before changing the
actuator. abort_actuation is a terminal task abort, not a request to stop this
transition and replan. If the current actuator operation should stop but the
human instruction remains recoverable, select hold_actuation so the fresh
operation scheduler can choose another capability. Select abort_actuation only
when no safe recovery can continue the overall instruction. Measured touch and
force are physical evidence: incomplete travel with touch can indicate an
object is obstructing closure, while disengagement should remove retained
contact. If actuator context contains failed_grasp_attempts and the fresh pose
still matches an ineffective contact pose, do not engage the clamp again; hold
that repeated engagement so the operation scheduler can select a pose
correction. This restriction applies only to engagement. Never use failed-grasp
history to block disengaging a closed, empty gripper: disengagement is the
recoverable reset that permits a new pose and grasp attempt. The actuator
context may include failed_grasp_pose_comparisons with measured object-relative
translation_delta_m and orientation_delta_deg. Use those explicit deltas when
deciding whether a pose changed; do not describe a pose as unchanged without
reconciling both measurements in the tool-call reason. The governor, not this
runtime, decides whether the measured change is sufficient. The scheduler
dispatch supplies the fresh reason this actuator capability was selected; use
it as context while independently validating the transition against the fresh
observation. If trigger_event.actuator_outcome_invalidated is true, the previous
requested actuator state produced neither measured actuator displacement nor
sensor-supported loaded contact. Do not issue that same requested state again.
If its commanded state is engaged, disengage it to permit a pose correction;
otherwise hold so the operation scheduler can select motion. This is a measured
outcome rule, not an object-specific grasp prescription. When fresh
contact_bodies are available for a multi-contact clamp,
use the named per-body forces to distinguish an opposing multi-body pinch from
same-direction surface pressure; aggregate force or touch alone does not prove
object retention. Use pairwise_force_direction_cosine and
force_magnitude_ratio_min_over_max when present; positive cosine is
same-direction pressure rather than an opposing pinch. Executor settings are
optional and must remain inside their
advertised schema. Do not emit prose or JSON outside the single native tool
call.

{critic_context}"""


def _choose_observation_bound_actuator_tool(
    provider: GeminiProvider,
    registry: ActuatorExecutorRegistry,
    *,
    instruction: str,
    observation_prefix: str,
    frame: np.ndarray,
    state: dict[str, Any],
    actuator_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> tuple[dict[str, Any], float, str]:
    """Ask the selected model for one fresh-observation actuator call."""
    encoded, digest = _encode_frame(frame)
    observation_id = f"{observation_prefix}:{digest}"
    gate = ObservationBoundActuatorGate(
        observation_id=observation_id,
        registry=registry,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual actuator governor. Every actuator decision "
                "must be exactly one runtime-advertised native tool call."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": _actuator_governor_prompt(
                        instruction=instruction,
                        observation_id=observation_id,
                        state=state,
                        actuator_context=actuator_context,
                        rgbd_summary=rgbd_summary,
                        critic_context=critic_context,
                    ),
                },
            ],
        },
    ]
    started = time.perf_counter()
    response = asyncio.run(
        asyncio.wait_for(
            provider.complete(
                messages,
                {
                    "tools": actuator_tool_schemas(observation_id, registry),
                    "tool_choice": "required",
                },
            ),
            timeout=args_cli.timeout,
        )
    )
    latency = time.perf_counter() - started
    tool_calls = response.tool_calls or []
    if len(tool_calls) != 1:
        error = (
            "model must issue exactly one actuator tool call; "
            f"received {len(tool_calls)}"
        )
        return (
            {
                "decision": "retry",
                "confidence": 0.0,
                "assessment": f"Actuator tool rejected by safety gate: {error}",
                "actuator_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": None,
                    "arguments": None,
                    "error": error,
                    "model_text": str(response.text or "")[:500],
                },
                "executor_id": None,
                "command": {},
                "executor_config": {},
            },
            latency,
            digest,
        )
    try:
        outcome = gate.dispatch(tool_calls[0])
    except MotionToolValidationError as error:
        function = tool_calls[0].get("function", {})
        return (
            {
                "decision": "retry",
                "confidence": 0.0,
                "assessment": f"Actuator tool rejected by safety gate: {error}",
                "actuator_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": function.get("name"),
                    "arguments": function.get("arguments"),
                    "error": str(error),
                },
                "executor_id": None,
                "command": {},
                "executor_config": {},
            },
            latency,
            digest,
        )
    return (
        {
            "decision": (
                "execute"
                if outcome.action == "execute"
                else "retry" if outcome.action == "hold" else "abort"
            ),
            "confidence": outcome.confidence,
            "assessment": outcome.reason,
            "actuator_tool": outcome.to_dict(),
            "executor_id": outcome.executor_id,
            "command": dict(outcome.command),
            "executor_config": dict(outcome.executor_config),
        },
        latency,
        digest,
    )


def _post_motion_operation_candidates(
    *, actuator_transition_available: bool = True
) -> tuple[OperationCandidate, ...]:
    """Advertise only operations admitted by current runtime preconditions."""
    candidates = [
        OperationCandidate(
            operation_id="continue.runtime_motion",
            kind="motion",
            description=(
                "Preserve all current actuator commands and continue to the "
                "next runtime-proposed motion operation."
            ),
        ),
    ]
    if actuator_transition_available:
        candidates.append(OperationCandidate(
            operation_id="evaluate.runtime_actuator",
            kind="actuation",
            description=(
                "Request a fresh model-governed actuator command before any "
                "later runtime-proposed motion."
            ),
        ))
    return tuple(candidates)


def _operation_scheduler_prompt(
    *,
    instruction: str,
    observation_id: str,
    state: dict[str, Any],
    operation_context: dict[str, Any],
    candidates: tuple[OperationCandidate, ...],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> str:
    """Build a task-neutral next-operation request from runtime candidates."""
    return f"""Select the next operation using the attached fresh observation
and exactly one advertised scheduler tool.

Human instruction:
{instruction}

Fresh observation token: {observation_id}
Observed world state:
{json.dumps(state, indent=2)}
Operation context:
{json.dumps(operation_context, indent=2)}
Runtime-advertised candidates:
{json.dumps([candidate.to_dict() for candidate in candidates], indent=2)}
RGB-D summary:
{json.dumps(rgbd_summary, indent=2)}

A bounded runtime operation has just completed or yielded on measured evidence.
Dispatch evaluate.runtime_actuator when changing or confirming an actuator
command is the next physical operation needed to advance the human instruction.
Dispatch continue.runtime_motion only when current actuator commands should be
preserved while the runtime proposes the next movement. Use observe_again when
the evidence is insufficient, complete_task only when the physical instruction
is already achieved, or abort_task when neither advertised operation is safe.
When trigger_event contains failed_grasp_attempts, compare the current pose and
contact evidence with those attempts. If the actuator is still engaged after a
measured outcome invalidated that contact as an effective grasp, dispatch the
actuator capability to disengage it before pose correction. Once disengaged, do
not dispatch another clamp engagement from an unchanged ineffective grasp pose;
dispatch motion so the model can choose a different evidence-grounded position
and/or orientation. A later clamp engagement is appropriate only after the fresh
state shows that the grasp pose has materially changed or the prior failure
evidence has otherwise been resolved.
When failed_grasp_pose_comparisons are present, use their measured
object-relative translation_delta_m and orientation_delta_deg rather than
guessing pose similarity from raw coordinates. Reconcile both values in the
scheduler-tool reason; the selected governor decides whether the change is
sufficient for another physical attempt.
When current actuator state is disengaged and pregrasp_axis_alignment_ready is
false, actuation has intentionally not been advertised. Dispatch motion so the
motion governor can rotate the wrist using the fresh pregrasp_axis_alignment
axis comparisons. Do not describe another descent or translation-only move as
fixing a measured angular error.
When trigger_event says runtime_transition_not_admitted, the legacy runtime
label is only telemetry and cannot advance itself. Select the physical operation
that establishes the named required_capability from fresh evidence. For
supported_loaded_interaction, establish an engaged actuator with supported
loaded contact and observed interaction geometry before any carrying motion.
For released_interaction, dispatch actuator evaluation when the task relation
adapter reports goal_relation.satisfied, and do not choose retreat motion until
disengagement is physically observed.
When trigger_event says actuator_outcome_invalidated, the previous actuator
command produced neither measured actuator motion nor sensor-supported loaded
contact. Do not treat the commanded state as a physical grasp and do not repeat
the ineffective command; select the actuator capability for a different command
or motion so the model can correct the pose.
Do not infer a transition from a prerecorded action or phase schedule. Do not
emit prose or JSON outside the single native tool call.

{critic_context}"""


def _choose_observation_bound_operation(
    provider: GeminiProvider,
    *,
    instruction: str,
    observation_prefix: str,
    frame: np.ndarray,
    state: dict[str, Any],
    operation_context: dict[str, Any],
    candidates: tuple[OperationCandidate, ...],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> tuple[dict[str, Any], float, str]:
    """Ask the selected model to route one fresh-observation operation."""
    encoded, digest = _encode_frame(frame)
    observation_id = f"{observation_prefix}:{digest}"
    gate = ObservationBoundOperationGate(
        observation_id=observation_id,
        candidates=candidates,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual operation scheduler. Every next-operation "
                "decision must be exactly one runtime-advertised native tool call."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": _operation_scheduler_prompt(
                        instruction=instruction,
                        observation_id=observation_id,
                        state=state,
                        operation_context=operation_context,
                        candidates=candidates,
                        rgbd_summary=rgbd_summary,
                        critic_context=critic_context,
                    ),
                },
            ],
        },
    ]
    started = time.perf_counter()
    response = asyncio.run(
        asyncio.wait_for(
            provider.complete(
                messages,
                {
                    "tools": operation_scheduler_tool_schemas(
                        observation_id, candidates
                    ),
                    "tool_choice": "required",
                },
            ),
            timeout=args_cli.timeout,
        )
    )
    latency = time.perf_counter() - started
    tool_calls = response.tool_calls or []
    if len(tool_calls) != 1:
        error = (
            "model must issue exactly one scheduler tool call; "
            f"received {len(tool_calls)}"
        )
        return (
            {
                "decision": "observe",
                "confidence": 0.0,
                "assessment": f"Scheduler tool rejected by safety gate: {error}",
                "scheduler_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": None,
                    "arguments": None,
                    "error": error,
                    "model_text": str(response.text or "")[:500],
                },
                "operation_id": None,
                "operation_kind": None,
            },
            latency,
            digest,
        )
    try:
        outcome = gate.dispatch(tool_calls[0])
    except MotionToolValidationError as error:
        function = tool_calls[0].get("function", {})
        return (
            {
                "decision": "observe",
                "confidence": 0.0,
                "assessment": f"Scheduler tool rejected by safety gate: {error}",
                "scheduler_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": function.get("name"),
                    "arguments": function.get("arguments"),
                    "error": str(error),
                },
                "operation_id": None,
                "operation_kind": None,
            },
            latency,
            digest,
        )
    return (
        {
            "decision": outcome.action,
            "confidence": outcome.confidence,
            "assessment": outcome.reason,
            "scheduler_tool": outcome.to_dict(),
            "operation_id": outcome.operation_id,
            "operation_kind": outcome.operation_kind,
        },
        latency,
        digest,
    )


def _test_line(index: int, name: str, passed: bool, detail: str) -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[TEST {index}/{TOTAL_TESTS}] {status} | {name} | {detail}", flush=True)


def _runtime_geometry_by_id(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    scene_geometry = state.get("rgbd_scene_geometry", {})
    geometries = (
        scene_geometry.get("geometries", [])
        if isinstance(scene_geometry, Mapping)
        else []
    )
    if not isinstance(geometries, list):
        return {}
    return {
        str(item["runtime_id"]): item
        for item in geometries
        if isinstance(item, Mapping)
        and isinstance(item.get("runtime_id"), str)
        and item.get("runtime_id")
    }


def _point_aabb_clearance_m(
    point: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
) -> float:
    point_np = np.asarray(point, dtype=np.float64)
    lower_np = np.asarray(lower, dtype=np.float64)
    upper_np = np.asarray(upper, dtype=np.float64)
    if point_np.shape != (3,) or lower_np.shape != (3,) or upper_np.shape != (3,):
        return float("inf")
    if not (
        np.isfinite(point_np).all()
        and np.isfinite(lower_np).all()
        and np.isfinite(upper_np).all()
    ):
        return float("inf")
    outside = np.maximum(np.maximum(lower_np - point_np, point_np - upper_np), 0.0)
    return float(np.linalg.norm(outside))


def _interaction_path_clearance_m(
    current_interaction_position_m: Sequence[float],
    target_interaction_position_m: Sequence[float],
    geometries: Mapping[str, Mapping[str, Any]],
) -> tuple[float, str | None]:
    current = np.asarray(current_interaction_position_m, dtype=np.float64)
    target = np.asarray(target_interaction_position_m, dtype=np.float64)
    if current.shape != (3,) or target.shape != (3,):
        return float("inf"), None
    minimum = float("inf")
    nearest_id: str | None = None
    for alpha in np.linspace(0.0, 1.0, 17):
        sample = (1.0 - alpha) * current + alpha * target
        for entity_id, geometry in geometries.items():
            lower = geometry.get("visible_aabb_min_base_m")
            upper = geometry.get("visible_aabb_max_base_m")
            if not isinstance(lower, (list, tuple)) or not isinstance(
                upper, (list, tuple)
            ):
                continue
            clearance = _point_aabb_clearance_m(sample, lower, upper)
            if clearance < minimum:
                minimum = clearance
                nearest_id = entity_id
    return minimum, nearest_id


def _axis_set_error_deg(
    reference_axis: Sequence[float],
    current_axes: Any,
) -> float | None:
    reference = np.asarray(reference_axis, dtype=np.float64)
    if reference.shape != (3,) or not np.isfinite(reference).all():
        return None
    reference_norm = float(np.linalg.norm(reference))
    if reference_norm <= 1.0e-9 or not isinstance(current_axes, (list, tuple)):
        return None
    reference /= reference_norm
    errors: list[float] = []
    for raw_axis in current_axes:
        axis = np.asarray(raw_axis, dtype=np.float64)
        if axis.shape != (3,) or not np.isfinite(axis).all():
            continue
        norm = float(np.linalg.norm(axis))
        if norm <= 1.0e-9:
            continue
        cosine = abs(float(np.dot(reference, axis / norm)))
        errors.append(float(np.degrees(np.arccos(np.clip(cosine, 0.0, 1.0)))))
    return min(errors) if errors else None


def _interaction_target_entity_id(
    invocation_candidate: Any,
    invocation_decision: Any,
) -> str | None:
    """Resolve the fresh RGB-D anchor entity intentionally being contacted."""
    selected_anchor_id = invocation_decision.position_anchor_id
    if selected_anchor_id is None:
        return None
    selected_anchor = next(
        (
            item
            for item in invocation_candidate.position_anchors
            if item.anchor_id == selected_anchor_id
        ),
        None,
    )
    if selected_anchor is None:
        raise RuntimeError(
            "issued invocation position anchor is absent from its candidate"
        )
    return selected_anchor.entity_id


def _guarded_dispatch_invalidation_events(
    *,
    runtime_lease: Any,
    lease_candidate: Any,
    invocation_candidate: Any,
    invocation_decision: Any,
    baseline_membership_ids: set[str],
    current_provider_instance_id: str,
    state: Mapping[str, Any],
    baseline_tracked_positions_m: Mapping[str, Sequence[float]],
    current_tracked_positions_m: Mapping[str, Sequence[float]],
) -> tuple[DispatchInvalidationEvent, ...]:
    """Evaluate the issued condition set from fresh simulator/RGB-D evidence."""
    current_geometry = _runtime_geometry_by_id(state)
    baseline_geometry = {
        item.entity_id: item.geometry for item in lease_candidate.geometry_bindings
    }
    current_inventory = semantic_scene_inventory_from_state(state)
    current_membership_ids = {
        str(item["entity_id"])
        for item in current_inventory.get("entities", [])
        if isinstance(item, Mapping) and isinstance(item.get("entity_id"), str)
    }
    config = runtime_lease.lease.tool_configuration
    invalidations: list[DispatchInvalidationEvent] = []

    def emit(binding: Any, evidence: Mapping[str, Any], reason: str) -> None:
        invalidations.append(
            DispatchInvalidationEvent(
                condition_id=binding.condition_id,
                evidence_source_id=binding.evidence_source_id,
                evidence=evidence,
                reason=reason,
            )
        )

    target_ids = set(lease_candidate.operation_target_entity_ids)
    for binding in runtime_lease.lease.invalidation_bindings:
        condition_id = binding.condition_id
        targets = set(binding.target_entity_ids) or target_ids
        if condition_id == "lease.membership_changed":
            if current_membership_ids != baseline_membership_ids:
                emit(
                    binding,
                    {
                        "expected_entity_ids": sorted(baseline_membership_ids),
                        "observed_entity_ids": sorted(current_membership_ids),
                    },
                    "task membership changed",
                )
        elif condition_id == "provider.instance_changed":
            if current_provider_instance_id != runtime_lease.lease.provider_instance_id:
                emit(
                    binding,
                    {
                        "expected_provider_instance_id": (
                            runtime_lease.lease.provider_instance_id
                        ),
                        "observed_provider_instance_id": current_provider_instance_id,
                    },
                    "world-effect provider instance changed",
                )
        elif condition_id == "scene.target_visibility_lost":
            missing = sorted(targets - set(current_geometry))
            if missing:
                emit(binding, {"missing_entity_ids": missing}, "RGB-D target lost")
        elif condition_id == "scene.target_geometry_drift":
            center_limit = float(binding.parameters["maximum_center_shift_m"])
            extent_limit = float(
                binding.parameters["maximum_extent_change_fraction"]
            )
            for entity_id in sorted(targets):
                baseline = baseline_geometry.get(entity_id)
                current = current_geometry.get(entity_id)
                if not isinstance(baseline, Mapping) or not isinstance(
                    current, Mapping
                ):
                    continue
                assessment = assess_fused_target_geometry(
                    baseline_geometry=baseline,
                    current_geometry=current,
                    maximum_center_shift_m=center_limit,
                    maximum_extent_change_fraction=extent_limit,
                    baseline_tracked_position_m=(
                        baseline_tracked_positions_m.get(entity_id)
                    ),
                    current_tracked_position_m=(
                        current_tracked_positions_m.get(entity_id)
                    ),
                )
                if assessment["invalidated"]:
                    emit(
                        binding,
                        {"entity_id": entity_id, **assessment},
                        "fused tracked-pose/RGB-D geometry exceeded the issued lease",
                    )
                    break
        elif condition_id == "scene.tracked_pose_error_exceeded":
            center_limit = float(config["maximum_tracked_pose_error_m"])
            for entity_id in sorted(targets):
                baseline_position = baseline_tracked_positions_m.get(entity_id)
                current_position = current_tracked_positions_m.get(entity_id)
                if baseline_position is None or current_position is None:
                    emit(
                        binding,
                        {
                            "entity_id": entity_id,
                            "tracked_pose_available": False,
                            "maximum_tracked_pose_error_m": center_limit,
                        },
                        "required runtime tracked pose is unavailable",
                    )
                    break
                tracked_error = float(
                    np.linalg.norm(
                        np.asarray(current_position, dtype=np.float64)
                        - np.asarray(baseline_position, dtype=np.float64)
                    )
                )
                if tracked_error > center_limit:
                    emit(
                        binding,
                        {
                            "entity_id": entity_id,
                            "tracked_pose_error_m": tracked_error,
                            "maximum_tracked_pose_error_m": center_limit,
                            "tracked_pose_source": "runtime_entity_pose_tracker",
                        },
                        "tracked entity pose exceeded the issued lease",
                    )
                    break
        elif condition_id == "scene.tracked_orientation_error_exceeded":
            selected_axis = next(
                (
                    item
                    for item in invocation_candidate.orientation_axes
                    if item.alignment_id
                    == invocation_decision.orientation_alignment_id
                ),
                None,
            )
            if selected_axis is not None:
                current = current_geometry.get(selected_axis.entity_id)
                error = (
                    _axis_set_error_deg(
                        selected_axis.axis_robot_root,
                        current.get("oriented_footprint_axes_base"),
                    )
                    if isinstance(current, Mapping)
                    else None
                )
                limit = float(
                    config.get("maximum_tracked_orientation_error_deg", math.inf)
                )
                if error is None or error > limit:
                    emit(
                        binding,
                        {
                            "entity_id": selected_axis.entity_id,
                            "orientation_error_deg": error,
                            "maximum_orientation_error_deg": limit,
                        },
                        "tracked RGB-D orientation exceeded the issued lease",
                    )
        elif condition_id == "scene.observed_clearance_below_minimum":
            interaction = state.get("actuator_contact_geometry", {})
            current_position = (
                interaction.get("contact_center_xyz_m")
                if isinstance(interaction, Mapping)
                else None
            )
            target_position = invocation_decision.grounding_assessment.get(
                "realized_interaction_position_m"
            )
            if isinstance(current_position, (list, tuple)) and isinstance(
                target_position, (list, tuple)
            ):
                obstacle_geometry = interaction_obstacle_geometry(
                    current_geometry,
                    interaction_target_entity_id=(
                        _interaction_target_entity_id(
                            invocation_candidate,
                            invocation_decision,
                        )
                    ),
                )
                clearance, nearest_id = _interaction_path_clearance_m(
                    current_position, target_position, obstacle_geometry
                )
                limit = float(config.get("minimum_observed_clearance_m", 0.0))
                if clearance < limit:
                    emit(
                        binding,
                        {
                            "path_clearance_m": clearance,
                            "minimum_clearance_m": limit,
                            "nearest_entity_id": nearest_id,
                        },
                        "RGB-D interaction path clearance is below the lease minimum",
                    )
        elif condition_id == "contact.required_contact_lost" and config.get(
            "require_contact"
        ):
            contact = state.get("current_contact", {})
            if not isinstance(contact, Mapping) or not contact.get("touch"):
                emit(binding, {"contact": contact}, "required contact was lost")
    return tuple(invalidations)


def _plan_guarded_world_effect_continuation(
    *,
    coach: Any,
    instruction: str,
    frame: np.ndarray,
    graph: WorldGoalGraph,
    membership_lease: SceneMembershipLease,
    inventory: Mapping[str, Any],
    predicate_registry: Any,
    capability_registry: Any,
    capability_advertisement: Mapping[str, Any],
    provider_registry: Any,
    runtime_effect_tools: Sequence[RuntimeToolCapability],
    trackable_object_ids: Sequence[str],
    runtime_state: Mapping[str, Any],
    maximum_duration_s: float,
    operation_index: int,
) -> dict[str, Any]:
    """Plan and issue one fresh continuation lease without dispatching it.

    This is intentionally a full replan. Every model response is constrained by
    a candidate set derived from the current inventory, and the returned lease
    still needs a fresh-evidence, single-use dispatch permit.
    """
    activation_candidates = build_goal_activation_candidates(
        graph,
        membership_lease,
        predicate_registry,
        capability_registry,
        inventory,
    )
    inventory_digest = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:16]
    graph_digest = hashlib.sha256(
        json.dumps(
            graph.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:16]
    activation_observation_id = (
        f"goal-activation-continuation:{operation_index}:"
        f"{inventory_digest}:{graph_digest}"
    )
    activation_payload, activation_latency, activation_image_digest = coach.reason(
        build_world_goal_activation_prompt(
            instruction=instruction,
            observation_id=activation_observation_id,
            graph=graph,
            membership_lease=membership_lease,
            inventory=inventory,
            capability_advertisement=capability_advertisement,
            candidate_set=activation_candidates,
        ),
        frame,
    )
    activation_decision = WorldGoalActivationGate(
        activation_observation_id,
        activation_candidates,
    ).dispatch(activation_payload)
    trace: dict[str, Any] = {
        "operation_index": operation_index,
        "goal_activation": {
            "observation_id": activation_observation_id,
            "candidate_set": activation_candidates.to_dict(),
            "decision": activation_decision.to_dict(),
            "latency_s": activation_latency,
            "image_digest": activation_image_digest,
        },
        "motion_authority": False,
        "execution_authority": False,
        "authority_scope": [],
    }
    if activation_decision.decision != "select_goal":
        return {
            "status": "goal_not_selected",
            "trace": trace,
            "activation_decision": activation_decision,
        }

    provider_assessment = provider_registry.assess(
        activation_decision.capability_id,
        runtime_effect_tools,
    )
    session_candidates = build_world_effect_session_candidates(
        graph,
        membership_lease,
        activation_candidates,
        activation_decision,
        provider_assessment,
    )
    session_payload, session_latency, session_image_digest = coach.reason(
        build_world_effect_session_prompt(
            instruction=instruction,
            graph=graph,
            membership_lease=membership_lease,
            activation_decision=activation_decision,
            candidate_set=session_candidates,
        ),
        frame,
    )
    session_decision = WorldEffectSessionGate(session_candidates).dispatch(
        session_payload
    )
    trace["effect_session"] = {
        "provider_assessment": provider_assessment.to_dict(),
        "candidate_set": session_candidates.to_dict(),
        "decision": session_decision.to_dict(),
        "latency_s": session_latency,
        "image_digest": session_image_digest,
    }
    if session_decision.decision != "select_provider":
        return {
            "status": "provider_not_selected",
            "trace": trace,
            "activation_decision": activation_decision,
        }

    factory_catalog = PlanningToolFactoryCatalog()
    factory_catalog.register(
        PlanningToolFactory(
            factory_tool_id="factory.spatial_pose_target",
            tool_family="motion",
            capability_tags=SPATIAL_MOTION_CAPABILITY_TAGS,
            activator=lambda: _local_dls_executor_registry(
                trackable_object_ids
            ).advertisement(),
        )
    )
    factory_catalog.register(
        PlanningToolFactory(
            factory_tool_id="factory.reversible_entity_attachment",
            tool_family="actuator",
            capability_tags=REVERSIBLE_ATTACHMENT_CAPABILITY_TAGS,
            activator=lambda: _local_binary_actuator_registry().advertisement(),
        )
    )
    provider_instance = build_planning_world_effect_provider_instance(
        session_candidates,
        session_decision,
        runtime_effect_tools,
        factory_catalog,
    )
    operation_candidates = build_world_effect_operation_candidates(
        provider_instance,
        inventory,
    )
    execution_context = {
        "operation_index": operation_index,
        "controlled_frame": {
            "position_m": runtime_state.get("eef_gripper_base_xyz"),
            "quaternion_wxyz": runtime_state.get(
                "eef_gripper_base_quaternion_wxyz"
            ),
        },
        "interaction_frame": runtime_state.get("actuator_contact_geometry"),
        "current_contact": runtime_state.get("current_contact"),
        "gripper_closed_fraction": runtime_state.get(
            "gripper_closed_fraction"
        ),
        "fresh_rgbd_geometry": runtime_state.get("rgbd_scene_geometry"),
    }
    operation_payload, operation_latency, operation_image_digest = coach.reason(
        build_world_effect_operation_prompt(
            instruction=instruction,
            inventory=inventory,
            instance=provider_instance,
            candidate_set=operation_candidates,
            execution_context=execution_context,
        ),
        frame,
    )
    operation_decision = WorldEffectOperationGate(operation_candidates).dispatch(
        operation_payload
    )
    trace["operation_plan"] = {
        "planning_provider_instance": provider_instance.to_dict(),
        "candidate_set": operation_candidates.to_dict(),
        "decision": operation_decision.to_dict(),
        "execution_context": execution_context,
        "latency_s": operation_latency,
        "image_digest": operation_image_digest,
    }
    if operation_decision.decision != "propose_operation":
        return {
            "status": "operation_not_proposed",
            "trace": trace,
            "activation_decision": activation_decision,
        }

    lease_candidates = build_shadow_execution_lease_candidates(
        provider_instance,
        operation_candidates,
        operation_decision,
        inventory,
    )
    lease_payload, lease_latency, lease_image_digest = coach.reason(
        build_shadow_execution_lease_prompt(
            instruction=instruction,
            candidate_set=lease_candidates,
        ),
        frame,
    )
    lease_decision = ShadowExecutionLeaseGate(lease_candidates).dispatch(
        lease_payload
    )
    trace["execution_lease"] = {
        "candidate_set": lease_candidates.to_dict(),
        "decision": lease_decision.to_dict(),
        "latency_s": lease_latency,
        "image_digest": lease_image_digest,
    }
    if lease_decision.decision != "propose_lease":
        return {
            "status": "lease_not_proposed",
            "trace": trace,
            "activation_decision": activation_decision,
        }

    interaction_geometry = runtime_state.get("actuator_contact_geometry", {})
    if not isinstance(interaction_geometry, Mapping):
        raise ValueError("runtime interaction geometry must be an object")
    runtime_observation = {
        "schema_version": RUNTIME_TOOL_OBSERVATION_SCHEMA_VERSION,
        "source": "fresh_continuation_controlled_and_interaction_frames",
        "coordinate_frame": inventory.get("frame", "unknown"),
        "controlled_frame": {
            "position_m": runtime_state["eef_gripper_base_xyz"],
            "quaternion_wxyz": runtime_state[
                "eef_gripper_base_quaternion_wxyz"
            ],
        },
        "interaction_frame": {
            "origin_offset_local_m": interaction_geometry.get(
                "contact_center_local_m"
            ),
            "alignment_axis_local": interaction_geometry.get(
                "closing_axis_local"
            ),
            "alignment_relation": "surface_tangent",
        },
    }
    invocation_candidates = build_shadow_tool_invocation_candidates(
        provider_instance,
        lease_candidates,
        lease_decision,
        runtime_observation,
    )
    invocation_attempts: list[dict[str, Any]] = []
    invocation_decision = None
    invocation_latency = None
    invocation_image_digest = None
    rejection_context: Mapping[str, Any] | None = None
    for invocation_attempt in range(1, 3):
        invocation_payload, invocation_latency, invocation_image_digest = (
            coach.reason(
                build_shadow_tool_invocation_prompt(
                    instruction=instruction,
                    candidate_set=invocation_candidates,
                    rejection_context=rejection_context,
                ),
                frame,
            )
        )
        try:
            invocation_decision = ShadowToolInvocationGate(
                invocation_candidates
            ).dispatch(invocation_payload)
            invocation_attempts.append(
                {
                    "attempt": invocation_attempt,
                    "status": "valid",
                    "decision": invocation_decision.to_dict(),
                    "latency_s": invocation_latency,
                    "image_digest": invocation_image_digest,
                }
            )
            break
        except WorldEffectToolInvocationError as invocation_error:
            rejection_context = {
                "attempt": invocation_attempt,
                "error_type": type(invocation_error).__name__,
                "error": str(invocation_error),
                "requirements": {
                    "same_fresh_candidate_set": True,
                    "do_not_repeat_rejected_arguments": True,
                    "execution_authority": False,
                },
            }
            invocation_attempts.append(
                {
                    "attempt": invocation_attempt,
                    "status": "rejected",
                    "proposal": invocation_payload,
                    "rejection": dict(rejection_context),
                    "latency_s": invocation_latency,
                    "image_digest": invocation_image_digest,
                }
            )
            if invocation_attempt >= 2:
                raise
    if invocation_decision is None:
        raise RuntimeError("continuation invocation produced no decision")
    trace["tool_invocation"] = {
        "runtime_observation": runtime_observation,
        "candidate_set": invocation_candidates.to_dict(),
        "decision": invocation_decision.to_dict(),
        "attempts": invocation_attempts,
        "latency_s": invocation_latency,
        "image_digest": invocation_image_digest,
    }
    if invocation_decision.decision != "propose_invocation":
        return {
            "status": "invocation_not_proposed",
            "trace": trace,
            "activation_decision": activation_decision,
        }

    runtime_lease = issue_world_effect_runtime_lease(
        lease_candidates=lease_candidates,
        lease_decision=lease_decision,
        invocation_candidates=invocation_candidates,
        invocation_decision=invocation_decision,
        maximum_duration_s=maximum_duration_s,
    )
    trace["runtime_lease"] = runtime_lease.to_dict()
    return {
        "status": "runtime_lease_issued",
        "trace": trace,
        "runtime_lease": runtime_lease,
        "lease_candidates": lease_candidates,
        "lease_decision": lease_decision,
        "invocation_candidates": invocation_candidates,
        "invocation_decision": invocation_decision,
        "planning_provider": provider_instance,
        "activation_decision": activation_decision,
    }


def _dispatch_guarded_world_effect_continuation(
    *,
    env: Any,
    obs: dict[str, Any],
    initial_object_z: float,
    bundle: Mapping[str, Any],
    baseline_inventory: Mapping[str, Any],
    baseline_tracked_positions_m: Mapping[str, Sequence[float]],
    motion_registry: MotionExecutorRegistry,
    actuator_registry: ActuatorExecutorRegistry,
    maximum_evidence_age_s: float,
    maximum_permit_lifetime_s: float,
    artifact_dir: Path,
    operation_index: int,
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    """Dispatch one continuation bundle through a fresh single-use permit."""
    runtime_lease = bundle["runtime_lease"]
    lease_candidates = bundle["lease_candidates"]
    lease_decision = bundle["lease_decision"]
    invocation_candidates = bundle["invocation_candidates"]
    invocation_decision = bundle["invocation_decision"]
    planning_provider = bundle["planning_provider"]
    lease_candidate = next(
        item
        for item in lease_candidates.candidates
        if item.candidate_id == lease_decision.candidate_id
    )
    invocation_candidate = next(
        item
        for item in invocation_candidates.candidates
        if item.candidate_id == invocation_decision.candidate_id
    )
    baseline_membership_ids = {
        str(item["entity_id"])
        for item in baseline_inventory.get("entities", [])
        if isinstance(item, Mapping) and isinstance(item.get("entity_id"), str)
    }
    target_ids = lease_candidate.operation_target_entity_ids
    tracked_baseline = {
        entity_id: baseline_tracked_positions_m[entity_id]
        for entity_id in target_ids
        if entity_id in baseline_tracked_positions_m
    }
    fresh_state = _state(env, initial_object_z)
    fresh_tracked = _tracked_entity_positions_m(env, target_ids)
    fresh_events = _guarded_dispatch_invalidation_events(
        runtime_lease=runtime_lease,
        lease_candidate=lease_candidate,
        invocation_candidate=invocation_candidate,
        invocation_decision=invocation_decision,
        baseline_membership_ids=baseline_membership_ids,
        current_provider_instance_id=planning_provider.instance_id,
        state=fresh_state,
        baseline_tracked_positions_m=tracked_baseline,
        current_tracked_positions_m=fresh_tracked,
    )
    fresh_evidence = build_fresh_dispatch_evidence(
        runtime_lease=runtime_lease,
        source="live_simulator_continuation_rgbd_state",
        observation={
            "controlled_frame": {
                "position_m": fresh_state["eef_gripper_base_xyz"],
                "quaternion_wxyz": fresh_state[
                    "eef_gripper_base_quaternion_wxyz"
                ],
            },
            "interaction_frame": fresh_state.get("actuator_contact_geometry"),
            "rgbd_scene_geometry": fresh_state.get("rgbd_scene_geometry"),
            "tracked_entity_positions_m": fresh_tracked,
            "current_contact": fresh_state.get("current_contact"),
            "gripper_closed_fraction": fresh_state.get(
                "gripper_closed_fraction"
            ),
        },
        invalidation_events=fresh_events,
    )
    handler_registry = RuntimeWorldEffectHandlerRegistry()
    monitored_events: list[dict[str, Any]] = []
    issued_geometry_by_id = {
        item.entity_id: item.geometry for item in lease_candidate.geometry_bindings
    }
    obstacle_geometry_by_id = interaction_obstacle_geometry(
        issued_geometry_by_id,
        interaction_target_entity_id=_interaction_target_entity_id(
            invocation_candidate,
            invocation_decision,
        ),
    )

    if lease_candidate.tool_family == "motion":
        runtime_spec = next(
            (
                item
                for item in motion_registry.specs()
                if item.executor_id == runtime_lease.lease.tool_id
            ),
            None,
        )
        if runtime_spec is None:
            raise RuntimeError("issued continuation tool has no motion executor")
        selected_orientation_axis = next(
            (
                item
                for item in invocation_candidate.orientation_axes
                if item.alignment_id
                == invocation_decision.orientation_alignment_id
            ),
            None,
        )
        rgbd_axis_references = (
            {
                selected_orientation_axis.entity_id: np.asarray(
                    selected_orientation_axis.axis_robot_root,
                    dtype=np.float64,
                )
            }
            if selected_orientation_axis is not None
            else {}
        )

        def handler(
            invocation_arguments: Mapping[str, Any],
            tool_configuration: Mapping[str, Any],
            active_lease: Any,
        ) -> Mapping[str, Any]:
            target_position = torch.tensor(
                invocation_arguments["target_position_m"], dtype=torch.float32
            )
            target_quaternion = torch.tensor(
                invocation_arguments["target_quaternion_wxyz"],
                dtype=torch.float32,
            )
            initial_action = _current_robot_joint_action(
                env,
                gripper_closed_fraction=float(
                    fresh_state["gripper_closed_fraction"]
                ),
            )

            def observe_clearance() -> tuple[float, str]:
                interaction = _actuator_contact_geometry(env, _eef_position(env))
                current_position = interaction.get("contact_center_xyz_m")
                target_interaction_position = (
                    invocation_decision.grounding_assessment.get(
                        "realized_interaction_position_m"
                    )
                )
                if not isinstance(current_position, (list, tuple)) or not isinstance(
                    target_interaction_position, (list, tuple)
                ):
                    raise RuntimeError(
                        "continuation clearance observer lacks an interaction frame"
                    )
                clearance, _ = _interaction_path_clearance_m(
                    current_position,
                    target_interaction_position,
                    obstacle_geometry_by_id,
                )
                if not math.isfinite(clearance):
                    raise RuntimeError(
                        "continuation clearance observer found no finite clearance"
                    )
                return clearance, "sim6.live_continuation_interaction_frame"

            def observe_orientation(
                entity_id: str, reference_axis: np.ndarray
            ) -> tuple[float, str, Mapping[str, Any]]:
                geometry = _rgbd_scene_geometry_observation(env)
                current = next(
                    (
                        item
                        for item in geometry.get("geometries", [])
                        if isinstance(item, Mapping)
                        and item.get("runtime_id") == entity_id
                    ),
                    None,
                )
                if not isinstance(current, Mapping):
                    raise ValueError(
                        f"RGB-D continuation target {entity_id!r} is not visible"
                    )
                error_deg = _axis_set_error_deg(
                    reference_axis,
                    current.get("oriented_footprint_axes_base"),
                )
                if error_deg is None:
                    raise ValueError("RGB-D continuation axis set is unavailable")
                return (
                    error_deg,
                    "rgbd.oriented_footprint_axis_set_robot_root",
                    {
                        "entity_id": entity_id,
                        "reference_axis_robot_root": reference_axis.tolist(),
                        "observed_axes_robot_root": current.get(
                            "oriented_footprint_axes_base"
                        ),
                        "orientation_error_deg": error_deg,
                    },
                )

            def monitor() -> dict[str, Any] | None:
                if not active_lease.active:
                    event = {
                        "condition_id": "runtime.maximum_duration_elapsed",
                        "reason": "issued runtime lease is no longer active",
                        "lease_state": active_lease.state,
                    }
                    monitored_events.append(event)
                    return {**event, "converged": False}
                monitor_state = _state(env, initial_object_z)
                current_tracked = _tracked_entity_positions_m(env, target_ids)
                events = _guarded_dispatch_invalidation_events(
                    runtime_lease=active_lease,
                    lease_candidate=lease_candidate,
                    invocation_candidate=invocation_candidate,
                    invocation_decision=invocation_decision,
                    baseline_membership_ids=baseline_membership_ids,
                    current_provider_instance_id=planning_provider.instance_id,
                    state=monitor_state,
                    baseline_tracked_positions_m=tracked_baseline,
                    current_tracked_positions_m=current_tracked,
                )
                if not events:
                    return None
                event = events[0]
                active_lease.observe_invalidation(
                    event.condition_id, event.evidence
                )
                serialized = event.to_dict()
                monitored_events.append(serialized)
                return {**serialized, "lease_state": active_lease.state, "converged": False}

            (
                next_obs,
                terminal,
                final_action,
                motion_report,
            ) = _move_eef_to_target(
                env,
                obs,
                initial_action,
                target_position,
                target_quaternion,
                phase=f"world_effect:{lease_candidate.purpose}",
                gripper_closed=bool(
                    float(initial_action[0, 7].detach().cpu()) > 0.5
                ),
                initial_object_z=initial_object_z,
                executor_config=dict(tool_configuration),
                tracked_position_references_m=tracked_baseline,
                rgbd_axis_references=rgbd_axis_references,
                tracked_orientation_observer=observe_orientation,
                observed_clearance_observer=observe_clearance,
                checkpoint_callback=None,
                early_stop_callback=monitor,
            )
            post_state = _state(env, initial_object_z)
            post_tracked = _tracked_entity_positions_m(env, target_ids)
            post_events = _guarded_dispatch_invalidation_events(
                runtime_lease=active_lease,
                lease_candidate=lease_candidate,
                invocation_candidate=invocation_candidate,
                invocation_decision=invocation_decision,
                baseline_membership_ids=baseline_membership_ids,
                current_provider_instance_id=planning_provider.instance_id,
                state=post_state,
                baseline_tracked_positions_m=tracked_baseline,
                current_tracked_positions_m=post_tracked,
            )
            if post_events and active_lease.active:
                event = post_events[0]
                active_lease.observe_invalidation(
                    event.condition_id, event.evidence
                )
                monitored_events.append(event.to_dict())
            if not motion_report.get("converged") and active_lease.active:
                active_lease.revoke(
                    reason="dispatch.motion_not_converged",
                    evidence={
                        "target_error_after_m": motion_report.get(
                            "target_error_after_m"
                        ),
                        "orientation_error_after_deg": motion_report.get(
                            "orientation_error_after_deg"
                        ),
                        "terminal": terminal,
                    },
                )
            handler_result_box["_obs"] = next_obs
            return {
                "executor_id": runtime_spec.executor_id,
                "executor_tool_name": runtime_spec.tool_name,
                "tool_family": "motion",
                "execution_report": motion_report,
                "motion_report": motion_report,
                "monitored_invalidation_events": monitored_events,
                "terminal": terminal,
                "final_action": final_action.detach().cpu().tolist(),
                "post_dispatch_observation": {
                    "eef_gripper_base_xyz": post_state[
                        "eef_gripper_base_xyz"
                    ],
                    "eef_gripper_base_quaternion_wxyz": post_state[
                        "eef_gripper_base_quaternion_wxyz"
                    ],
                    "rgbd_scene_geometry": post_state.get(
                        "rgbd_scene_geometry"
                    ),
                    "tracked_entity_positions_m": post_tracked,
                    "current_contact": post_state.get("current_contact"),
                },
                "requires_model_replan": True,
            }

    elif lease_candidate.tool_family == "actuator":
        runtime_spec = next(
            (
                item
                for item in actuator_registry.specs()
                if item.executor_id == runtime_lease.lease.tool_id
            ),
            None,
        )
        if runtime_spec is None:
            raise RuntimeError("issued continuation tool has no actuator executor")

        def handler(
            invocation_arguments: Mapping[str, Any],
            tool_configuration: Mapping[str, Any],
            active_lease: Any,
        ) -> Mapping[str, Any]:
            command = runtime_spec.validate_command(invocation_arguments)
            configuration = runtime_spec.validate_configuration(tool_configuration)
            initial_action = _current_robot_joint_action(
                env,
                gripper_closed_fraction=float(
                    fresh_state["gripper_closed_fraction"]
                ),
            )
            next_obs, terminal, final_action, actuator_report = (
                _execute_binary_actuator_tool(
                    env,
                    obs,
                    initial_action,
                    {
                        "executor_id": runtime_spec.executor_id,
                        "command": command,
                        "executor_config": configuration,
                    },
                    initial_object_z=initial_object_z,
                )
            )
            post_state = _state(env, initial_object_z)
            post_tracked = _tracked_entity_positions_m(env, target_ids)
            post_events = _guarded_dispatch_invalidation_events(
                runtime_lease=active_lease,
                lease_candidate=lease_candidate,
                invocation_candidate=invocation_candidate,
                invocation_decision=invocation_decision,
                baseline_membership_ids=baseline_membership_ids,
                current_provider_instance_id=planning_provider.instance_id,
                state=post_state,
                baseline_tracked_positions_m=tracked_baseline,
                current_tracked_positions_m=post_tracked,
            )
            if post_events and active_lease.active:
                event = post_events[0]
                active_lease.observe_invalidation(
                    event.condition_id, event.evidence
                )
                monitored_events.append(event.to_dict())
            if terminal and active_lease.active:
                active_lease.revoke(
                    reason="dispatch.environment_terminal",
                    evidence={"terminal": True},
                )
            handler_result_box["_obs"] = next_obs
            return {
                "executor_id": runtime_spec.executor_id,
                "executor_tool_name": runtime_spec.tool_name,
                "tool_family": "actuator",
                "execution_report": actuator_report,
                "actuator_report": actuator_report,
                "monitored_invalidation_events": monitored_events,
                "terminal": terminal,
                "final_action": final_action.detach().cpu().tolist(),
                "post_dispatch_observation": {
                    "eef_gripper_base_xyz": post_state[
                        "eef_gripper_base_xyz"
                    ],
                    "eef_gripper_base_quaternion_wxyz": post_state[
                        "eef_gripper_base_quaternion_wxyz"
                    ],
                    "rgbd_scene_geometry": post_state.get(
                        "rgbd_scene_geometry"
                    ),
                    "tracked_entity_positions_m": post_tracked,
                    "current_contact": post_state.get("current_contact"),
                    "gripper_closed_fraction": post_state.get(
                        "gripper_closed_fraction"
                    ),
                },
                "requires_model_replan": True,
            }

    else:
        raise RuntimeError(
            f"unsupported continuation tool family {lease_candidate.tool_family!r}"
        )

    handler_result_box: dict[str, Any] = {}

    def capture_handler(
        invocation_arguments: Mapping[str, Any],
        tool_configuration: Mapping[str, Any],
        active_lease: Any,
    ) -> Mapping[str, Any]:
        result = handler(
            invocation_arguments,
            tool_configuration,
            active_lease,
        )
        handler_result_box.update(result)
        return result

    handler_registry.register(runtime_lease.lease.tool_id, capture_handler)
    dispatcher = GuardedWorldEffectDispatcher(
        runtime_lease=runtime_lease,
        handlers=handler_registry,
        maximum_evidence_age_s=maximum_evidence_age_s,
        maximum_permit_lifetime_s=maximum_permit_lifetime_s,
    )
    permit = dispatcher.mint_permit(fresh_evidence)
    dispatch_outcome = dispatcher.dispatch(permit)
    next_obs = handler_result_box.pop("_obs", obs)
    post_frame = _single_exterior_frame(next_obs)
    cv2.imwrite(
        str(artifact_dir / f"{operation_index:02d}_guarded_post_dispatch.jpg"),
        cv2.cvtColor(post_frame, cv2.COLOR_RGB2BGR),
    )
    result = dispatch_outcome.to_dict()
    handler_result = result["handler_result"]
    terminal = bool(handler_result.get("terminal"))
    return next_obs, terminal, {
        "operation_index": operation_index,
        "tool_family": lease_candidate.tool_family,
        "tool_id": runtime_lease.lease.tool_id,
        "purpose": lease_candidate.purpose,
        "fresh_evidence": fresh_evidence.to_dict(),
        "permit": permit.to_dict(),
        "outcome": result,
        "runtime_lease_after": runtime_lease.to_dict(),
        "requires_model_replan": True,
        "dispatch_enabled": False,
        "motion_authority": False,
        "execution_authority": False,
        "authority_scope": [],
    }


def _write_trace(path: Path, trace: dict[str, Any]) -> None:
    """Atomically publish the latest episode evidence, including partial runs."""
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(trace, indent=2) + "\n")
    temporary.replace(path)


def _next_episode_index(output_dir: Path) -> int:
    indices = []
    for path in output_dir.glob("run_*.hdf5"):
        try:
            indices.append(int(path.stem.split("_")[-1]))
        except ValueError:
            continue
    return max(indices, default=-1) + 1


def _run_joint_segment(
    env: Any,
    obs: dict[str, Any],
    joint_states: np.ndarray,
    recorded_actions: np.ndarray,
    start: int,
    end: int,
) -> tuple[dict[str, Any], bool, torch.Tensor]:
    action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
    terminal = False
    for step in range(start, end):
        state_index = min(step + 1, len(joint_states) - 1)
        action[0, :7] = torch.as_tensor(
            joint_states[state_index, :7], dtype=torch.float32, device=env.device
        )
        action[0, 7] = float(recorded_actions[min(step, len(recorded_actions) - 1), 7])
        obs, _, terminated, truncated, _ = _step_env(env, action)
        terminal = bool(torch.as_tensor(terminated).any()) or bool(
            torch.as_tensor(truncated).any()
        )
        if terminal:
            break
    return obs, terminal, action


def _hold_joint_action(
    env: Any,
    obs: dict[str, Any],
    action: torch.Tensor,
    steps: int,
    gripper_closed: bool | None = None,
) -> tuple[dict[str, Any], bool]:
    command = action.clone()
    if gripper_closed is not None:
        command[0, 7] = 1.0 if gripper_closed else 0.0
    terminal = False
    for _ in range(steps):
        obs, _, terminated, truncated, _ = _step_env(env, command)
        terminal = bool(torch.as_tensor(terminated).any()) or bool(
            torch.as_tensor(truncated).any()
        )
        if terminal:
            break
    return obs, terminal


def _current_robot_joint_action(
    env: Any, *, gripper_closed_fraction: float
) -> torch.Tensor:
    """Create a hold command from the active embodiment's current joint state."""
    robot = env.scene["robot"]
    joint_positions = getattr(robot.data.joint_pos, "torch", robot.data.joint_pos)
    action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
    arm_ids = [
        robot.data.joint_names.index(f"panda_joint{index}")
        for index in range(1, 8)
    ]
    action[0, :7] = joint_positions[0, arm_ids]
    action[0, 7] = 1.0 if gripper_closed_fraction > 0.5 else 0.0
    return action


def _execute_binary_actuator_tool(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    decision: dict[str, Any],
    *,
    initial_object_z: float,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Adapt one admitted runtime actuator call to RoboLab's binary action."""
    if decision.get("executor_id") != "binary_end_effector_clamp":
        raise RuntimeError(
            f"Unsupported actuator executor: {decision.get('executor_id')!r}"
        )
    requested_state = decision.get("command", {}).get("state")
    if requested_state not in {"engage", "disengage", "maintain"}:
        raise RuntimeError(f"Invalid admitted actuator state: {requested_state!r}")
    command = last_action.clone()
    engaged_before = bool(float(command[0, 7].detach().cpu()) > 0.5)
    engaged_after = (
        True
        if requested_state == "engage"
        else False if requested_state == "disengage" else engaged_before
    )
    settle_steps = int(decision.get("executor_config", {}).get("settle_steps", 35))
    state_before = _state(env, initial_object_z)
    obs, terminal = _hold_joint_action(
        env,
        obs,
        command,
        settle_steps,
        gripper_closed=engaged_after,
    )
    command[0, 7] = 1.0 if engaged_after else 0.0
    state_after = _state(env, initial_object_z)
    return (
        obs,
        terminal,
        command,
        {
            "executor_id": decision["executor_id"],
            "requested_state": requested_state,
            "engaged_before": engaged_before,
            "engaged_after": engaged_after,
            "settle_steps": settle_steps,
            "state_before": {
                "finger_joint_rad": state_before["finger_joint_rad"],
                "gripper_closed_fraction": state_before[
                    "gripper_closed_fraction"
                ],
                "current_contact": state_before["current_contact"],
            },
            "state_after": {
                "finger_joint_rad": state_after["finger_joint_rad"],
                "gripper_closed_fraction": state_after[
                    "gripper_closed_fraction"
                ],
                "current_contact": state_after["current_contact"],
            },
            "terminal": terminal,
        },
    )


def _actuator_feedback_event_from_execution(
    execution: dict[str, Any],
    policy: ActuatorFeedbackEventPolicy,
) -> dict[str, Any]:
    """Translate adapter telemetry into the task-neutral feedback-event contract."""
    before = execution["state_before"]
    after = execution["state_after"]
    event = assess_actuator_feedback_event(
        position_before=float(before["gripper_closed_fraction"]),
        position_after=float(after["gripper_closed_fraction"]),
        force_before_n=float(before["current_contact"]["net_force_n"]),
        force_after_n=float(after["current_contact"]["net_force_n"]),
        touch_before=bool(before["current_contact"]["touch"]),
        touch_after=bool(after["current_contact"]["touch"]),
        policy=policy,
    )
    result = event.to_dict()
    loaded_contact_supported = retained_contact_supports_loaded_actuator(
        after["current_contact"]
    )
    invalidation_reason = actuator_command_outcome_invalidation_reason(
        requested_state=str(execution["requested_state"]),
        actuator_position_changed=bool(event.actuator_position_changed),
        loaded_contact_supported=loaded_contact_supported,
    )
    result.update(
        {
            "loaded_contact_supported_after": loaded_contact_supported,
            "actuator_outcome_invalidated": invalidation_reason is not None,
            "actuator_outcome_invalidation_reason": invalidation_reason,
        }
    )
    if invalidation_reason is not None:
        result["triggered"] = True
    return result


def _move_eef_to_target(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    target: torch.Tensor,
    target_quaternion_wxyz: torch.Tensor,
    phase: str,
    *,
    gripper_closed: bool,
    initial_object_z: float,
    executor_config: dict[str, Any] | None = None,
    carry_reference_offset: torch.Tensor | None = None,
    tracked_position_references_m: Mapping[str, Sequence[float]] | None = None,
    rgbd_axis_references: dict[str, np.ndarray] | None = None,
    tracked_orientation_observer: Callable[
        [str, np.ndarray], tuple[float, str, Mapping[str, Any]]
    ]
    | None = None,
    observed_clearance_observer: Callable[[], tuple[float, str]] | None = None,
    checkpoint_callback: Callable[
        [dict[str, Any], dict[str, Any]], dict[str, Any]
    ]
    | None = None,
    early_stop_callback: Callable[[], dict[str, Any] | None] | None = None,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Reach a live SE(3) target with bounded local DLS IK."""
    if target.shape != (3,) or not bool(torch.isfinite(target).all()):
        raise ValueError(f"Invalid adaptive target for {phase}: {target}")
    if target_quaternion_wxyz.shape != (4,) or not bool(
        torch.isfinite(target_quaternion_wxyz).all()
    ):
        raise ValueError(
            f"Invalid adaptive orientation target for {phase}: {target_quaternion_wxyz}"
        )
    effective_config = {
        "position_tolerance_m": args_cli.adaptive_tolerance,
        "translation_step_limit_m": args_cli.adaptive_max_step,
        "maximum_iterations": args_cli.adaptive_max_iterations,
        "settle_steps": args_cli.adaptive_settle_steps,
        "joint_step_limit_rad": args_cli.adaptive_max_joint_step,
        "damping": args_cli.adaptive_damping,
        "orientation_tolerance_deg": args_cli.adaptive_orientation_tolerance_deg,
        "rotation_step_limit_deg": args_cli.adaptive_max_angle_step_deg,
        "minimum_progress_m": 0.00025,
        "maximum_stalled_observations": 3,
        "require_contact": False,
        "minimum_contact_force_n": 0.0,
        "maximum_tracked_pose_error_m": None,
        "maximum_tracked_orientation_error_deg": None,
        "minimum_observed_clearance_m": None,
    }
    effective_config.update(executor_config or {})
    lease_conditions = _motion_lease_conditions_from_config(effective_config)
    sensor_predicate_registry = _motion_sensor_predicate_registry()
    sensor_predicate_leases = _motion_sensor_predicate_leases(effective_config)
    robot = env.scene["robot"]
    arm_joint_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    body_idx = robot.data.body_names.index("base_link")
    jacobi_body_idx = body_idx - 1 if robot.is_fixed_base else body_idx
    jacobi_joint_ids = [index + robot.num_base_dofs for index in arm_joint_ids]
    command = last_action.clone()
    command[0, 7] = 1.0 if gripper_closed else 0.0
    target_cpu = target.detach().cpu().to(dtype=torch.float32)
    target_quat_cpu = target_quaternion_wxyz.detach().cpu().to(dtype=torch.float32)
    orientation_tolerance = np.deg2rad(
        effective_config["orientation_tolerance_deg"]
    )
    maximum_angle_step = np.deg2rad(effective_config["rotation_step_limit_deg"])
    terminal = False
    iterations: list[dict[str, Any]] = []
    eef_start = _eef_position(env)
    error_start = float(torch.linalg.vector_norm(target_cpu - eef_start))
    orientation_error_start = float(
        torch.linalg.vector_norm(
            quaternion_error_axis_angle_wxyz(target_quat_cpu, _eef_quaternion(env))
        )
    )
    previous_error = error_start
    previous_orientation_error = orientation_error_start
    recovery_request: dict[str, Any] | None = None
    early_stop: dict[str, Any] | None = None
    stalled_observations = 0

    for iteration in range(int(effective_config["maximum_iterations"])):
        eef_before = _eef_position(env)
        error = target_cpu - eef_before
        error_norm = float(torch.linalg.vector_norm(error))
        orientation_error = quaternion_error_axis_angle_wxyz(
            target_quat_cpu, _eef_quaternion(env)
        )
        orientation_error_norm = float(torch.linalg.vector_norm(orientation_error))
        if (
            error_norm <= effective_config["position_tolerance_m"]
            and orientation_error_norm <= orientation_tolerance
        ):
            break
        xyz_step = bounded_vector_step(
            error.to(device=env.device), effective_config["translation_step_limit_m"]
        )
        desired_twist_w = torch.zeros(6, dtype=torch.float32, device=env.device)
        desired_twist_w[:3] = xyz_step
        desired_twist_w[3:] = bounded_vector_step(
            orientation_error.to(device=env.device), maximum_angle_step
        )
        jacobian_w = robot.data.body_link_jacobian_w.torch[
            0, jacobi_body_idx
        ][:, jacobi_joint_ids]
        delta_joint = damped_least_squares_delta(
            jacobian_w,
            desired_twist_w,
            effective_config["damping"],
            effective_config["joint_step_limit_rad"],
        )
        joint_pos = robot.data.joint_pos.torch[0, arm_joint_ids]
        joint_limits = robot.data.soft_joint_pos_limits.torch[0, arm_joint_ids]
        command[0, :7] = torch.clamp(
            joint_pos + delta_joint,
            min=joint_limits[:, 0] + 1.0e-3,
            max=joint_limits[:, 1] - 1.0e-3,
        )
        obs, terminal = _hold_joint_action(
            env,
            obs,
            command,
            int(effective_config["settle_steps"]),
            gripper_closed=gripper_closed,
        )
        eef_after = _eef_position(env)
        error_after = float(torch.linalg.vector_norm(target_cpu - eef_after))
        orientation_error_after = float(
            torch.linalg.vector_norm(
                quaternion_error_axis_angle_wxyz(
                    target_quat_cpu, _eef_quaternion(env)
                )
            )
        )
        record = {
            "iteration": iteration + 1,
            "eef_before_xyz": eef_before.tolist(),
            "requested_xyz_step_m": xyz_step.detach().cpu().tolist(),
            "max_abs_joint_step_rad": float(torch.max(torch.abs(delta_joint))),
            "eef_after_xyz": eef_after.tolist(),
            "target_error_after_m": error_after,
            "orientation_error_before_deg": float(np.rad2deg(orientation_error_norm)),
            "orientation_error_after_deg": float(np.rad2deg(orientation_error_after)),
            "terminal": terminal,
        }
        checkpoint_reason: str | None = None
        tracked_pose_error_m = None
        tracked_pose_source_id = None
        tracked_object_id = effective_config.get("tracked_object_id")
        tracked_position_reference = (
            tracked_position_references_m.get(tracked_object_id)
            if isinstance(tracked_object_id, str)
            and tracked_position_references_m is not None
            else None
        )
        if tracked_position_reference is not None:
            tracked_pose_error_m = float(
                torch.linalg.vector_norm(
                    _local_position(env, tracked_object_id)
                    - torch.as_tensor(
                        tracked_position_reference, dtype=torch.float32
                    )
                )
            )
            tracked_pose_source_id = "sim6.runtime_entity_pose_tracker"
        elif carry_reference_offset is not None:
            tracked_object = _movable_object_position(env)
            tracked_pose_error_m = float(
                torch.linalg.vector_norm(
                    (eef_after - tracked_object) - carry_reference_offset
                )
            )
            tracked_pose_source_id = "sim6.privileged_relative_pose_adapter"
        observed_clearance_m = None
        observed_clearance_source_id = None
        if lease_conditions.minimum_observed_clearance_m is not None:
            if observed_clearance_observer is not None:
                (
                    observed_clearance_m,
                    observed_clearance_source_id,
                ) = observed_clearance_observer()
                observed_clearance_m = float(observed_clearance_m)
            else:
                observed_clearance_m = float(
                    _movable_object_position(env)[2]
                    - _target_receptacle_position(env)[2]
                )
                observed_clearance_source_id = (
                    "sim6.privileged_object_to_support_height_adapter"
                )
        current_contact = _current_contact_observation(env)
        observed_at_s = time.monotonic()
        sensor_observations: list[SensorObservation] = []
        progress_m = previous_error - error_after
        if progress_m < float(effective_config["minimum_progress_m"]):
            stalled_observations += 1
        else:
            stalled_observations = 0
        sensor_observations.append(
            SensorObservation(
                channel_id="motion.stalled_observation_count",
                source_id="sim6.robot_kinematic_state_adapter",
                sequence=iteration + 1,
                timestamp_s=observed_at_s,
                value=stalled_observations,
            )
        )
        record["measured_target_progress_m"] = progress_m
        record["stalled_observation_count"] = stalled_observations
        if bool(current_contact.get("available")):
            sensor_observations.extend(
                (
                    SensorObservation(
                        channel_id="gripper.touch",
                        source_id="sim6.gripper_contact_sensor",
                        sequence=iteration + 1,
                        timestamp_s=observed_at_s,
                        value=bool(current_contact["touch"]),
                    ),
                    SensorObservation(
                        channel_id="gripper.contact_force_n",
                        source_id="sim6.gripper_contact_sensor",
                        sequence=iteration + 1,
                        timestamp_s=observed_at_s,
                        value=float(current_contact["net_force_n"]),
                    ),
                )
            )
        if tracked_pose_error_m is not None:
            sensor_observations.append(
                SensorObservation(
                    channel_id="object.tracked_translation_error_m",
                    source_id=str(tracked_pose_source_id),
                    sequence=iteration + 1,
                    timestamp_s=observed_at_s,
                    value=tracked_pose_error_m,
                )
            )
        rgbd_axis_observation = None
        rgbd_axis_error = None
        rgbd_axis_error_message = None
        rgbd_axis_source_id = None
        if (
            effective_config.get("maximum_tracked_orientation_error_deg")
            is not None
        ):
            try:
                tracked_object_id = effective_config.get("tracked_object_id")
                if not isinstance(tracked_object_id, str) or not tracked_object_id:
                    raise ValueError(
                        "tracked_object_id is unavailable for the RGB-D lease"
                    )
                reference_axis = (rgbd_axis_references or {}).get(
                    tracked_object_id
                )
                if reference_axis is None:
                    raise ValueError(
                        "RGB-D tracked-orientation reference is unavailable for "
                        f"{tracked_object_id!r}"
                    )
                if tracked_orientation_observer is not None:
                    (
                        rgbd_axis_error,
                        rgbd_axis_source_id,
                        rgbd_axis_observation,
                    ) = tracked_orientation_observer(
                        tracked_object_id, reference_axis
                    )
                    rgbd_axis_error = float(rgbd_axis_error)
                else:
                    rgbd_axis_observation = _rgbd_object_axis_observation(
                        env,
                        prim_label_fragment=f"/scene/{tracked_object_id}",
                        reference_axis=reference_axis,
                    )
                    rgbd_axis_error = float(
                        rgbd_axis_observation["orientation_error_deg"]
                    )
                    rgbd_axis_source_id = "rgbd.instance_depth_major_axis"
                sensor_observations.append(
                    SensorObservation(
                        channel_id="rgbd.object_orientation_error_deg",
                        source_id=str(rgbd_axis_source_id),
                        sequence=iteration + 1,
                        timestamp_s=observed_at_s,
                        value=rgbd_axis_error,
                        frame_id="over_shoulder_left_camera",
                    )
                )
            except ValueError as exc:
                rgbd_axis_error_message = str(exc)
        if observed_clearance_m is not None:
            sensor_observations.append(
                SensorObservation(
                    channel_id="scene.observed_clearance_m",
                    source_id=str(observed_clearance_source_id),
                    sequence=iteration + 1,
                    timestamp_s=observed_at_s,
                    value=observed_clearance_m,
                )
            )
        ros2_overlay_channels: list[str] = []
        if (
            ACTIVE_ROS2_SENSOR_INGRESS is not None
            and ACTIVE_ROS2_SENSOR_INGRESS.available
        ):
            ros2_snapshot = ACTIVE_ROS2_SENSOR_INGRESS.buffer.snapshot()
            ros2_overlay_channels = list(ros2_snapshot.channels())
            sensor_observations = list(
                overlay_sensor_observations(sensor_observations, ros2_snapshot)
            )
        observation_snapshot = SensorObservationSnapshot(sensor_observations)
        iteration_predicate_leases = sensor_predicate_leases
        if observation_snapshot.get("scene.collision_stop") is not None:
            iteration_predicate_leases = (
                *iteration_predicate_leases,
                SensorPredicateLease("scene.no_collision_stop", {}),
            )
        lease_assessment = sensor_predicate_registry.assess(
            iteration_predicate_leases,
            observation_snapshot,
            evaluated_at_s=observed_at_s,
        )
        channel_sources = {
            item.channel_id: item.source_id for item in sensor_observations
        }
        record["motion_lease"] = {
            **lease_assessment.to_dict(),
            "active_predicates": [
                item.to_dict() for item in iteration_predicate_leases
            ],
            "observation_channels": [
                item.to_dict() for item in sensor_observations
            ],
            "ros2_overlay_channels": ros2_overlay_channels,
            "contact_source": (
                channel_sources.get("gripper.touch")
                if channel_sources.get("gripper.touch")
                == channel_sources.get("gripper.contact_force_n")
                else "+".join(
                    sorted(
                        {
                            source
                            for source in (
                                channel_sources.get("gripper.touch"),
                                channel_sources.get("gripper.contact_force_n"),
                            )
                            if source is not None
                        }
                    )
                )
            ),
            "tracked_pose_source": channel_sources.get(
                "object.tracked_translation_error_m"
            ),
            "observed_clearance_source": channel_sources.get(
                "scene.observed_clearance_m"
            ),
            "tracked_orientation_source": channel_sources.get(
                "rgbd.object_orientation_error_deg"
            ),
            "collision_stop_source": channel_sources.get("scene.collision_stop"),
            "tracked_orientation_object_id": effective_config.get(
                "tracked_object_id"
            ),
            "rgbd_tracked_axis": rgbd_axis_observation,
            "rgbd_tracking_error": rgbd_axis_error_message,
        }
        local_invalidation_reasons = list(lease_assessment.invalidation_reasons)
        if error_after > previous_error + 0.008:
            local_invalidation_reasons.append("motion_execution_diverged")
        if orientation_error_after > previous_orientation_error + np.deg2rad(8.0):
            local_invalidation_reasons.append("motion_orientation_diverged")
        if local_invalidation_reasons:
            checkpoint_reason = "lease_invalidated:" + ",".join(
                dict.fromkeys(local_invalidation_reasons)
            )
        if early_stop_callback is not None:
            early_stop = early_stop_callback()
            if early_stop is not None:
                record["early_stop"] = early_stop
        # Preserve the last executed transition even if a local stop or coach
        # pause raises below. This is audit evidence, never success admission.
        iterations.append(record)
        periodic_checkpoint = (
            args_cli.periodic_motion_observations
            and (iteration + 1) % args_cli.coach_interval_iterations == 0
        )
        target_changed = False
        if early_stop is None and checkpoint_callback is not None and (
            periodic_checkpoint or checkpoint_reason is not None
        ):
            checkpoint = {
                "reason": checkpoint_reason or "periodic",
                "phase": phase,
                "iteration": iteration + 1,
                "target_error_m": error_after,
                "orientation_error_deg": float(np.rad2deg(orientation_error_after)),
                "motion_lease": record.get("motion_lease"),
                "lease_condition_sources": {
                    "contact": record["motion_lease"].get("contact_source"),
                    "tracked_pose": record["motion_lease"].get(
                        "tracked_pose_source"
                    ),
                    "tracked_orientation": record["motion_lease"].get(
                        "tracked_orientation_source"
                    ),
                    "observed_clearance": record["motion_lease"].get(
                        "observed_clearance_source"
                    ),
                    "collision_stop": record["motion_lease"].get(
                        "collision_stop_source"
                    ),
                    "model_polling": (
                        "periodic_or_event"
                        if args_cli.periodic_motion_observations
                        else "event_or_completion_only"
                    ),
                },
                "current_target_xyz_m": target_cpu.tolist(),
                "current_target_quaternion_wxyz": target_quat_cpu.tolist(),
                "executor_id": "bounded_dls_ik",
                "executor_config": dict(effective_config),
            }
            tracked_orientation_object_id = record["motion_lease"].get(
                "tracked_orientation_object_id"
            )
            tracked_orientation_source = checkpoint[
                "lease_condition_sources"
            ].get("tracked_orientation")
            checkpoint["lease_condition_sources"]["tracked_orientation"] = (
                {
                    tracked_orientation_object_id: tracked_orientation_source
                }
                if tracked_orientation_object_id
                and tracked_orientation_source is not None
                else {}
            )
            checkpoint_decision = checkpoint_callback(obs, checkpoint)
            record["coach_checkpoint"] = checkpoint_decision
            if checkpoint_decision.get("decision") == "execute":
                updated_target = torch.tensor(
                    checkpoint_decision.get("target_xyz_m", target_cpu.tolist()),
                    dtype=torch.float32,
                )
                if updated_target.shape != (3,) or not bool(
                    torch.isfinite(updated_target).all()
                ):
                    raise RuntimeError(
                        f"Model returned an invalid world target: {updated_target}"
                    )
                translation_target_changed = not bool(
                    torch.allclose(updated_target, target_cpu)
                )
                if translation_target_changed:
                    record["target_before_model_correction_m"] = target_cpu.tolist()
                    target_cpu = updated_target
                    record["target_after_model_correction_m"] = target_cpu.tolist()
                    error_after = float(torch.linalg.vector_norm(target_cpu - eef_after))
                    record["target_error_after_model_correction_m"] = error_after
                updated_target_quaternion = torch.tensor(
                    checkpoint_decision.get(
                        "target_quaternion_wxyz", target_quat_cpu.tolist()
                    ),
                    dtype=torch.float32,
                )
                if updated_target_quaternion.shape != (4,) or not bool(
                    torch.isfinite(updated_target_quaternion).all()
                ):
                    raise RuntimeError(
                        "Model returned an invalid world orientation target: "
                        f"{updated_target_quaternion}"
                    )
                quaternion_norm = torch.linalg.vector_norm(
                    updated_target_quaternion
                )
                if float(quaternion_norm) <= 1.0e-9:
                    raise RuntimeError(
                        "Model returned a zero-magnitude world orientation target"
                    )
                updated_target_quaternion = (
                    updated_target_quaternion / quaternion_norm
                )
                orientation_target_changed = not bool(
                    torch.allclose(
                        updated_target_quaternion,
                        target_quat_cpu,
                        atol=1.0e-6,
                    )
                )
                if orientation_target_changed:
                    record["target_quaternion_before_model_correction_wxyz"] = (
                        target_quat_cpu.tolist()
                    )
                    target_quat_cpu = updated_target_quaternion
                    record["target_quaternion_after_model_correction_wxyz"] = (
                        target_quat_cpu.tolist()
                    )
                    orientation_error_after = float(
                        torch.linalg.vector_norm(
                            quaternion_error_axis_angle_wxyz(
                                target_quat_cpu, _eef_quaternion(env)
                            )
                        )
                    )
                    record[
                        "orientation_error_after_model_correction_deg"
                    ] = float(np.rad2deg(orientation_error_after))
                target_changed = (
                    translation_target_changed or orientation_target_changed
                )
                updated_config = checkpoint_decision.get("executor_config") or {}
                if updated_config:
                    effective_config.update(updated_config)
                    lease_conditions = _motion_lease_conditions_from_config(
                        effective_config
                    )
                    sensor_predicate_leases = _motion_sensor_predicate_leases(
                        effective_config
                    )
                    orientation_tolerance = np.deg2rad(
                        effective_config["orientation_tolerance_deg"]
                    )
                    maximum_angle_step = np.deg2rad(
                        effective_config["rotation_step_limit_deg"]
                    )
                    record["executor_config_after_model_call"] = dict(
                        effective_config
                    )
            if checkpoint_reason is not None:
                if checkpoint_decision.get("decision") == "abort":
                    raise RuntimeError(
                        f"Local safety stopped {phase} and Gemini aborted at "
                        f"iteration {iteration + 1}: {checkpoint_reason}; "
                        f"coach={checkpoint_decision}"
                    )
                if checkpoint_decision.get("decision") == "execute":
                    record["lease_reauthorized_by_model"] = True
                    stalled_observations = 0
                else:
                    recovery_request = {
                        **checkpoint,
                        "reason": "model_requested_hold",
                        "lease_invalidation_reason": checkpoint_reason,
                        "coach_decision": checkpoint_decision,
                    }
            elif checkpoint_decision.get("decision") == "retry":
                recovery_request = {
                    **checkpoint,
                    "reason": "model_requested_hold",
                    "coach_decision": checkpoint_decision,
                }
            elif checkpoint_decision.get("decision") != "execute":
                raise RuntimeError(
                    f"Gemini paused {phase} at mid-motion checkpoint: "
                    f"{checkpoint_decision}"
                )
        print(
            f"[adaptive-ik] phase={phase} iteration={iteration + 1} "
            f"error={error_norm:.4f}→{error_after:.4f}m "
            f"angle={np.rad2deg(orientation_error_norm):.1f}→"
            f"{np.rad2deg(orientation_error_after):.1f}deg "
            f"step={record['requested_xyz_step_m']} "
            f"max_dq={record['max_abs_joint_step_rad']:.4f}rad",
            flush=True,
        )
        if recovery_request is not None:
            print(
                f"[adaptive-ik] paused {phase} for bounded recovery: "
                f"{recovery_request['reason']}",
                flush=True,
            )
            break
        if early_stop is not None:
            print(
                f"[adaptive-ik] stopped {phase} on measured condition: "
                f"{early_stop}",
                flush=True,
            )
            break
        if terminal:
            break
        previous_error = error_after
        previous_orientation_error = orientation_error_after

    eef_final = _eef_position(env)
    error_final = float(torch.linalg.vector_norm(target_cpu - eef_final))
    orientation_error_final = float(
        torch.linalg.vector_norm(
            quaternion_error_axis_angle_wxyz(target_quat_cpu, _eef_quaternion(env))
        )
    )
    report = {
        "enabled": True,
        "phase": phase,
        "target_source": "live_object_pose",
        "target_xyz": target_cpu.tolist(),
        "target_quaternion_wxyz": target_quat_cpu.tolist(),
        "eef_start_xyz": eef_start.tolist(),
        "eef_final_xyz": eef_final.tolist(),
        "target_error_before_m": error_start,
        "target_error_after_m": error_final,
        "orientation_error_before_deg": float(np.rad2deg(orientation_error_start)),
        "orientation_error_after_deg": float(np.rad2deg(orientation_error_final)),
        "executor_id": "bounded_dls_ik",
        "executor_config": effective_config,
        "motion_lease": {
            "conditions": lease_conditions.to_dict(),
            "active_predicates": [
                item.to_dict() for item in sensor_predicate_leases
            ],
            "maximum_iterations": int(effective_config["maximum_iterations"]),
            "model_observation_mode": (
                "periodic_or_event"
                if args_cli.periodic_motion_observations
                else "event_or_completion_only"
            ),
            "tracking_source": (
                "sim_privileged_relative_pose"
                if carry_reference_offset is not None
                else None
            ),
            "tracked_orientation_source": (
                "rgbd_instance_depth_major_axis"
                if effective_config.get("tracked_object_id")
                in (rgbd_axis_references or {})
                else None
            ),
            "tracked_orientation_object_id": effective_config.get(
                "tracked_object_id"
            ),
            "clearance_source": (
                "sim_privileged_object_to_support_height"
                if lease_conditions.minimum_observed_clearance_m is not None
                else None
            ),
        },
        "orientation_tolerance_deg": effective_config[
            "orientation_tolerance_deg"
        ],
        "target_tolerance_m": effective_config["position_tolerance_m"],
        "recovery_requested": recovery_request is not None,
        "recovery_request": recovery_request,
        "early_stop": early_stop,
        "converged": bool(
            not terminal
            and (
            (
                error_final <= effective_config["position_tolerance_m"]
                and orientation_error_final <= orientation_tolerance
                and recovery_request is None
            )
            or bool(early_stop is not None and early_stop.get("converged"))
            )
        ),
        "iterations": iterations,
    }
    return obs, terminal, command, report


def _recover_transport_grasp(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    *,
    initial_object_z: float,
    grasp_offset_object: torch.Tensor,
    object_to_grasp_quat: torch.Tensor,
    checkpoint_callback: Callable[
        [dict[str, Any], dict[str, Any]], dict[str, Any]
    ],
) -> tuple[
    dict[str, Any],
    bool,
    torch.Tensor,
    torch.Tensor,
    torch.Tensor,
    dict[str, Any],
]:
    """Stabilize a shifted grasp or safely set down and reacquire the object."""
    command = last_action.clone()
    command[0, 7] = 1.0
    terminal = False
    segments: list[dict[str, Any]] = []
    eef_before = _eef_position(env)
    object_before = _movable_object_position(env)
    offset_before = eef_before - object_before

    obs, terminal = _hold_joint_action(
        env,
        obs,
        command,
        args_cli.recovery_hold_steps,
        gripper_closed=True,
    )
    if terminal:
        raise RuntimeError("Environment terminated during recovery stability hold")
    eef_after_hold = _eef_position(env)
    object_after_hold = _movable_object_position(env)
    offset_after = eef_after_hold - object_after_hold
    hold_assessment = assess_recovery_hold(
        offset_before=offset_before.numpy(),
        offset_after=offset_after.numpy(),
        object_z_after=float(object_after_hold[2]),
        object_initial_z=initial_object_z,
        maximum_hold_drift_m=args_cli.recovery_stability_drift,
        minimum_carried_lift_m=args_cli.minimum_transport_lift,
    )
    try:
        contact_frame = sensor_frame_from_isaac_env(env)
        contact_valid = bool(
            contact_frame.validity[5] > 0.5 and contact_frame.validity[6] > 0.5
        )
        contact_touch = bool(
            contact_frame.values[SIGNAL_SLICES["gripper_touch"]][0] >= 0.5
        )
        contact_force = contact_frame.values[
            SIGNAL_SLICES["gripper_contact_force"]
        ]
        contact_force_n = float(np.linalg.vector_norm(contact_force))
    except Exception:
        contact_valid = False
        contact_touch = False
        contact_force_n = 0.0
    contact_confirms_grasp = (
        args_cli.disable_contact_telemetry or (contact_valid and contact_touch)
    )
    report: dict[str, Any] = {
        "strategy": hold_assessment.strategy,
        "hold_steps": args_cli.recovery_hold_steps,
        "offset_before_hold_m": offset_before.tolist(),
        "offset_after_hold_m": offset_after.tolist(),
        "hold_assessment": hold_assessment.to_dict(),
        "contact_confirmation": {
            "required": not args_cli.disable_contact_telemetry,
            "valid": contact_valid,
            "touch": contact_touch,
            "net_force_n": contact_force_n,
            "confirms_grasp": contact_confirms_grasp,
        },
        "segments": segments,
        "completed": False,
    }

    if hold_assessment.safe_to_resume and contact_confirms_grasp:
        decision = checkpoint_callback(
            obs,
            {
                "reason": "recovery_hold_stable",
                "phase": "above_plate_recovery",
                "iteration": 0,
                "target_error_m": 0.0,
                "orientation_error_deg": 0.0,
                "local_safety": {
                    "safe": True,
                    "reasons": [],
                    "grasp_translation_drift_m": hold_assessment.hold_grasp_drift_m,
                    "object_lift_m": hold_assessment.object_lift_m,
                    "rgbd_clearance_m": None,
                },
            },
        )
        report["stability_coach_decision"] = decision
        if decision.get("decision") == "execute":
            report["completed"] = True
            report["outcome"] = "stable_grasp_relatched"
            return (
                obs,
                terminal,
                command,
                offset_after,
                _eef_quaternion(env),
                report,
            )
        if decision.get("decision") == "abort":
            raise RuntimeError(
                f"Gemini aborted after recovery stability hold: {decision}"
            )
        report["strategy"] = "set_down_and_regrasp"
        report["stability_override"] = "coach_requested_physical_regrasp"
    elif hold_assessment.safe_to_resume:
        report["strategy"] = "set_down_and_regrasp"
        report["stability_override"] = "contact_sensor_did_not_confirm_grasp"

    def move_segment(
        name: str,
        target: torch.Tensor,
        quaternion: torch.Tensor,
        *,
        closed: bool,
        early_stop_callback: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        nonlocal obs, terminal, command
        obs, terminal, command, segment_report = _move_eef_to_target(
            env,
            obs,
            command,
            target,
            quaternion,
            name,
            gripper_closed=closed,
            initial_object_z=initial_object_z,
            checkpoint_callback=None,
            early_stop_callback=early_stop_callback,
        )
        segments.append(segment_report)
        if terminal or not bool(segment_report["converged"]):
            raise RuntimeError(
                f"Recovery segment {name} failed to converge: "
                f"error={segment_report['target_error_after_m']:.4f} m"
            )

    # If the object is still carried, put it down vertically at its current XY
    # before opening. If it has already dropped, opening immediately avoids a
    # blind descent toward an uncertain object pose.
    if hold_assessment.object_lift_m >= args_cli.minimum_transport_lift:
        carry_offset = _eef_position(env) - _movable_object_position(env)
        set_down = _eef_position(env)
        set_down[2] = (
            initial_object_z
            + max(float(carry_offset[2]), GRIPPER_BASE_TO_FINGERTIP_M)
            + args_cli.recovery_set_down_clearance
        )
        set_down[2] = min(float(_eef_position(env)[2]), float(set_down[2]))

        support_monitor = SupportContactMonitor(
            object_initial_z=initial_object_z,
            set_down_clearance_m=args_cli.recovery_set_down_clearance,
        )

        def object_support_contact() -> dict[str, Any] | None:
            banana = _movable_object_position(env)
            eef = _eef_position(env)
            return support_monitor.update(
                object_z=float(banana[2]),
                eef_z=float(eef[2]),
                target_eef_z=float(set_down[2]),
                target_tolerance_m=args_cli.adaptive_tolerance,
            )

        move_segment(
            "recovery_set_down",
            set_down,
            _eef_quaternion(env),
            closed=True,
            early_stop_callback=object_support_contact,
        )

    obs, terminal = _hold_joint_action(
        env, obs, command, 35, gripper_closed=False
    )
    command[0, 7] = 0.0
    if terminal:
        raise RuntimeError("Environment terminated while opening for recovery")
    release_state = _state(env, initial_object_z)
    report["state_after_recovery_release"] = release_state

    retreat_target = _eef_position(env) + torch.tensor(
        [0.0, 0.0, args_cli.approach_clearance], dtype=torch.float32
    )
    move_segment(
        "recovery_retreat",
        retreat_target,
        _eef_quaternion(env),
        closed=False,
    )
    obs, terminal = _hold_joint_action(
        env, obs, command, args_cli.recovery_hold_steps, gripper_closed=False
    )
    if terminal:
        raise RuntimeError("Environment terminated while object settled for recovery")

    # Release-to-support motion is expected and may be large. Measure actual
    # stability across a second settled hold instead of comparing against the
    # pre-retreat release pose.
    settle_reference = _movable_object_position(env)
    settle_verification_steps = max(8, args_cli.recovery_hold_steps // 2)
    obs, terminal = _hold_joint_action(
        env,
        obs,
        command,
        settle_verification_steps,
        gripper_closed=False,
    )
    if terminal:
        raise RuntimeError("Environment terminated during recovery settle verification")

    banana = _movable_object_position(env)
    plate = _target_receptacle_position(env)
    settled_displacement = float(
        torch.linalg.vector_norm(banana - settle_reference)
    )
    placement_event = placement_completion_event(
        object_xyz=banana.numpy(),
        target_xyz=plate.numpy(),
        maximum_contact_height_m=max(args_cli.plate_contact_height, 0.100),
        settled_displacement_m=settled_displacement,
    )
    object_quaternion = _movable_object_quaternion(env)
    support_aligned_quaternion = torch.as_tensor(
        support_aligned_object_quaternion_wxyz(object_quaternion.numpy()),
        dtype=torch.float32,
    )
    report["settled_object_pose"] = {
        "xyz": banana.tolist(),
        "quaternion_wxyz": object_quaternion.tolist(),
        "support_aligned_quaternion_wxyz": support_aligned_quaternion.tolist(),
        "displacement_during_stability_hold_m": settled_displacement,
        "stability_verification_steps": settle_verification_steps,
    }
    report["placement_after_set_down"] = placement_event
    reacquire_decision = checkpoint_callback(
        obs,
        {
            "reason": "recovery_object_settled_reacquire",
            "phase": "recovery_reacquire",
            "iteration": len(segments),
            "target_error_m": 0.0,
            "orientation_error_deg": 0.0,
            "local_safety": {
                "safe": True,
                "reasons": [],
                "grasp_translation_drift_m": None,
                "object_lift_m": float(banana[2]) - initial_object_z,
                "rgbd_clearance_m": None,
            },
        },
    )
    report["reacquire_coach_decision"] = reacquire_decision
    if placement_event is not None:
        report["completed"] = True
        report["goal_completed"] = True
        report["outcome"] = "goal_completed_during_recovery_set_down"
        return (
            obs,
            terminal,
            command,
            _eef_position(env) - banana,
            _eef_quaternion(env),
            report,
        )
    if reacquire_decision.get("decision") != "execute":
        raise RuntimeError(
            f"Gemini refused recovery reacquisition: {reacquire_decision}"
        )
    grasp_xyz, grasp_quaternion = apply_object_relative_grasp(
        banana,
        support_aligned_quaternion,
        grasp_offset_object,
        object_to_grasp_quat,
    )
    approach = grasp_xyz + torch.tensor(
        [0.0, 0.0, args_cli.approach_clearance], dtype=torch.float32
    )
    move_segment(
        "recovery_approach",
        approach,
        grasp_quaternion,
        closed=False,
    )
    # Re-read the object after approach in case it rolled while settling.
    banana = _movable_object_position(env)
    support_aligned_quaternion = torch.as_tensor(
        support_aligned_object_quaternion_wxyz(
            _movable_object_quaternion(env).numpy()
        ),
        dtype=torch.float32,
    )
    grasp_xyz, grasp_quaternion = apply_object_relative_grasp(
        banana,
        support_aligned_quaternion,
        grasp_offset_object,
        object_to_grasp_quat,
    )
    pregrasp_decision = checkpoint_callback(
        obs,
        {
            "reason": "recovery_pregrasp_visual_gate",
            "phase": "recovery_pregrasp",
            "iteration": len(segments),
            "target_error_m": float(
                torch.linalg.vector_norm(grasp_xyz - _eef_position(env))
            ),
            "orientation_error_deg": float(
                torch.linalg.vector_norm(
                    quaternion_error_axis_angle_wxyz(
                        grasp_quaternion, _eef_quaternion(env)
                    )
                )
                * 180.0
                / np.pi
            ),
            "local_safety": {
                "safe": True,
                "reasons": [],
                "grasp_translation_drift_m": None,
                "object_lift_m": float(banana[2]) - initial_object_z,
                "rgbd_clearance_m": None,
            },
        },
    )
    report["pregrasp_coach_decision"] = pregrasp_decision
    if pregrasp_decision.get("decision") != "execute":
        raise RuntimeError(f"Gemini refused recovery pregrasp: {pregrasp_decision}")
    move_segment(
        "recovery_descend",
        grasp_xyz,
        grasp_quaternion,
        closed=False,
    )
    obs, terminal = _hold_joint_action(
        env, obs, command, 35, gripper_closed=True
    )
    command[0, 7] = 1.0
    if terminal:
        raise RuntimeError("Environment terminated while closing recovery grasp")

    # Lift from the fresh measured pose so object motion during finger closure
    # does not become an open-loop offset.
    recovery_lift_target = _eef_position(env) + torch.tensor(
        [0.0, 0.0, args_cli.lift_clearance], dtype=torch.float32
    )
    move_segment(
        "recovery_lift",
        recovery_lift_target,
        _eef_quaternion(env),
        closed=True,
    )
    object_regrasped = _movable_object_position(env)
    lift = float(object_regrasped[2]) - initial_object_z
    if lift < max(0.05, args_cli.minimum_transport_lift):
        raise RuntimeError(
            f"Recovery regrasp failed physical lift verification: {lift:.4f} m"
        )
    new_offset = _eef_position(env) - object_regrasped
    new_quaternion = _eef_quaternion(env)
    decision = checkpoint_callback(
        obs,
        {
            "reason": "recovery_regrasp_verified",
            "phase": "above_plate_recovery",
            "iteration": len(segments),
            "target_error_m": 0.0,
            "orientation_error_deg": 0.0,
            "local_safety": {
                "safe": True,
                "reasons": [],
                "grasp_translation_drift_m": 0.0,
                "object_lift_m": lift,
                "rgbd_clearance_m": None,
            },
        },
    )
    report["regrasp_coach_decision"] = decision
    if decision.get("decision") != "execute":
        raise RuntimeError(
            f"Gemini did not approve transport after verified regrasp: {decision}"
        )
    report["completed"] = True
    report["outcome"] = "set_down_reacquired_regrasped_relatched"
    report["new_carry_offset_m"] = new_offset.tolist()
    report["object_lift_after_regrasp_m"] = lift
    return obs, terminal, command, new_offset, new_quaternion, report


def _residual_center_over_plate(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    initial_object_z: float,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Move the grasped object toward target center with bounded local DLS IK."""
    robot = env.scene["robot"]
    arm_joint_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    body_idx = robot.data.body_names.index("base_link")
    jacobi_body_idx = body_idx - 1 if robot.is_fixed_base else body_idx
    jacobi_joint_ids = [index + robot.num_base_dofs for index in arm_joint_ids]
    command = last_action.clone()
    terminal = False
    iterations: list[dict[str, Any]] = []
    contact_detected = False
    support_contact_event: dict[str, Any] | None = None
    object_start = _movable_object_position(env)
    plate_start = _target_receptacle_position(env)
    error_start = float(torch.linalg.vector_norm(plate_start[:2] - object_start[:2]))
    height_start = float(object_start[2] - plate_start[2])
    previous_error = error_start
    previous_height_error = abs(args_cli.release_height - height_start)
    support_monitor = SupportContactMonitor(
        object_initial_z=initial_object_z,
        set_down_clearance_m=args_cli.recovery_set_down_clearance,
        require_eef_stall=False,
    )

    for iteration in range(args_cli.center_max_iterations):
        banana = _movable_object_position(env)
        plate = _target_receptacle_position(env)
        error_xy = (plate[:2] - banana[:2]).to(env.device)
        error_norm = float(torch.linalg.vector_norm(error_xy))
        height_above_plate = float(banana[2] - plate[2])
        height_error = args_cli.release_height - height_above_plate
        eef_z_before = float(_eef_position(env)[2])
        if (
            error_norm <= args_cli.center_tolerance
            and abs(height_error) <= args_cli.release_height_tolerance
        ):
            break
        if (
            error_norm <= 0.12
            and 0.0 <= height_above_plate <= args_cli.plate_contact_height
        ):
            contact_detected = True
            print(
                f"[center] plate contact proxy active at xy={error_norm:.4f}m "
                f"height={height_above_plate:.4f}m; stopping before release",
                flush=True,
            )
            break
        if iteration == 0 and float(banana[2]) - initial_object_z < 0.05:
            raise RuntimeError("Residual centering refused: object is no longer securely lifted")
        if height_above_plate < 0.015:
            raise RuntimeError("Residual centering refused: object is already at target contact height")

        # Center laterally at the safe hover height before lowering. Combining
        # a near-contact XY sweep with a downward command can lever the banana
        # out of the gripper or collide it with the plate rim.
        if error_norm > args_cli.center_tolerance:
            correction_mode = "xy"
            xy_step = bounded_xy_step(error_xy, args_cli.center_max_step)
            z_step = torch.tensor(0.0, dtype=torch.float32, device=env.device)
        else:
            correction_mode = "z"
            xy_step = torch.zeros(2, dtype=torch.float32, device=env.device)
            z_step = bounded_scalar_step(
                torch.tensor(height_error, dtype=torch.float32, device=env.device),
                args_cli.center_max_z_step,
            )
        desired_twist_w = torch.zeros(6, dtype=torch.float32, device=env.device)
        # The task/robot roots share world orientation in this fixed-base scene,
        # so the measured target-minus-object vector is already on Jacobian axes.
        desired_twist_w[:2] = xy_step
        desired_twist_w[2] = z_step
        jacobian_w = robot.data.body_link_jacobian_w.torch[
            0, jacobi_body_idx
        ][:, jacobi_joint_ids]
        delta_joint = damped_least_squares_delta(
            jacobian_w,
            desired_twist_w,
            args_cli.center_damping,
            args_cli.center_max_joint_step,
        )
        joint_pos = robot.data.joint_pos.torch[0, arm_joint_ids]
        joint_limits = robot.data.soft_joint_pos_limits.torch[0, arm_joint_ids]
        joint_target = torch.clamp(
            joint_pos + delta_joint,
            min=joint_limits[:, 0] + 1.0e-3,
            max=joint_limits[:, 1] - 1.0e-3,
        )
        command[0, :7] = joint_target
        command[0, 7] = 1.0
        obs, terminal = _hold_joint_action(
            env,
            obs,
            command,
            args_cli.center_settle_steps,
            gripper_closed=True,
        )
        object_after = _movable_object_position(env)
        plate_after = _target_receptacle_position(env)
        error_after = float(torch.linalg.vector_norm(plate_after[:2] - object_after[:2]))
        lifted_after = float(object_after[2]) - initial_object_z
        height_after = float(object_after[2] - plate_after[2])
        height_error_after = abs(args_cli.release_height - height_after)
        if correction_mode == "z" and error_after <= args_cli.center_tolerance:
            support_contact_event = support_monitor.update(
                object_z=float(object_after[2]),
                eef_z=float(_eef_position(env)[2]),
                target_eef_z=eef_z_before + height_error,
                target_tolerance_m=args_cli.release_height_tolerance,
            )
        contact_after = (
            error_after <= 0.12
            and 0.0 <= height_after <= args_cli.plate_contact_height
        )
        iteration_record = {
            "iteration": iteration + 1,
            "correction_mode": correction_mode,
            "xy_error_before_m": error_norm,
            "requested_xy_step_m": xy_step.detach().cpu().tolist(),
            "height_above_plate_before_m": height_above_plate,
            "requested_z_step_m": float(z_step),
            "max_abs_joint_step_rad": float(torch.max(torch.abs(delta_joint))),
            "xy_error_after_m": error_after,
            "height_above_plate_after_m": height_after,
            "object_lift_after_m": lifted_after,
            "object_target_contact_proxy": contact_after,
            "support_contact_event": support_contact_event,
            "terminal": terminal,
        }
        iterations.append(iteration_record)
        print(
            f"[center] mode={correction_mode} iteration={iteration + 1} "
            f"xy={error_norm:.4f}→{error_after:.4f}m "
            f"height={height_above_plate:.4f}→{height_after:.4f}m "
            f"step_xy={xy_step.detach().cpu().tolist()} step_z={float(z_step):.4f}m "
            f"max_dq={iteration_record['max_abs_joint_step_rad']:.4f}rad",
            flush=True,
        )
        if terminal:
            break
        if contact_after:
            contact_detected = True
            print(
                "[center] plate contact proxy became active; "
                "suppressing further Cartesian correction until release",
                flush=True,
            )
            break
        if support_contact_event is not None:
            contact_detected = True
            print(
                "[center] aligned object stopped descending inside the support "
                "envelope; suppressing further correction before release",
                flush=True,
            )
            break
        if height_after < 0.015:
            raise RuntimeError("Residual centering pushed the object below safe target clearance")
        if lifted_after < 0.010:
            raise RuntimeError(
                "Residual centering detected grasp loss before release: "
                f"object lift fell to {lifted_after:.4f} m"
            )
        # Fail closed if a commanded correction materially moves away from the
        # plate. Tiny contact-induced fluctuations are allowed.
        if correction_mode == "xy" and error_after > previous_error + 0.005:
            raise RuntimeError(
                f"Residual centering diverged: XY error {previous_error:.4f}→{error_after:.4f} m"
            )
        if correction_mode == "z" and height_error_after > previous_height_error + 0.005:
            raise RuntimeError(
                "Residual centering diverged vertically: release-height error "
                f"{previous_height_error:.4f}→{height_error_after:.4f} m"
            )
        previous_error = error_after
        previous_height_error = height_error_after

    object_final = _movable_object_position(env)
    plate_final = _target_receptacle_position(env)
    error_final = float(torch.linalg.vector_norm(plate_final[:2] - object_final[:2]))
    height_final = float(object_final[2] - plate_final[2])
    report = {
        "enabled": True,
        "xy_error_before_m": error_start,
        "xy_error_after_m": error_final,
        "target_tolerance_m": args_cli.center_tolerance,
        "height_above_plate_before_m": height_start,
        "height_above_plate_after_m": height_final,
        "target_release_height_m": args_cli.release_height,
        "release_height_tolerance_m": args_cli.release_height_tolerance,
        "plate_contact_height_m": args_cli.plate_contact_height,
        "object_target_contact_proxy": contact_detected,
        "support_contact_event": support_contact_event,
        "converged": (
            contact_detected
            or (
                error_final <= args_cli.center_tolerance
                and abs(height_final - args_cli.release_height)
                <= args_cli.release_height_tolerance
            )
        ),
        "iterations": iterations,
    }
    return obs, terminal, command, report


def _retreat_after_release(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Raise the open gripper and verify that the released object stays behind."""
    if bool(float(last_action[0, 7].detach().cpu()) > 0.5):
        raise RuntimeError(
            "Release retreat requires a separately executed and observed "
            "actuator disengagement"
        )
    robot = env.scene["robot"]
    arm_joint_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    body_idx = robot.data.body_names.index("base_link")
    jacobi_body_idx = body_idx - 1 if robot.is_fixed_base else body_idx
    jacobi_joint_ids = [index + robot.num_base_dofs for index in arm_joint_ids]
    command = last_action.clone()
    terminal = False
    iterations: list[dict[str, Any]] = []
    eef_start = _eef_position(env)
    object_start = _movable_object_position(env)
    start_separation = float(torch.linalg.vector_norm(eef_start - object_start))
    target_z = float(eef_start[2]) + args_cli.retreat_distance
    previous_remaining = args_cli.retreat_distance

    for iteration in range(args_cli.retreat_max_iterations):
        eef_before = _eef_position(env)
        remaining = target_z - float(eef_before[2])
        if remaining <= 0.010:
            break
        z_step = bounded_scalar_step(
            torch.tensor(remaining, dtype=torch.float32, device=env.device),
            args_cli.retreat_max_step,
        )
        desired_twist_w = torch.zeros(6, dtype=torch.float32, device=env.device)
        desired_twist_w[2] = z_step
        jacobian_w = robot.data.body_link_jacobian_w.torch[
            0, jacobi_body_idx
        ][:, jacobi_joint_ids]
        delta_joint = damped_least_squares_delta(
            jacobian_w,
            desired_twist_w,
            args_cli.center_damping,
            args_cli.center_max_joint_step,
        )
        joint_pos = robot.data.joint_pos.torch[0, arm_joint_ids]
        joint_limits = robot.data.soft_joint_pos_limits.torch[0, arm_joint_ids]
        command[0, :7] = torch.clamp(
            joint_pos + delta_joint,
            min=joint_limits[:, 0] + 1.0e-3,
            max=joint_limits[:, 1] - 1.0e-3,
        )
        obs, terminal = _hold_joint_action(
            env,
            obs,
            command,
            args_cli.retreat_settle_steps,
            gripper_closed=False,
        )
        eef_after = _eef_position(env)
        remaining_after = max(0.0, target_z - float(eef_after[2]))
        record = {
            "iteration": iteration + 1,
            "eef_z_before_m": float(eef_before[2]),
            "requested_z_step_m": float(z_step),
            "max_abs_joint_step_rad": float(torch.max(torch.abs(delta_joint))),
            "eef_z_after_m": float(eef_after[2]),
            "remaining_z_m": remaining_after,
            "terminal": terminal,
        }
        iterations.append(record)
        print(
            f"[retreat] iteration={iteration + 1} "
            f"eef_z={float(eef_before[2]):.4f}→{float(eef_after[2]):.4f}m "
            f"remaining={remaining_after:.4f}m "
            f"max_dq={record['max_abs_joint_step_rad']:.4f}rad",
            flush=True,
        )
        if terminal:
            break
        if remaining_after > previous_remaining + 0.004:
            raise RuntimeError(
                "Release retreat diverged: remaining vertical distance "
                f"{previous_remaining:.4f}→{remaining_after:.4f} m"
            )
        previous_remaining = remaining_after

    # Let the object settle with the gripper open and clear before scoring it.
    obs, settle_terminal = _hold_joint_action(
        env,
        obs,
        command,
        args_cli.retreat_settle_steps * 2,
        gripper_closed=False,
    )
    terminal = terminal or settle_terminal
    eef_final = _eef_position(env)
    object_final = _movable_object_position(env)
    plate_final = _target_receptacle_position(env)
    final_separation = float(torch.linalg.vector_norm(eef_final - object_final))
    retreat_z = float(eef_final[2] - eef_start[2])
    object_motion = float(torch.linalg.vector_norm(object_final - object_start))
    object_target_xy_error = float(
        torch.linalg.vector_norm(object_final[:2] - plate_final[:2])
    )
    object_height_above_target = float(object_final[2] - plate_final[2])
    on_plate = (
        object_target_xy_error <= 0.12
        and 0.0 <= object_height_above_target <= 0.20
    )
    state_final = _state(env, float(object_start[2]))
    gripper_open = state_final["gripper_closed_fraction"] <= 0.10
    detachment = assess_release_detachment(
        controlled_start_xyz=eef_start.numpy(),
        controlled_final_xyz=eef_final.numpy(),
        subject_start_xyz=object_start.numpy(),
        subject_final_xyz=object_final.numpy(),
        released=gripper_open,
        goal_relation_holds=on_plate,
        terminal=terminal,
        minimum_retreat_m=max(0.040, args_cli.retreat_distance - 0.020),
    )
    converged = bool(detachment["converged"])
    report = {
        "enabled": True,
        "requested_retreat_m": args_cli.retreat_distance,
        "eef_start_xyz": eef_start.tolist(),
        "eef_final_xyz": eef_final.tolist(),
        "eef_retreat_z_m": retreat_z,
        "object_start_xyz": object_start.tolist(),
        "object_final_xyz": object_final.tolist(),
        "object_motion_during_retreat_m": object_motion,
        "object_target_xy_error_after_m": object_target_xy_error,
        "object_height_above_target_after_m": object_height_above_target,
        "object_remained_on_target": on_plate,
        "eef_object_separation_before_m": start_separation,
        "eef_object_separation_after_m": final_separation,
        "gripper_closed_fraction_after": state_final["gripper_closed_fraction"],
        "detachment_evidence": detachment,
        "converged": converged,
        "iterations": iterations,
    }
    return obs, terminal, command, report


def main() -> int:
    global ACTIVE_EPISODE_RECORDER, ACTIVE_SENSOR_MONITOR, ACTIVE_SENSOR_SAMPLE_INDEX
    global ACTIVE_ROS2_SENSOR_INGRESS
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY in the environment")
    world_effect_only_mode = bool(
        args_cli.shadow_plan_only or args_cli.guarded_world_effect_execution
    )
    if world_effect_only_mode and args_cli.disable_world_intent_shadow:
        raise ValueError(
            "world-effect-only modes cannot be combined with "
            "--disable-world-intent-shadow"
        )
    if args_cli.shadow_plan_only and args_cli.guarded_world_effect_execution:
        raise ValueError(
            "--shadow-plan-only and --guarded-world-effect-execution are "
            "mutually exclusive"
        )
    if args_cli.world_effect_runtime_lease_duration_s <= 0:
        raise ValueError("world-effect-runtime-lease-duration-s must be positive")
    world_effect_sequence_budget = WorldEffectSequenceBudget(
        args_cli.world_effect_max_operations
    )
    if args_cli.world_effect_dispatch_evidence_max_age_s <= 0:
        raise ValueError(
            "world-effect-dispatch-evidence-max-age-s must be positive"
        )
    if args_cli.world_effect_dispatch_permit_lifetime_s <= 0:
        raise ValueError(
            "world-effect-dispatch-permit-lifetime-s must be positive"
        )
    if args_cli.world_effect_preflight_settle_steps < 0:
        raise ValueError("world-effect-preflight-settle-steps must be non-negative")
    if (
        args_cli.guarded_world_effect_execution
        and args_cli.world_effect_preflight_settle_steps == 0
    ):
        raise ValueError(
            "guarded world-effect execution requires at least one preflight "
            "physics settle step"
        )
    if not args_cli.disable_adaptive_ik:
        positive_adaptive_values = {
            "adaptive_tolerance": args_cli.adaptive_tolerance,
            "adaptive_max_step": args_cli.adaptive_max_step,
            "adaptive_max_iterations": args_cli.adaptive_max_iterations,
            "adaptive_settle_steps": args_cli.adaptive_settle_steps,
            "adaptive_max_joint_step": args_cli.adaptive_max_joint_step,
            "adaptive_damping": args_cli.adaptive_damping,
            "adaptive_orientation_tolerance_deg": args_cli.adaptive_orientation_tolerance_deg,
            "adaptive_max_angle_step_deg": args_cli.adaptive_max_angle_step_deg,
            "coach_interval_iterations": args_cli.coach_interval_iterations,
            "motion_checkpoint_replans": args_cli.motion_checkpoint_replans,
            "maximum_model_target_correction": (
                args_cli.maximum_model_target_correction
            ),
            "maximum_grasp_drift": args_cli.maximum_grasp_drift,
            "minimum_transport_lift": args_cli.minimum_transport_lift,
            "max_lift_recovery_operations": (
                args_cli.max_lift_recovery_operations
            ),
            "max_failed_grasp_attempts": (
                args_cli.max_failed_grasp_attempts
            ),
            "recovery_hold_steps": args_cli.recovery_hold_steps,
            "recovery_stability_drift": args_cli.recovery_stability_drift,
            "recovery_set_down_clearance": args_cli.recovery_set_down_clearance,
            "approach_clearance": args_cli.approach_clearance,
            "lift_clearance": args_cli.lift_clearance,
            "plate_hover_height": args_cli.plate_hover_height,
        }
        invalid = {
            name: value for name, value in positive_adaptive_values.items() if value <= 0
        }
        if invalid:
            raise ValueError(f"Adaptive-IK parameters must be positive: {invalid}")
        if args_cli.max_transport_recoveries < 0:
            raise ValueError("max-transport-recoveries must be non-negative")
    relocation_values = (
        *args_cli.movable_object_offset,
        *args_cli.plate_offset,
        args_cli.movable_object_yaw_deg,
        *(tuple() if args_cli.light_intensity is None else (args_cli.light_intensity,)),
    )
    if not all(np.isfinite(value) for value in relocation_values):
        raise ValueError("Object relocation offsets must be finite")
    if args_cli.light_intensity is not None and args_cli.light_intensity <= 0:
        raise ValueError("light-intensity must be positive")
    if not args_cli.disable_residual_centering:
        positive_centering_values = {
            "center_tolerance": args_cli.center_tolerance,
            "center_max_step": args_cli.center_max_step,
            "center_max_z_step": args_cli.center_max_z_step,
            "release_height": args_cli.release_height,
            "release_height_tolerance": args_cli.release_height_tolerance,
            "plate_contact_height": args_cli.plate_contact_height,
            "center_max_iterations": args_cli.center_max_iterations,
            "center_settle_steps": args_cli.center_settle_steps,
            "center_max_joint_step": args_cli.center_max_joint_step,
            "center_damping": args_cli.center_damping,
        }
        invalid = {name: value for name, value in positive_centering_values.items() if value <= 0}
        if invalid:
            raise ValueError(f"Residual-centering parameters must be positive: {invalid}")
    if not args_cli.disable_release_retreat:
        positive_retreat_values = {
            "retreat_distance": args_cli.retreat_distance,
            "retreat_max_step": args_cli.retreat_max_step,
            "retreat_max_iterations": args_cli.retreat_max_iterations,
            "retreat_settle_steps": args_cli.retreat_settle_steps,
        }
        invalid = {name: value for name, value in positive_retreat_values.items() if value <= 0}
        if invalid:
            raise ValueError(f"Release-retreat parameters must be positive: {invalid}")
    if args_cli.episode_index < -1:
        raise ValueError("episode-index must be -1 or a non-negative integer")
    if args_cli.record_video_scale <= 0:
        raise ValueError("record-video-scale must be positive")
    if args_cli.model_max_retries < 0 or args_cli.model_retry_backoff < 0:
        raise ValueError("model retry count and backoff must be non-negative")
    if args_cli.world_goal_revision_attempts < 0:
        raise ValueError("world-goal-revision-attempts must be non-negative")
    if args_cli.world_scope_revision_attempts < 0:
        raise ValueError("world-scope-revision-attempts must be non-negative")
    if not 0.0 <= args_cli.minimum_contact_coverage <= 1.0:
        raise ValueError("minimum-contact-coverage must be in [0, 1]")
    if args_cli.minimum_touch_samples < 1:
        raise ValueError("minimum-touch-samples must be positive")
    if args_cli.maximum_actuator_interaction_distance <= 0:
        raise ValueError("maximum-actuator-interaction-distance must be positive")
    if args_cli.failed_grasp_retry_minimum_translation <= 0:
        raise ValueError(
            "failed-grasp-retry-minimum-translation must be positive"
        )
    if args_cli.failed_grasp_retry_minimum_orientation_deg <= 0:
        raise ValueError(
            "failed-grasp-retry-minimum-orientation-deg must be positive"
        )
    if not 0.0 < args_cli.maximum_model_rotation_correction_deg <= 180.0:
        raise ValueError(
            "maximum-model-rotation-correction-deg must be within (0, 180]"
        )
    if not 0.0 < args_cli.maximum_pregrasp_axis_error_deg <= 90.0:
        raise ValueError(
            "maximum-pregrasp-axis-error-deg must be within (0, 90]"
        )
    actuator_feedback_policy = ActuatorFeedbackEventPolicy(
        minimum_position_change=args_cli.actuator_feedback_position_change,
        minimum_force_change_n=args_cli.actuator_feedback_force_change,
    )

    demo_path: Path | None = None
    recorded_actions = np.empty((0, 8), dtype=np.float32)
    joint_states = np.empty((0, 8), dtype=np.float32)
    boundaries = np.empty((0,), dtype=np.int64)
    phase_names = ["approach_object", "descend", "grasp", "lift", "above_plate"]
    if not world_effect_only_mode:
        demo_path = args_cli.demo.expanduser().resolve()
        if not demo_path.is_file():
            raise FileNotFoundError(
                f"Successful local motion primitive not found: {demo_path}"
            )
        with h5py.File(demo_path, "r") as source:
            demo = source["data/demo_0"]
            if not bool(demo.attrs.get("success", False)):
                raise RuntimeError(
                    "Refusing to execute a demonstration not marked successful: "
                    f"{demo_path}"
                )
            recorded_actions = np.asarray(demo["actions"], dtype=np.float32)
            joint_states = np.asarray(
                demo["states/articulation/robot/joint_position"],
                dtype=np.float32,
            )
        change_points = np.flatnonzero(
            np.max(np.abs(np.diff(recorded_actions, axis=0)), axis=1) > 1.0e-5
        ) + 1
        boundaries = np.concatenate(
            ([0], change_points, [len(recorded_actions)])
        )
        if len(boundaries) - 1 != len(phase_names):
            raise RuntimeError(
                f"Expected {len(phase_names)} semantic trajectory segments, "
                f"got {len(boundaries) - 1}"
            )

    args_cli.artifact_dir.mkdir(parents=True, exist_ok=True)
    training_episode_dir = (
        args_cli.training_episode_dir
        if args_cli.training_episode_dir is not None
        else args_cli.artifact_dir / "training_episodes"
    ).expanduser().resolve()
    critic_memory_path = args_cli.artifact_dir / "critic_guidance.json"
    critic_memory = (
        {"source_model": None, "lessons": []}
        if args_cli.disable_critic_guidance
        else _load_critic_guidance(critic_memory_path, args_cli.task)
    )
    robolab.constants.set_output_dir(str(args_cli.artifact_dir / "robolab_output"))
    # Use an unfiltered two-finger sensor below instead of RoboLab's brittle
    # legacy object-pair filter graph.
    robolab.constants.ENABLE_SUBTASK_PROGRESS_CHECKING = False
    robolab.constants.RECORD_IMAGE_DATA = False
    robolab.constants.VERBOSE = False

    print("=" * 78)
    print("VISIBLE TEST SUITE: Gemini Robotics ER 2 + RoboLab DROID/Franka")
    print(f"Model: {MODEL_ID}")
    print(f"Task:  {args_cli.task}")
    print(
        "Scene roles: "
        f"movable_object={SCENE_ROLES.movable_object_asset!r} "
        f"({SCENE_ROLES.movable_object_label}), "
        f"target_receptacle={SCENE_ROLES.target_receptacle_asset!r} "
        f"({SCENE_ROLES.target_receptacle_label})"
    )
    print(f"GUI:   {'off (headless)' if args_cli.headless else 'on'}")
    print(
        "World-intent shadow: "
        + ("disabled" if args_cli.disable_world_intent_shadow else args_cli.instruction)
    )
    print(
        "World-goal-graph shadow: "
        + (
            "disabled"
            if args_cli.disable_world_intent_shadow
            else "full observed scene inventory; no motion authority"
        )
    )
    print(
        "Execution boundary: "
        + (
            "GUARDED WORLD-EFFECT EXECUTION; bounded fresh-replan sequence "
            f"(max {args_cli.world_effect_max_operations} single-use invocations)"
            if args_cli.guarded_world_effect_execution
            else (
                "SHADOW PLAN ONLY; legacy demonstration/providers/motion disabled"
                if args_cli.shadow_plan_only
                else "full task runtime"
            )
        )
    )
    print(
        "Local motion primitive: not loaded"
        if world_effect_only_mode
        else f"Local motion primitive: {demo_path} ({len(recorded_actions)} steps)"
    )
    runtime_detail: Callable[..., None] = (
        (lambda *unused_args, **unused_kwargs: None)
        if args_cli.shadow_plan_only
        else print
    )
    if world_effect_only_mode:
        print("Legacy runtime details: skipped")
    runtime_detail(
        "Control cadence: one observation-bound model tool per runtime operation; "
        + (
            f"periodic + event checkpoints every {args_cli.coach_interval_iterations} "
            "local IK chunks"
            if args_cli.periodic_motion_observations
            else "event/completion checkpoints only during each multi-step IK lease"
        )
    )
    runtime_detail(
        "Motion executor: runtime-registered bounded DLS IK; model-configurable "
        f"with target correction≤{args_cli.maximum_model_target_correction:.3f}m "
        "and rotation correction≤"
        f"{args_cli.maximum_model_rotation_correction_deg:.1f}deg"
    )
    runtime_detail(
        "Actuator executor: runtime-registered binary clamp; model-selectable "
        "engage/disengage/maintain with 8–120 settling steps"
    )
    runtime_detail(
        "Operation scheduler: fresh-observation routing between continued "
        "motion and actuator evaluation; no recorded gripper-state hints"
    )
    runtime_detail(
        "Post-actuation feedback: immediate Gemini reschedule when position "
        f"change≥{actuator_feedback_policy.minimum_position_change:.3f} and "
        "touch changes or force delta≥"
        f"{actuator_feedback_policy.minimum_force_change_n:.3f}N"
    )
    runtime_detail(
        "Live-pose adaptive IK: "
        + (
            "off (fixed demonstration replay)"
            if args_cli.disable_adaptive_ik
            else (
                f"on (tolerance={args_cli.adaptive_tolerance:.3f}m, "
                f"step≤{args_cli.adaptive_max_step:.3f}m, "
                f"iterations≤{args_cli.adaptive_max_iterations}, "
                f"object_offset={list(args_cli.movable_object_offset)}, "
                f"target_offset={list(args_cli.plate_offset)}, "
                f"object_yaw={args_cli.movable_object_yaw_deg:.1f}deg)"
            )
        )
    )
    runtime_detail(
        "Model-governed placement: "
        + (
            "off (baseline mode)"
            if args_cli.disable_residual_centering
            else (
                "on (fresh RGB-D/state motion tool; "
                f"measured XY tolerance={args_cli.center_tolerance:.3f}m, "
                f"release height={args_cli.release_height:.3f}m)"
            )
        )
    )
    runtime_detail(
        "Passive critic memory: "
        + (
            f"{len(critic_memory['lessons'])} lessons from {critic_memory['source_model']}"
            if critic_memory["lessons"]
            else "none"
        )
    )
    runtime_detail(
        "Post-release detachment check: "
        + (
            "off"
            if args_cli.disable_release_retreat
            else f"on (open-gripper retreat={args_cli.retreat_distance:.3f}m)"
        )
    )
    runtime_detail(
        "Training capture: "
        + (
            "disabled"
            if args_cli.disable_training_recording or world_effect_only_mode
            else f"successful Gemini completions only → {training_episode_dir}"
        )
    )
    runtime_detail(
        "ROS 2 sensor ingress: "
        + (
            "configured (subscriber availability checked after scene startup)"
            if args_cli.ros2_sensor_ingress
            else "disabled"
        )
    )
    print("=" * 78, flush=True)

    sim_version_path = Path(os.environ.get("ISAAC_SIM_ROOT", "")) / "VERSION"
    sim_version = (
        sim_version_path.read_text().strip()
        if sim_version_path.is_file()
        else "6.x runtime (VERSION path unavailable)"
    )
    sim6_ok = sim_version.startswith("6.")
    _test_line(1, "Isaac Sim 6 runtime", sim6_ok, sim_version)

    auto_register_droid_abs_ik_envs(
        task=args_cli.task,
        contact_sensors=False,
    )
    env_cfg = parse_env_cfg(
        args_cli.task, device="cuda:0", seed=0, num_envs=1, use_fabric=True
    )
    # RoboLab's explicit robot/object poses came from the Sim 5 (w, x, y, z)
    # configuration contract. This Sim 6 source build consumes spawn poses as
    # (x, y, z, w); convert only the legacy-authored fields at the boundary.
    env_cfg.scene.robot.init_state.rot = (0.0, 0.0, 0.0, 1.0)
    fixture_rot = env_cfg.scene.table_fixture.init_state.rot
    env_cfg.scene.table_fixture.init_state.rot = (
        fixture_rot[1], fixture_rot[2], fixture_rot[3], fixture_rot[0]
    )
    for asset_name in env_cfg.contact_object_list:
        asset_cfg = getattr(env_cfg.scene, asset_name)
        w, x, y, z = asset_cfg.init_state.rot
        asset_cfg.init_state.rot = (x, y, z, w)
    if not args_cli.disable_contact_telemetry:
        install_sim6_gripper_contact_sensor(env_cfg)
    if args_cli.randomize_background:
        from robolab.variations.backgrounds import find_background_files

        backgrounds = find_background_files()
        current_background = str(env_cfg.scene.dome_light.spawn.texture_file)
        backgrounds = [path for path in backgrounds if str(path) != current_background]
        if not backgrounds:
            raise FileNotFoundError("No non-default RoboLab HDRI backgrounds are available")
        env_cfg.scene.dome_light.spawn.texture_file = random.Random(
            args_cli.appearance_seed
        ).choice(backgrounds)
    if args_cli.light_intensity is not None:
        sphere_light = getattr(env_cfg.scene, "sphere_light", None)
        if sphere_light is None:
            raise RuntimeError("Requested light variation but scene has no sphere_light")
        sphere_light.spawn.intensity = args_cli.light_intensity
    if args_cli.rgbd_safety:
        for camera_name in ("over_shoulder_left_camera", "wrist_cam"):
            camera_cfg = getattr(env_cfg.scene, camera_name)
            if "depth" not in camera_cfg.data_types:
                camera_cfg.data_types = [*camera_cfg.data_types, "depth"]
        exterior_camera_cfg = env_cfg.scene.over_shoulder_left_camera
        if "instance_id_segmentation_fast" not in exterior_camera_cfg.data_types:
            exterior_camera_cfg.data_types = [
                *exterior_camera_cfg.data_types,
                "instance_id_segmentation_fast",
            ]
        exterior_camera_cfg.renderer_cfg.colorize_instance_id_segmentation = False
        env_cfg.scene.lazy_sensor_update = False
    # Sim 6's absolute-IK bridge currently resolves the Robotiq control body
    # incorrectly. Replay the demonstrated arm states through the stable joint
    # controller while ER 2 performs fresh visual gates at semantic boundaries.
    env_cfg.actions = DroidJointPositionActionCfg()
    env_cfg.terminations = None
    env_cfg.subtasks = None
    # The environment task supplies the scene template only. The model-facing
    # goal comes from the current human instruction and semantic scene roles.
    env_cfg.instruction = args_cli.instruction
    # RoboLab's EE-state recorder still consumes the removed Sim 5 tensor API
    # and is not needed for this live control/visualization test.
    env_cfg.recorders = None
    env, _ = create_env(env_cfg, use_fabric=True, policy="gemini-er2")
    obs, _ = env.reset()
    SCENE_ROLES.validate_scene(env.scene)
    ACTIVE_SENSOR_MONITOR = SensorCaptureBuffer()
    ACTIVE_SENSOR_SAMPLE_INDEX = 0
    contact_sensor_info = contact_sensor_runtime_info(env)
    print(f"[contact-sensor] {contact_sensor_info}", flush=True)
    baseline_movable_object_xyz = _movable_object_position(env)
    baseline_object_quat = _movable_object_quaternion(env)
    grasp_offset_object, object_to_grasp_quat = derive_object_relative_grasp(
        baseline_movable_object_xyz,
        baseline_object_quat,
        baseline_movable_object_xyz + DEFAULT_OBJECT_GRASP_OFFSET,
        DEFAULT_DOWNWARD_GRASP_QUAT,
    )
    # Do not restore Sim 5 rigid-body snapshots into Sim 6. Their contact state
    # can begin interpenetrating and eject task objects on the first step.
    _transform_asset_pose(
        env,
        SCENE_ROLES.movable_object_asset,
        tuple(args_cli.movable_object_offset),
        yaw_degrees=args_cli.movable_object_yaw_deg,
    )
    _transform_asset_pose(
        env, SCENE_ROLES.target_receptacle_asset, tuple(args_cli.plate_offset)
    )
    _set_sim6_camera_views(env)
    env.sim.render()
    obs = env.observation_manager.compute()
    coach = GeminiRoboticsER2(api_key, args_cli.timeout)
    motion_tool_provider: GeminiProvider | None = None
    trackable_object_ids = tuple(
        object_id
        for object_id in env_cfg.contact_object_list
        if isinstance(object_id, str) and object_id
    )
    motion_executor_registry: MotionExecutorRegistry | None = None
    actuator_executor_registry: ActuatorExecutorRegistry | None = None
    if not args_cli.shadow_plan_only or args_cli.guarded_world_effect_execution:
        motion_tool_provider = GeminiProvider(api_key, MODEL_ID)
        motion_executor_registry = _local_dls_executor_registry(
            trackable_object_ids
        )
        actuator_executor_registry = _local_binary_actuator_registry()
    runtime_effect_tools: list[RuntimeToolCapability] = [
        RuntimeToolCapability(
            tool_id="sensor.rgbd_scene_geometry",
            tool_family="sensor",
            capability_tags=("scene.geometry.rgbd",),
            activation_status="active",
            source="live_synchronized_rgbd_instance_geometry",
        )
    ]
    if motion_executor_registry is None:
        runtime_effect_tools.append(
            RuntimeToolCapability(
                tool_id="factory.spatial_pose_target",
                tool_family="motion",
                capability_tags=SPATIAL_MOTION_CAPABILITY_TAGS,
                activation_status="factory_available",
                source="runtime_executor_factory_catalog",
            )
        )
    else:
        runtime_effect_tools.extend(
            RuntimeToolCapability(
                tool_id=spec.executor_id,
                tool_family="motion",
                capability_tags=spec.capability_tags,
                activation_status="active",
                source="active_motion_executor_registry",
                tool_advertisement=spec.advertisement(),
            )
            for spec in motion_executor_registry.specs()
        )
    if actuator_executor_registry is None:
        runtime_effect_tools.append(
            RuntimeToolCapability(
                tool_id="factory.reversible_entity_attachment",
                tool_family="actuator",
                capability_tags=REVERSIBLE_ATTACHMENT_CAPABILITY_TAGS,
                activation_status="factory_available",
                source="runtime_executor_factory_catalog",
            )
        )
    else:
        runtime_effect_tools.extend(
            RuntimeToolCapability(
                tool_id=spec.executor_id,
                tool_family="actuator",
                capability_tags=spec.capability_tags,
                activation_status="active",
                source="active_actuator_executor_registry",
                tool_advertisement=spec.advertisement(),
            )
            for spec in actuator_executor_registry.specs()
        )
    world_effect_provider_registry = default_world_effect_provider_registry()
    inside_effect_provider_assessment = world_effect_provider_registry.assess(
        "world_relation.realize_inside",
        runtime_effect_tools,
    )
    world_predicate_evaluator_registry = (
        rgbd_world_predicate_evaluator_registry()
    )
    world_predicate_evaluator_advertisement = (
        world_predicate_evaluator_registry.advertisement()
    )
    world_capability_registry = shadow_world_capability_registry(
        effect_provider_assessment=inside_effect_provider_assessment.to_dict()
    )
    world_capability_advertisement = world_capability_registry.advertisement()
    initial_object = _movable_object_position(env)
    initial_object_z = float(initial_object[2])
    initial_eef = _eef_position(env)
    robot_root_w = env.scene["robot"].data.root_pos_w
    robot_root_w = getattr(robot_root_w, "torch", robot_root_w)[0].detach().cpu()
    spawn_ok = bool(initial_eef[2] > 0.15 and initial_eef[2] < 1.50)
    print(
        f"[spawn] robot_root={robot_root_w.tolist()} eef_root={initial_eef.tolist()} "
        f"tabletop_z≈0.0 valid={spawn_ok}",
        flush=True,
    )
    if not spawn_ok:
        raise RuntimeError("Robot/table spawn validation failed before policy execution")

    tests: dict[str, bool] = {"runtime": sim6_ok}
    digests: list[str] = []
    eef_trace: list[list[float]] = [_eef_position(env).tolist()]
    model_calls = 0
    terminal = False
    episode_trace: dict[str, Any] = {
        "schema_version": 4,
        "execution_mode": (
            "guarded_world_effect_execution"
            if args_cli.guarded_world_effect_execution
            else (
                "shadow_plan_only"
                if args_cli.shadow_plan_only
                else "full_task_runtime"
            )
        ),
        "execution_boundary": {
            "demonstration_loaded": demo_path is not None,
            "motion_provider_created": motion_tool_provider is not None,
            "motion_executor_registry_created": (
                motion_executor_registry is not None
            ),
            "actuator_executor_registry_created": (
                actuator_executor_registry is not None
            ),
            "motion_authority": False if world_effect_only_mode else None,
        },
        "task": args_cli.task,
        "instruction": args_cli.instruction,
        "scene_roles": SCENE_ROLES.to_dict(),
        "coach_model": MODEL_ID,
        "critic_memory_applied": critic_memory,
        "sim_version": sim_version,
        "physics_steps_are_local": True,
        "motion_executor": (
            None
            if world_effect_only_mode
            else (
                "fixed_demonstration_replay"
                if args_cli.disable_adaptive_ik
                else "live_pose_bounded_dls_ik"
            )
        ),
        "requested_relocation_xy_m": {
            "movable_object": list(args_cli.movable_object_offset),
            "target_receptacle": list(args_cli.plate_offset),
        },
        "requested_movable_object_yaw_deg": args_cli.movable_object_yaw_deg,
        "appearance": {
            "randomized_background": args_cli.randomize_background,
            "appearance_seed": args_cli.appearance_seed,
            "sphere_light_intensity": (
                args_cli.light_intensity
                if args_cli.light_intensity is not None
                else float(env_cfg.scene.sphere_light.spawn.intensity)
            ),
            "background_texture": str(env_cfg.scene.dome_light.spawn.texture_file),
        },
        "rgbd_safety": {
            "enabled": args_cli.rgbd_safety,
            "motion_checkpoint_depth_panel": args_cli.rgbd_safety,
            "tracked_object_translation_source": (
                "ros2_tracked_object_status_when_published_else_"
                "sim_privileged_relative_pose"
            ),
            "tracked_object_orientation_source": (
                "ros2_tracked_object_status_when_published_else_"
                "rgbd_instance_depth_major_axis"
                if args_cli.rgbd_safety
                else None
            ),
            "tracked_object_rgbd_axis_adapter_implemented": True,
        },
        "checkpoint_recovery": {
            "rejected_tool_replan_limit": args_cli.motion_checkpoint_replans,
            "preserves_local_correction_limit_m": (
                args_cli.maximum_model_target_correction
            ),
            "preserves_local_rotation_limit_deg": (
                args_cli.maximum_model_rotation_correction_deg
            ),
            "stable_grasp_clearance_recovery_is_model_selected": True,
        },
        "ros2_sensor_ingress": {
            "enabled": args_cli.ros2_sensor_ingress,
            "available": None,
            "status": "pending" if args_cli.ros2_sensor_ingress else "disabled",
        },
        "contact_sensor": contact_sensor_info,
        "world_intent_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "contract_version": WORLD_INTENT_SCHEMA_VERSION,
            "motion_authority": False,
            "authority_scope": [],
            "instruction": args_cli.instruction,
        },
        "world_goal_graph_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "contract_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
            "inventory_contract_version": (
                SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION
            ),
            "motion_authority": False,
            "authority_scope": [],
            "instruction": args_cli.instruction,
            "revision_policy": {
                "trigger": "no_activatable_goal_with_evidence_blockers",
                "maximum_attempts": args_cli.world_goal_revision_attempts,
                "complete_replacement_required": True,
                "execution_authority": False,
            },
        },
        "world_scope_membership_audit_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "feasibility_is_membership_authority": False,
            "maximum_revision_attempts": args_cli.world_scope_revision_attempts,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "world_predicate_evaluator_protocol": {
            "shadow_only": True,
            "motion_authority": False,
            "advertisement": world_predicate_evaluator_advertisement,
        },
        "scene_membership_lease_protocol": {
            "shadow_only": True,
            "motion_authority": False,
            "scope_coverage_required": True,
            "unknown_scope_is_admitted": False,
            "resolved_subset_with_deferred_unknowns_is_admitted": True,
            "deferred_unknowns_receive_execution_authority": False,
            "replan_after_goal_completion": True,
            "fresh_complete_graph_required_before_task_completion": True,
        },
        "world_goal_activation_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
            "capability_advertisement": world_capability_advertisement,
        },
        "world_effect_provider_protocol": {
            "shadow_only": True,
            "motion_authority": False,
            "execution_authority": False,
            "runtime_tools": [item.to_dict() for item in runtime_effect_tools],
            "provider_advertisement": (
                world_effect_provider_registry.advertisement()
            ),
            "inside_relation_assessment": (
                inside_effect_provider_assessment.to_dict()
            ),
        },
        "world_effect_session_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "provider_instantiated": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "world_effect_operation_plan_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "planning_provider_instantiated": False,
            "execution_provider_created": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "world_effect_execution_lease_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "configuration_validated": False,
            "execution_lease_issued": False,
            "tool_called": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "world_effect_tool_invocation_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "invocation_validated": False,
            "execution_lease_issued": False,
            "tool_called": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "world_effect_runtime_lease": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "execution_lease_issued": False,
            "lease_armed": False,
            "revocable": False,
            "dispatch_permit_issued": False,
            "tool_called": False,
            "handler_bound": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "world_effect_guarded_dispatch": {
            "status": (
                "pending"
                if args_cli.guarded_world_effect_execution
                else "disabled"
            ),
            "fresh_evidence_validated": False,
            "dispatch_permit_issued": False,
            "dispatch_performed": False,
            "handler_bound": False,
            "tool_called": False,
            "requires_model_replan": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "world_effect_sequence": {
            "status": (
                "pending"
                if args_cli.guarded_world_effect_execution
                else "disabled"
            ),
            "budget": world_effect_sequence_budget.to_dict(),
            "operations": [],
            "progress_observations": [],
            "completed_operation_count": 0,
            "stop_reason": None,
            "task_completion_claimed": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "authority_scope": [],
        },
        "motion_tool_protocol": {
            "observation_bound": True,
            "lease_model_observation_mode": (
                "periodic_or_event"
                if args_cli.periodic_motion_observations
                else "event_or_completion_only"
            ),
            "tracked_pose_source": (
                "ros2_tracked_object_status_when_published_else_"
                "sim_privileged_relative_pose"
            ),
            "rgbd_tracked_orientation_source": (
                "ros2_tracked_object_status_when_published_else_"
                "rgbd_instance_depth_major_axis"
                if args_cli.rgbd_safety
                else None
            ),
            "rgbd_tracked_axis_adapter_implemented": True,
            "maximum_target_correction_m": (
                args_cli.maximum_model_target_correction
            ),
            "maximum_rotation_correction_deg": (
                args_cli.maximum_model_rotation_correction_deg
            ),
            "registered_executors": [
                {
                    "executor_id": spec.executor_id,
                    "tool_name": spec.tool_name,
                    "configuration_schema": spec.configuration_schema,
                }
                for spec in (
                    motion_executor_registry.specs()
                    if motion_executor_registry is not None
                    else ()
                )
            ],
            "calls": [],
        },
        "actuator_tool_protocol": {
            "observation_bound": True,
            "registered_executors": [
                {
                    "executor_id": spec.executor_id,
                    "tool_name": spec.tool_name,
                    "command_schema": spec.command_schema,
                    "configuration_schema": spec.configuration_schema,
                }
                for spec in (
                    actuator_executor_registry.specs()
                    if actuator_executor_registry is not None
                    else ()
                )
            ],
            "calls": [],
        },
        "operation_scheduler_protocol": {
            "observation_bound": True,
            "candidate_source": "runtime",
            "recorded_actuator_hints": False,
            "post_actuation_feedback_policy": {
                "minimum_position_change": (
                    actuator_feedback_policy.minimum_position_change
                ),
                "minimum_force_change_n": (
                    actuator_feedback_policy.minimum_force_change_n
                ),
            },
            "calls": [],
        },
        "runtime_transition_protocol": {
            "authority": "fresh_runtime_capability_evidence",
            "legacy_phase_index_is_control_authority": False,
            "calls": [],
        },
        "transport_recovery": {
            "maximum_attempts": args_cli.max_transport_recoveries,
            "stability_hold_steps": args_cli.recovery_hold_steps,
            "maximum_stability_drift_m": args_cli.recovery_stability_drift,
            "set_down_clearance_m": args_cli.recovery_set_down_clearance,
            "strategies": [
                "stable_grasp_relatch",
                "set_down_reacquire_regrasp",
            ],
        },
        "measured_lift_outcome_recovery": {
            "maximum_operations": args_cli.max_lift_recovery_operations,
            "maximum_failed_grasp_attempts": (
                args_cli.max_failed_grasp_attempts
            ),
            "budget_unit": "fresh_runtime_operation",
            "failure_budget_unit": "physically_tested_grasp_pose",
            "failed_grasp_retry_lease": {
                "minimum_object_relative_translation_delta_m": (
                    args_cli.failed_grasp_retry_minimum_translation
                ),
                "minimum_orientation_delta_deg": (
                    args_cli.failed_grasp_retry_minimum_orientation_deg
                ),
            },
        },
        "object_relative_grasp": {
            "offset_object_m": grasp_offset_object.tolist(),
            "quaternion_object_to_grasp_wxyz": object_to_grasp_quat.tolist(),
        },
        "pregrasp_axis_alignment": {
            "source": (
                "rgbd_oriented_footprint_plus_runtime_contact_geometry"
            ),
            "maximum_axis_error_deg": (
                args_cli.maximum_pregrasp_axis_error_deg
            ),
            "admission_semantics": (
                "actuation_withheld_until_fresh_alignment_observation"
            ),
        },
        "initial_state": _state(env, initial_object_z),
        "stages": [],
        "status": "running",
    }
    trace_path = args_cli.artifact_dir / "sequence_trace.json"
    _write_trace(trace_path, episode_trace)
    episode_recorder: GeminiEpisodeDatasetRecorder | None = None
    ros2_sensor_ingress: ROS2SensorIngress | None = None
    issued_runtime_lease = None
    issued_execution_lease_candidates = None
    issued_execution_lease_decision = None
    issued_invocation_candidates = None
    issued_invocation_decision = None
    issued_planning_provider_instance = None
    if not args_cli.disable_training_recording and not world_effect_only_mode:
        episode_index = (
            args_cli.episode_index
            if args_cli.episode_index >= 0
            else _next_episode_index(training_episode_dir)
        )
        episode_recorder = GeminiEpisodeDatasetRecorder(
            output_dir=training_episode_dir,
            episode_index=episode_index,
            movable_object_asset=SCENE_ROLES.movable_object_asset,
            target_receptacle_asset=SCENE_ROLES.target_receptacle_asset,
            metadata={
                "task": args_cli.task,
                "instruction": args_cli.instruction,
                "scene_roles": SCENE_ROLES.to_dict(),
                "coach_model": MODEL_ID,
                "sim_version": sim_version,
                "movable_object_offset_xy_m": list(
                    args_cli.movable_object_offset
                ),
                "target_receptacle_offset_xy_m": list(args_cli.plate_offset),
                "movable_object_yaw_deg": args_cli.movable_object_yaw_deg,
                "appearance": episode_trace["appearance"],
                "object_relative_grasp": episode_trace["object_relative_grasp"],
            },
            video_writer_factory=VideoWriter,
            unpack_images=unpack_image_obs,
            fps=15,
            video_scale=args_cli.record_video_scale,
            require_contact_telemetry=not args_cli.disable_contact_telemetry,
            minimum_contact_coverage=args_cli.minimum_contact_coverage,
            minimum_touch_samples=args_cli.minimum_touch_samples,
        )
        ACTIVE_EPISODE_RECORDER = episode_recorder
        episode_trace["training_capture"] = {
            "status": "recording_unpublished",
            "episode_index": episode_index,
            "admission_rule": "all_physical_and_supervision_tests_pass",
        }
        _write_trace(trace_path, episode_trace)

    try:
        if args_cli.ros2_sensor_ingress:
            ros2_sensor_ingress = start_ros2_sensor_ingress(
                ROS2SensorIngressConfig(
                    touch_topic=args_cli.ros2_touch_topic,
                    contact_wrench_topic=args_cli.ros2_contact_wrench_topic,
                    contact_status_topic=args_cli.ros2_contact_status_topic,
                    rgbd_status_topic=args_cli.ros2_rgbd_status_topic,
                    safety_stop_topic=args_cli.ros2_safety_stop_topic,
                    tracked_object_status_topic=(
                        args_cli.ros2_tracked_object_status_topic
                    ),
                    motion_status_topic=args_cli.ros2_motion_status_topic,
                )
            )
            ACTIVE_ROS2_SENSOR_INGRESS = ros2_sensor_ingress
            episode_trace["ros2_sensor_ingress"] = {
                "enabled": True,
                "status": (
                    "subscribed"
                    if ros2_sensor_ingress.available
                    else "unavailable_using_simulator_fallback"
                ),
                **ros2_sensor_ingress.status(),
            }
            print(
                "[ros2-ingress] "
                f"status={episode_trace['ros2_sensor_ingress']['status']} "
                f"error={ros2_sensor_ingress.error}",
                flush=True,
            )
            _write_trace(trace_path, episode_trace)
        if args_cli.guarded_world_effect_execution:
            settle_state_before = _state(env, initial_object_z)
            visible_entity_ids_before = set(
                _runtime_geometry_by_id(settle_state_before)
            )
            tracked_positions_before = _tracked_entity_positions_m(
                env, visible_entity_ids_before
            )
            settle_action = _current_robot_joint_action(
                env,
                gripper_closed_fraction=float(
                    settle_state_before["gripper_closed_fraction"]
                ),
            )
            obs, settle_terminal = _hold_joint_action(
                env,
                obs,
                settle_action,
                args_cli.world_effect_preflight_settle_steps,
            )
            if settle_terminal:
                raise RuntimeError(
                    "environment terminated during world-effect preflight settling"
                )
            env.sim.render()
            settle_state_after = _state(env, initial_object_z)
            visible_entity_ids_after = set(_runtime_geometry_by_id(settle_state_after))
            tracked_entity_ids = visible_entity_ids_before | visible_entity_ids_after
            tracked_positions_after = _tracked_entity_positions_m(
                env, tracked_entity_ids
            )
            displacement_by_entity_m = {
                entity_id: float(
                    np.linalg.norm(
                        np.asarray(tracked_positions_after[entity_id])
                        - np.asarray(tracked_positions_before[entity_id])
                    )
                )
                for entity_id in sorted(
                    set(tracked_positions_before) & set(tracked_positions_after)
                )
            }
            episode_trace["world_effect_preflight_settle"] = {
                "status": "complete",
                "steps": args_cli.world_effect_preflight_settle_steps,
                "robot_command": "hold_current_joint_state",
                "motion_authority": False,
                "task_operation_performed": False,
                "tracked_entity_displacement_m": displacement_by_entity_m,
                "maximum_tracked_entity_displacement_m": max(
                    displacement_by_entity_m.values(), default=0.0
                ),
                "evidence_created_after_settle": True,
            }
            _write_trace(trace_path, episode_trace)
            print(
                "[world-effect-preflight] SETTLED "
                f"steps={args_cli.world_effect_preflight_settle_steps} "
                "authority=none",
                flush=True,
            )
        frame = _single_exterior_frame(obs)
        cv2.imwrite(
            str(args_cli.artifact_dir / "00_scene.jpg"),
            cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
        )
        preflight_state = _state(env, initial_object_z)
        preflight_state["entity_physical_evidence"] = (
            _runtime_scene_entity_physical_evidence(env, preflight_state)
        )
        semantic_scene_inventory = semantic_scene_inventory_from_state(
            preflight_state
        )
        preflight_entity_ids = {
            str(item["entity_id"])
            for item in semantic_scene_inventory.get("entities", [])
            if isinstance(item, Mapping) and isinstance(item.get("entity_id"), str)
        }
        preflight_tracked_positions_m = _tracked_entity_positions_m(
            env, preflight_entity_ids
        )
        semantic_scene_inventory_digest = hashlib.sha256(
            json.dumps(
                semantic_scene_inventory,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        if not args_cli.disable_world_intent_shadow:
            try:
                shadow_payload, shadow_latency, shadow_digest = coach.reason(
                    build_world_intent_prompt(args_cli.instruction),
                    frame,
                )
                shadow_intent = WorldIntent.from_mapping(shadow_payload)
                episode_trace["world_intent_shadow"] = {
                    "status": "valid",
                    "contract_version": WORLD_INTENT_SCHEMA_VERSION,
                    "motion_authority": False,
                    "authority_scope": [],
                    "instruction": args_cli.instruction,
                    "intent": shadow_intent.to_dict(),
                    "latency_s": shadow_latency,
                    "image_digest": shadow_digest,
                }
                print(
                    f"[world-intent] VALID operation={shadow_intent.operation} "
                    f"goals={len(shadow_intent.goals)} "
                    f"constraints={len(shadow_intent.constraints)} "
                    f"latency={shadow_latency:.2f}s authority=none",
                    flush=True,
                )
            except Exception as shadow_error:
                episode_trace["world_intent_shadow"] = {
                    "status": "invalid",
                    "contract_version": WORLD_INTENT_SCHEMA_VERSION,
                    "motion_authority": False,
                    "authority_scope": [],
                    "instruction": args_cli.instruction,
                    "error": {
                        "type": type(shadow_error).__name__,
                        "message": str(shadow_error),
                    },
                }
                print(
                    f"[world-intent] INVALID {type(shadow_error).__name__}: "
                    f"{shadow_error} authority=none",
                    flush=True,
                )
            _write_trace(trace_path, episode_trace)

            try:
                (
                    goal_graph_payload,
                    goal_graph_latency,
                    goal_graph_image_digest,
                ) = coach.reason(
                    build_world_goal_graph_prompt(
                        args_cli.instruction,
                        semantic_scene_inventory,
                        world_predicate_evaluator_advertisement,
                    ),
                    frame,
                )
                goal_graph = WorldGoalGraph.from_mapping(goal_graph_payload)
                validate_world_goal_graph_entity_references(
                    goal_graph,
                    semantic_scene_inventory,
                )
                initial_goal_graph = goal_graph
                scope_membership_observation_id = (
                    world_scope_membership_observation_id(
                        args_cli.instruction,
                        semantic_scene_inventory,
                        initial_goal_graph,
                    )
                )
                (
                    scope_membership_payload,
                    scope_membership_latency,
                    scope_membership_image_digest,
                ) = coach.reason(
                    build_world_scope_membership_audit_prompt(
                        instruction=args_cli.instruction,
                        observation_id=scope_membership_observation_id,
                        inventory=semantic_scene_inventory,
                        graph=initial_goal_graph,
                    ),
                    frame,
                )
                scope_membership_audit = WorldScopeMembershipAuditGate(
                    scope_membership_observation_id,
                    semantic_scene_inventory,
                ).dispatch(scope_membership_payload)
                initial_scope_membership_assessment = (
                    assess_world_goal_graph_membership_audit(
                        initial_goal_graph,
                        scope_membership_audit,
                    )
                )
                goal_graph_scope_membership_assessment = (
                    initial_scope_membership_assessment
                )
                scope_membership_revision_attempts: list[dict[str, Any]] = []
                audited_included_entity_ids = tuple(
                    item.entity_id
                    for item in scope_membership_audit.decisions
                    if item.status == "included"
                )
                for scope_revision_index in range(
                    args_cli.world_scope_revision_attempts
                ):
                    if (
                        goal_graph_scope_membership_assessment.admitted
                        or goal_graph_scope_membership_assessment.resolved_subset_admitted
                    ):
                        break
                    scope_revision_context = {
                        "revision_attempt": scope_revision_index + 1,
                        "trigger": "task_membership_audit_conflict",
                        "previous_graph": goal_graph.to_dict(),
                        "task_membership_audit": (
                            scope_membership_audit.to_dict()
                        ),
                        "membership_assessment": (
                            goal_graph_scope_membership_assessment.to_dict()
                        ),
                        "requirements": {
                            "complete_replacement_graph": True,
                            "preserve_exact_audited_scope": True,
                            "feasibility_is_membership_authority": False,
                            "execution_authority": False,
                        },
                    }
                    scope_revision_latency: float | None = None
                    scope_revision_image_digest: str | None = None
                    try:
                        (
                            scope_revised_payload,
                            scope_revision_latency,
                            scope_revision_image_digest,
                        ) = coach.reason(
                            build_world_goal_graph_prompt(
                                args_cli.instruction,
                                semantic_scene_inventory,
                                world_predicate_evaluator_advertisement,
                                revision_context=scope_revision_context,
                            ),
                            frame,
                        )
                        scope_revised_graph = WorldGoalGraph.from_mapping(
                            scope_revised_payload
                        )
                        validate_world_goal_graph_entity_references(
                            scope_revised_graph,
                            semantic_scene_inventory,
                        )
                        validate_world_goal_graph_revision(
                            goal_graph,
                            scope_revised_graph,
                            (),
                            preserve_included_entity_ids=(
                                audited_included_entity_ids
                            ),
                        )
                        scope_revised_assessment = (
                            assess_world_goal_graph_membership_audit(
                                scope_revised_graph,
                                scope_membership_audit,
                            )
                        )
                        scope_membership_revision_attempts.append(
                            {
                                "attempt": scope_revision_index + 1,
                                "status": (
                                    "admitted"
                                    if scope_revised_assessment.admitted
                                    else "not_admitted"
                                ),
                                "replacement_goal_graph": (
                                    scope_revised_graph.to_dict()
                                ),
                                "membership_assessment": (
                                    scope_revised_assessment.to_dict()
                                ),
                                "latency_s": scope_revision_latency,
                                "image_digest": scope_revision_image_digest,
                                "motion_authority": False,
                                "execution_authority": False,
                                "authority_scope": [],
                            }
                        )
                        if (
                            scope_revised_assessment.admitted
                            or scope_revised_assessment.resolved_subset_admitted
                        ):
                            goal_graph = scope_revised_graph
                            goal_graph_scope_membership_assessment = (
                                scope_revised_assessment
                            )
                            print(
                                "[world-scope-membership] CORRECTED "
                                f"attempt={scope_revision_index + 1} "
                                f"graph={goal_graph.graph_id} authority=none",
                                flush=True,
                            )
                            break
                    except Exception as scope_revision_error:
                        scope_membership_revision_attempts.append(
                            {
                                "attempt": scope_revision_index + 1,
                                "status": "invalid",
                                "error": {
                                    "type": type(scope_revision_error).__name__,
                                    "message": str(scope_revision_error),
                                },
                                "latency_s": scope_revision_latency,
                                "image_digest": scope_revision_image_digest,
                                "motion_authority": False,
                                "execution_authority": False,
                                "authority_scope": [],
                            }
                        )
                        print(
                            "[world-scope-membership] REVISION_INVALID "
                            f"attempt={scope_revision_index + 1} "
                            f"{type(scope_revision_error).__name__}: "
                            f"{scope_revision_error} authority=none",
                            flush=True,
                        )
                episode_trace["world_scope_membership_audit_shadow"] = {
                    "status": "valid",
                    "observation_id": scope_membership_observation_id,
                    "audit": scope_membership_audit.to_dict(),
                    "initial_assessment": (
                        initial_scope_membership_assessment.to_dict()
                    ),
                    "final_assessment": (
                        goal_graph_scope_membership_assessment.to_dict()
                    ),
                    "revision_attempts": scope_membership_revision_attempts,
                    "latency_s": scope_membership_latency,
                    "image_digest": scope_membership_image_digest,
                    "feasibility_is_membership_authority": False,
                    "motion_authority": False,
                    "execution_authority": False,
                    "authority_scope": [],
                }
                print(
                    "[world-scope-membership] VALID scope="
                    f"{scope_membership_audit.instruction_scope} "
                    "initial_admitted="
                    f"{initial_scope_membership_assessment.admitted} "
                    "final_admitted="
                    f"{goal_graph_scope_membership_assessment.admitted} "
                    "resolved_subset_admitted="
                    f"{goal_graph_scope_membership_assessment.resolved_subset_admitted} "
                    "deferred_unknowns="
                    f"{len(goal_graph_scope_membership_assessment.unknown_entity_ids)} "
                    "mismatches="
                    f"{len(goal_graph_scope_membership_assessment.mismatches)} "
                    f"latency={scope_membership_latency:.2f}s authority=none",
                    flush=True,
                )
                goal_graph_predicate_admission = (
                    world_predicate_evaluator_registry.assess_graph(
                        goal_graph,
                        semantic_scene_inventory,
                    )
                )
                goal_graph_scene_scope_admission = (
                    assess_world_goal_graph_scene_scope(
                        goal_graph,
                        semantic_scene_inventory,
                    )
                )
                task_membership_activation_admitted = bool(
                    goal_graph_scope_membership_assessment.admitted
                    or goal_graph_scope_membership_assessment.resolved_subset_admitted
                )
                predicate_activation_admitted = bool(
                    goal_graph_predicate_admission.admitted
                    or goal_graph_predicate_admission.resolved_subset_admitted
                )
                scene_scope_activation_admitted = bool(
                    goal_graph_scene_scope_admission.admitted
                    or goal_graph_scene_scope_admission.resolved_subset_admitted
                )
                goal_graph_membership_lease = (
                    SceneMembershipLease.issue(
                        goal_graph,
                        semantic_scene_inventory,
                        goal_graph_scene_scope_admission,
                    )
                    if scene_scope_activation_admitted
                    else None
                )
                goal_graph_shadow_admitted = bool(
                    task_membership_activation_admitted
                    and predicate_activation_admitted
                    and scene_scope_activation_admitted
                    and goal_graph_membership_lease is not None
                )
                resolved_subset_activation = bool(
                    goal_graph_shadow_admitted
                    and not goal_graph_scope_membership_assessment.admitted
                )
                episode_trace["world_goal_graph_shadow"] = {
                    "status": "valid",
                    "contract_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
                    "inventory_contract_version": (
                        SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION
                    ),
                    "motion_authority": False,
                    "authority_scope": [],
                    "instruction": args_cli.instruction,
                    "scene_inventory_digest": semantic_scene_inventory_digest,
                    "scene_inventory": semantic_scene_inventory,
                    "initial_goal_graph": initial_goal_graph.to_dict(),
                    "goal_graph": goal_graph.to_dict(),
                    "task_membership_audit_admission": (
                        goal_graph_scope_membership_assessment.to_dict()
                    ),
                    "scope_revision_attempts": (
                        scope_membership_revision_attempts
                    ),
                    "predicate_evaluator_admission": (
                        goal_graph_predicate_admission.to_dict()
                    ),
                    "scene_scope_admission": (
                        goal_graph_scene_scope_admission.to_dict()
                    ),
                    "scene_membership_lease": (
                        None
                        if goal_graph_membership_lease is None
                        else goal_graph_membership_lease.to_dict()
                    ),
                    "combined_shadow_admission": {
                        "admitted": goal_graph_shadow_admitted,
                        "admission_mode": (
                            "resolved_subset"
                            if resolved_subset_activation
                            else "complete_scope"
                        ),
                        "scope_resolution_complete": (
                            goal_graph_scope_membership_assessment.scope_resolution_complete
                        ),
                        "deferred_unknown_entity_ids": list(
                            goal_graph_scope_membership_assessment.unknown_entity_ids
                        ),
                        "task_completion_allowed": bool(
                            goal_graph_scope_membership_assessment.scope_resolution_complete
                            and goal_graph.status == "complete"
                        ),
                        "task_membership_admitted": (
                            goal_graph_scope_membership_assessment.admitted
                        ),
                        "resolved_subset_admitted": (
                            goal_graph_scope_membership_assessment.resolved_subset_admitted
                        ),
                        "motion_authority": False,
                        "authority_scope": [],
                    },
                    "latency_s": goal_graph_latency,
                    "image_digest": goal_graph_image_digest,
                    "revision_policy": {
                        "trigger": (
                            "no_activatable_goal_with_evidence_blockers"
                        ),
                        "maximum_attempts": (
                            args_cli.world_goal_revision_attempts
                        ),
                        "complete_replacement_required": True,
                        "execution_authority": False,
                    },
                    "revision_attempts": [],
                }
                print(
                    f"[world-goal-graph] VALID status={goal_graph.status} "
                    f"goals={len(goal_graph.goals)} "
                    f"roots={list(goal_graph.root_goal_ids)} "
                    "predicate_admitted="
                    f"{predicate_activation_admitted} "
                    "scope_admitted="
                    f"{scene_scope_activation_admitted} "
                    "membership_admitted="
                    f"{task_membership_activation_admitted} "
                    "admission_mode="
                    f"{'resolved_subset' if resolved_subset_activation else 'complete_scope'} "
                    f"entities={len(semantic_scene_inventory['entities'])} "
                    f"latency={goal_graph_latency:.2f}s authority=none",
                    flush=True,
                )
                if goal_graph_shadow_admitted:
                    try:
                        goal_activation_candidates = (
                            build_goal_activation_candidates(
                                goal_graph,
                                goal_graph_membership_lease,
                                world_predicate_evaluator_registry,
                                world_capability_registry,
                                semantic_scene_inventory,
                            )
                        )
                        goal_graph_revision_attempts: list[dict[str, Any]] = []
                        for revision_index in range(
                            args_cli.world_goal_revision_attempts
                        ):
                            if (
                                goal_activation_candidates.candidates
                                or not goal_activation_candidates.evidence_blocked_goal_ids
                            ):
                                break
                            previous_goal_graph = goal_graph
                            previous_blocked_goal_ids = (
                                goal_activation_candidates.evidence_blocked_goal_ids
                            )
                            revision_context = {
                                "revision_attempt": revision_index + 1,
                                "trigger": (
                                    "no_activatable_goal_with_evidence_blockers"
                                ),
                                "previous_graph": previous_goal_graph.to_dict(),
                                "activation_candidate_set": (
                                    goal_activation_candidates.to_dict()
                                ),
                                "runtime_effect_provider_assessment": (
                                    inside_effect_provider_assessment.to_dict()
                                ),
                                "task_membership_audit": (
                                    scope_membership_audit.to_dict()
                                ),
                                "requirements": {
                                    "complete_replacement_graph": True,
                                    "preserve_scene_membership": True,
                                    "preserve_unresolved_blocked_outcomes": True,
                                    "separate_independent_world_state_changes": True,
                                    "execution_authority": False,
                                },
                            }
                            revision_latency: float | None = None
                            revision_image_digest: str | None = None
                            try:
                                (
                                    revised_goal_graph_payload,
                                    revision_latency,
                                    revision_image_digest,
                                ) = coach.reason(
                                    build_world_goal_graph_prompt(
                                        args_cli.instruction,
                                        semantic_scene_inventory,
                                        world_predicate_evaluator_advertisement,
                                        revision_context=revision_context,
                                    ),
                                    frame,
                                )
                                revised_goal_graph = WorldGoalGraph.from_mapping(
                                    revised_goal_graph_payload
                                )
                                validate_world_goal_graph_entity_references(
                                    revised_goal_graph,
                                    semantic_scene_inventory,
                                )
                                validate_world_goal_graph_revision(
                                    previous_goal_graph,
                                    revised_goal_graph,
                                    previous_blocked_goal_ids,
                                )
                                revised_predicate_admission = (
                                    world_predicate_evaluator_registry.assess_graph(
                                        revised_goal_graph,
                                        semantic_scene_inventory,
                                    )
                                )
                                revised_scope_membership_assessment = (
                                    assess_world_goal_graph_membership_audit(
                                        revised_goal_graph,
                                        scope_membership_audit,
                                    )
                                )
                                revised_scene_scope_admission = (
                                    assess_world_goal_graph_scene_scope(
                                        revised_goal_graph,
                                        semantic_scene_inventory,
                                    )
                                )
                                revised_membership_lease = (
                                    SceneMembershipLease.issue(
                                        revised_goal_graph,
                                        semantic_scene_inventory,
                                        revised_scene_scope_admission,
                                    )
                                    if revised_scene_scope_admission.admitted
                                    else None
                                )
                                revised_shadow_admitted = bool(
                                    revised_scope_membership_assessment.admitted
                                    and revised_predicate_admission.admitted
                                    and revised_scene_scope_admission.admitted
                                    and revised_membership_lease is not None
                                )
                                revision_record: dict[str, Any] = {
                                    "attempt": revision_index + 1,
                                    "status": (
                                        "admitted"
                                        if revised_shadow_admitted
                                        else "not_admitted"
                                    ),
                                    "trigger_evidence": (
                                        goal_activation_candidates.to_dict()
                                    ),
                                    "replacement_goal_graph": (
                                        revised_goal_graph.to_dict()
                                    ),
                                    "predicate_evaluator_admission": (
                                        revised_predicate_admission.to_dict()
                                    ),
                                    "scene_scope_admission": (
                                        revised_scene_scope_admission.to_dict()
                                    ),
                                    "task_membership_audit_admission": (
                                        revised_scope_membership_assessment.to_dict()
                                    ),
                                    "scene_membership_lease": (
                                        None
                                        if revised_membership_lease is None
                                        else revised_membership_lease.to_dict()
                                    ),
                                    "latency_s": revision_latency,
                                    "image_digest": revision_image_digest,
                                    "motion_authority": False,
                                    "execution_authority": False,
                                    "authority_scope": [],
                                }
                                if not revised_shadow_admitted:
                                    goal_graph_revision_attempts.append(
                                        revision_record
                                    )
                                    print(
                                        "[world-goal-revision] NOT_ADMITTED "
                                        f"attempt={revision_index + 1} "
                                        "authority=none",
                                        flush=True,
                                    )
                                    continue
                                revised_activation_candidates = (
                                    build_goal_activation_candidates(
                                        revised_goal_graph,
                                        revised_membership_lease,
                                        world_predicate_evaluator_registry,
                                        world_capability_registry,
                                        semantic_scene_inventory,
                                    )
                                )
                                revision_record["activation_candidate_set"] = (
                                    revised_activation_candidates.to_dict()
                                )
                                goal_graph_revision_attempts.append(revision_record)
                                goal_graph = revised_goal_graph
                                goal_graph_predicate_admission = (
                                    revised_predicate_admission
                                )
                                goal_graph_scene_scope_admission = (
                                    revised_scene_scope_admission
                                )
                                goal_graph_membership_lease = (
                                    revised_membership_lease
                                )
                                goal_graph_scope_membership_assessment = (
                                    revised_scope_membership_assessment
                                )
                                goal_graph_shadow_admitted = True
                                goal_activation_candidates = (
                                    revised_activation_candidates
                                )
                                print(
                                    "[world-goal-revision] ADMITTED "
                                    f"attempt={revision_index + 1} "
                                    f"goals={len(goal_graph.goals)} "
                                    "candidates="
                                    f"{len(goal_activation_candidates.candidates)} "
                                    "evidence_blocked="
                                    f"{list(goal_activation_candidates.evidence_blocked_goal_ids)} "
                                    "authority=none",
                                    flush=True,
                                )
                            except Exception as revision_error:
                                goal_graph_revision_attempts.append(
                                    {
                                        "attempt": revision_index + 1,
                                        "status": "invalid",
                                        "trigger_evidence": (
                                            goal_activation_candidates.to_dict()
                                        ),
                                        "error": {
                                            "type": type(revision_error).__name__,
                                            "message": str(revision_error),
                                        },
                                        "latency_s": revision_latency,
                                        "image_digest": revision_image_digest,
                                        "motion_authority": False,
                                        "execution_authority": False,
                                        "authority_scope": [],
                                    }
                                )
                                print(
                                    "[world-goal-revision] INVALID "
                                    f"attempt={revision_index + 1} "
                                    f"{type(revision_error).__name__}: "
                                    f"{revision_error} authority=none",
                                    flush=True,
                                )
                        goal_graph_trace = episode_trace[
                            "world_goal_graph_shadow"
                        ]
                        goal_graph_trace["revision_attempts"] = (
                            goal_graph_revision_attempts
                        )
                        goal_graph_trace["revision_count"] = len(
                            goal_graph_revision_attempts
                        )
                        goal_graph_trace["goal_graph"] = goal_graph.to_dict()
                        goal_graph_trace["predicate_evaluator_admission"] = (
                            goal_graph_predicate_admission.to_dict()
                        )
                        goal_graph_trace["scene_scope_admission"] = (
                            goal_graph_scene_scope_admission.to_dict()
                        )
                        goal_graph_trace["task_membership_audit_admission"] = (
                            goal_graph_scope_membership_assessment.to_dict()
                        )
                        goal_graph_trace["scene_membership_lease"] = (
                            goal_graph_membership_lease.to_dict()
                        )
                        goal_graph_trace["combined_shadow_admission"] = {
                            "admitted": goal_graph_shadow_admitted,
                            "task_membership_admitted": (
                                goal_graph_scope_membership_assessment.admitted
                            ),
                            "motion_authority": False,
                            "authority_scope": [],
                        }
                        goal_graph_digest = hashlib.sha256(
                            json.dumps(
                                goal_graph.to_dict(),
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest()[:16]
                        goal_activation_observation_id = (
                            "goal-activation:"
                            f"{semantic_scene_inventory_digest}:"
                            f"{goal_graph_digest}"
                        )
                        (
                            goal_activation_payload,
                            goal_activation_latency,
                            goal_activation_image_digest,
                        ) = coach.reason(
                            build_world_goal_activation_prompt(
                                instruction=args_cli.instruction,
                                observation_id=(
                                    goal_activation_observation_id
                                ),
                                graph=goal_graph,
                                membership_lease=(
                                    goal_graph_membership_lease
                                ),
                                inventory=semantic_scene_inventory,
                                capability_advertisement=(
                                    world_capability_advertisement
                                ),
                                candidate_set=goal_activation_candidates,
                            ),
                            frame,
                        )
                        goal_activation_decision = WorldGoalActivationGate(
                            goal_activation_observation_id,
                            goal_activation_candidates,
                        ).dispatch(goal_activation_payload)
                        episode_trace["world_goal_activation_shadow"] = {
                            "status": "valid",
                            "motion_authority": False,
                            "execution_authority": False,
                            "authority_scope": [],
                            "observation_id": goal_activation_observation_id,
                            "goal_graph_id": goal_graph.graph_id,
                            "goal_graph_digest": goal_graph_digest,
                            "candidate_set": (
                                goal_activation_candidates.to_dict()
                            ),
                            "decision": goal_activation_decision.to_dict(),
                            "latency_s": goal_activation_latency,
                            "image_digest": goal_activation_image_digest,
                        }
                        print(
                            "[world-goal-activation] VALID decision="
                            f"{goal_activation_decision.decision} "
                            f"goal={goal_activation_decision.goal_id} "
                            "capability="
                            f"{goal_activation_decision.capability_id} "
                            f"latency={goal_activation_latency:.2f}s "
                            "authority=none",
                            flush=True,
                        )
                        if goal_activation_decision.decision == "select_goal":
                            effect_session_latency: float | None = None
                            effect_session_image_digest: str | None = None
                            try:
                                selected_provider_assessment = (
                                    world_effect_provider_registry.assess(
                                        goal_activation_decision.capability_id,
                                        runtime_effect_tools,
                                    )
                                )
                                effect_session_candidates = (
                                    build_world_effect_session_candidates(
                                        goal_graph,
                                        goal_graph_membership_lease,
                                        goal_activation_candidates,
                                        goal_activation_decision,
                                        selected_provider_assessment,
                                    )
                                )
                                (
                                    effect_session_payload,
                                    effect_session_latency,
                                    effect_session_image_digest,
                                ) = coach.reason(
                                    build_world_effect_session_prompt(
                                        instruction=args_cli.instruction,
                                        graph=goal_graph,
                                        membership_lease=(
                                            goal_graph_membership_lease
                                        ),
                                        activation_decision=(
                                            goal_activation_decision
                                        ),
                                        candidate_set=effect_session_candidates,
                                    ),
                                    frame,
                                )
                                effect_session_decision = WorldEffectSessionGate(
                                    effect_session_candidates
                                ).dispatch(effect_session_payload)
                                episode_trace["world_effect_session_shadow"] = {
                                    "status": "valid",
                                    "provider_assessment": (
                                        selected_provider_assessment.to_dict()
                                    ),
                                    "candidate_set": (
                                        effect_session_candidates.to_dict()
                                    ),
                                    "decision": effect_session_decision.to_dict(),
                                    "latency_s": effect_session_latency,
                                    "image_digest": effect_session_image_digest,
                                    "provider_instantiated": False,
                                    "motion_authority": False,
                                    "execution_authority": False,
                                    "authority_scope": [],
                                }
                                print(
                                    "[world-effect-session] VALID decision="
                                    f"{effect_session_decision.decision} "
                                    "provider="
                                    f"{effect_session_decision.provider_id} "
                                    f"latency={effect_session_latency:.2f}s "
                                    "instantiated=false authority=none",
                                    flush=True,
                                )
                                if (
                                    effect_session_decision.decision
                                    == "select_provider"
                                ):
                                    operation_latency: float | None = None
                                    operation_image_digest: str | None = None
                                    try:
                                        planning_factory_catalog = (
                                            PlanningToolFactoryCatalog()
                                        )
                                        planning_factory_catalog.register(
                                            PlanningToolFactory(
                                                factory_tool_id=(
                                                    "factory.spatial_pose_target"
                                                ),
                                                tool_family="motion",
                                                capability_tags=(
                                                    SPATIAL_MOTION_CAPABILITY_TAGS
                                                ),
                                                activator=lambda: (
                                                    _local_dls_executor_registry(
                                                        trackable_object_ids
                                                    ).advertisement()
                                                ),
                                            )
                                        )
                                        planning_factory_catalog.register(
                                            PlanningToolFactory(
                                                factory_tool_id=(
                                                    "factory.reversible_"
                                                    "entity_attachment"
                                                ),
                                                tool_family="actuator",
                                                capability_tags=(
                                                    REVERSIBLE_ATTACHMENT_CAPABILITY_TAGS
                                                ),
                                                activator=lambda: (
                                                    _local_binary_actuator_registry(
                                                    ).advertisement()
                                                ),
                                            )
                                        )
                                        planning_provider_instance = (
                                            build_planning_world_effect_provider_instance(
                                                effect_session_candidates,
                                                effect_session_decision,
                                                runtime_effect_tools,
                                                planning_factory_catalog,
                                            )
                                        )
                                        effect_operation_candidates = (
                                            build_world_effect_operation_candidates(
                                                planning_provider_instance,
                                                semantic_scene_inventory,
                                            )
                                        )
                                        (
                                            operation_payload,
                                            operation_latency,
                                            operation_image_digest,
                                        ) = coach.reason(
                                            build_world_effect_operation_prompt(
                                                instruction=(
                                                    args_cli.instruction
                                                ),
                                                inventory=(
                                                    semantic_scene_inventory
                                                ),
                                                instance=(
                                                    planning_provider_instance
                                                ),
                                                candidate_set=(
                                                    effect_operation_candidates
                                                ),
                                            ),
                                            frame,
                                        )
                                        effect_operation_decision = (
                                            WorldEffectOperationGate(
                                                effect_operation_candidates
                                            ).dispatch(operation_payload)
                                        )
                                        episode_trace["world_effect_operation_plan_shadow"] = {
                                            "status": "valid",
                                            "planning_provider_instance": (
                                                planning_provider_instance.to_dict()
                                            ),
                                            "candidate_set": (
                                                effect_operation_candidates.to_dict()
                                            ),
                                            "decision": (
                                                effect_operation_decision.to_dict()
                                            ),
                                            "latency_s": operation_latency,
                                            "image_digest": (
                                                operation_image_digest
                                            ),
                                            "planning_provider_instantiated": True,
                                            "execution_provider_created": False,
                                            "handler_bound": False,
                                            "dispatch_enabled": False,
                                            "motion_authority": False,
                                            "execution_authority": False,
                                            "authority_scope": [],
                                        }
                                        print(
                                            "[world-effect-operation] VALID "
                                            "decision="
                                            f"{effect_operation_decision.decision} "
                                            "requirement="
                                            f"{effect_operation_decision.requirement_id} "
                                            "tool="
                                            f"{effect_operation_decision.tool_id} "
                                            "purpose="
                                            f"{effect_operation_decision.purpose} "
                                            "planning_provider=true "
                                            "dispatch=false authority=none",
                                            flush=True,
                                        )
                                        if (
                                            effect_operation_decision.decision
                                            == "propose_operation"
                                        ):
                                            lease_latency: float | None = None
                                            lease_image_digest: str | None = None
                                            try:
                                                execution_lease_candidates = (
                                                    build_shadow_execution_lease_candidates(
                                                        planning_provider_instance,
                                                        effect_operation_candidates,
                                                        effect_operation_decision,
                                                        semantic_scene_inventory,
                                                    )
                                                )
                                                (
                                                    lease_payload,
                                                    lease_latency,
                                                    lease_image_digest,
                                                ) = coach.reason(
                                                    build_shadow_execution_lease_prompt(
                                                        instruction=(
                                                            args_cli.instruction
                                                        ),
                                                        candidate_set=(
                                                            execution_lease_candidates
                                                        ),
                                                    ),
                                                    frame,
                                                )
                                                execution_lease_decision = (
                                                    ShadowExecutionLeaseGate(
                                                        execution_lease_candidates
                                                    ).dispatch(lease_payload)
                                                )
                                                episode_trace["world_effect_execution_lease_shadow"] = {
                                                    "status": "valid",
                                                    "candidate_set": (
                                                        execution_lease_candidates.to_dict()
                                                    ),
                                                    "decision": (
                                                        execution_lease_decision.to_dict()
                                                    ),
                                                    "latency_s": lease_latency,
                                                    "image_digest": (
                                                        lease_image_digest
                                                    ),
                                                    "configuration_validated": (
                                                        execution_lease_decision.decision
                                                        == "propose_lease"
                                                    ),
                                                    "execution_lease_issued": False,
                                                    "tool_called": False,
                                                    "handler_bound": False,
                                                    "dispatch_enabled": False,
                                                    "motion_authority": False,
                                                    "execution_authority": False,
                                                    "authority_scope": [],
                                                }
                                                print(
                                                    "[world-effect-execution-lease] "
                                                    "VALID decision="
                                                    f"{execution_lease_decision.decision} "
                                                    "tool="
                                                    f"{execution_lease_decision.tool_id} "
                                                    "groundings="
                                                    f"{list(execution_lease_decision.grounding_entity_ids)} "
                                                    "invalidations="
                                                    f"{len(execution_lease_decision.invalidation_conditions)} "
                                                    "issued=false dispatch=false "
                                                    "authority=none",
                                                    flush=True,
                                                )
                                                if (
                                                    execution_lease_decision.decision
                                                    == "propose_lease"
                                                ):
                                                    invocation_latency: float | None = None
                                                    invocation_image_digest: str | None = None
                                                    try:
                                                        interaction_geometry = (
                                                            preflight_state.get(
                                                                "actuator_contact_geometry",
                                                                {},
                                                            )
                                                        )
                                                        if not isinstance(
                                                            interaction_geometry,
                                                            Mapping,
                                                        ):
                                                            raise ValueError(
                                                                "runtime interaction geometry "
                                                                "must be an object"
                                                            )
                                                        runtime_tool_observation = {
                                                            "schema_version": (
                                                                RUNTIME_TOOL_OBSERVATION_SCHEMA_VERSION
                                                            ),
                                                            "source": (
                                                                "fresh_simulator_controlled_"
                                                                "and_interaction_frames"
                                                            ),
                                                            "coordinate_frame": (
                                                                semantic_scene_inventory.get(
                                                                    "frame",
                                                                    "unknown",
                                                                )
                                                            ),
                                                            "controlled_frame": {
                                                                "position_m": (
                                                                    preflight_state[
                                                                        "eef_gripper_base_xyz"
                                                                    ]
                                                                ),
                                                                "quaternion_wxyz": (
                                                                    preflight_state[
                                                                        "eef_gripper_base_"
                                                                        "quaternion_wxyz"
                                                                    ]
                                                                ),
                                                            },
                                                            "interaction_frame": {
                                                                "origin_offset_local_m": (
                                                                    interaction_geometry.get(
                                                                        "contact_center_local_m"
                                                                    )
                                                                ),
                                                                "alignment_axis_local": (
                                                                    interaction_geometry.get(
                                                                        "closing_axis_local"
                                                                    )
                                                                ),
                                                                "alignment_relation": (
                                                                    "surface_tangent"
                                                                ),
                                                            },
                                                        }
                                                        invocation_candidates = (
                                                            build_shadow_tool_invocation_candidates(
                                                                planning_provider_instance,
                                                                execution_lease_candidates,
                                                                execution_lease_decision,
                                                                runtime_tool_observation,
                                                            )
                                                        )
                                                        (
                                                            invocation_payload,
                                                            invocation_latency,
                                                            invocation_image_digest,
                                                        ) = coach.reason(
                                                            build_shadow_tool_invocation_prompt(
                                                                instruction=(
                                                                    args_cli.instruction
                                                                ),
                                                                candidate_set=(
                                                                    invocation_candidates
                                                                ),
                                                            ),
                                                            frame,
                                                        )
                                                        invocation_decision = (
                                                            ShadowToolInvocationGate(
                                                                invocation_candidates
                                                            ).dispatch(
                                                                invocation_payload
                                                            )
                                                        )
                                                        episode_trace["world_effect_tool_invocation_shadow"] = {
                                                            "status": "valid",
                                                            "runtime_observation": (
                                                                runtime_tool_observation
                                                            ),
                                                            "candidate_set": (
                                                                invocation_candidates.to_dict()
                                                            ),
                                                            "decision": (
                                                                invocation_decision.to_dict()
                                                            ),
                                                            "latency_s": (
                                                                invocation_latency
                                                            ),
                                                            "image_digest": (
                                                                invocation_image_digest
                                                            ),
                                                            "invocation_validated": (
                                                                invocation_decision.decision
                                                                == "propose_invocation"
                                                            ),
                                                            "execution_lease_issued": False,
                                                            "tool_called": False,
                                                            "handler_bound": False,
                                                            "dispatch_enabled": False,
                                                            "motion_authority": False,
                                                            "execution_authority": False,
                                                            "authority_scope": [],
                                                        }
                                                        print(
                                                            "[world-effect-tool-invocation] "
                                                            "VALID decision="
                                                            f"{invocation_decision.decision} "
                                                            "tool="
                                                            f"{invocation_decision.tool_id} "
                                                            "position_anchor="
                                                            f"{invocation_decision.position_anchor_id} "
                                                            "orientation_axis="
                                                            f"{invocation_decision.orientation_alignment_id} "
                                                            "issued=false called=false "
                                                            "dispatch=false authority=none",
                                                            flush=True,
                                                        )
                                                        if (
                                                            invocation_decision.decision
                                                            == "propose_invocation"
                                                        ):
                                                            try:
                                                                runtime_lease = (
                                                                    issue_world_effect_runtime_lease(
                                                                        lease_candidates=(
                                                                            execution_lease_candidates
                                                                        ),
                                                                        lease_decision=(
                                                                            execution_lease_decision
                                                                        ),
                                                                        invocation_candidates=(
                                                                            invocation_candidates
                                                                        ),
                                                                        invocation_decision=(
                                                                            invocation_decision
                                                                        ),
                                                                        maximum_duration_s=(
                                                                            args_cli.world_effect_runtime_lease_duration_s
                                                                        ),
                                                                    )
                                                                )
                                                                runtime_lease_record = (
                                                                    runtime_lease.to_dict()
                                                                )
                                                                issued_runtime_lease = runtime_lease
                                                                issued_execution_lease_candidates = (
                                                                    execution_lease_candidates
                                                                )
                                                                issued_execution_lease_decision = (
                                                                    execution_lease_decision
                                                                )
                                                                issued_invocation_candidates = (
                                                                    invocation_candidates
                                                                )
                                                                issued_invocation_decision = (
                                                                    invocation_decision
                                                                )
                                                                issued_planning_provider_instance = (
                                                                    planning_provider_instance
                                                                )
                                                                episode_trace[
                                                                    "world_effect_runtime_lease"
                                                                ] = {
                                                                    "status": "valid",
                                                                    "lease": (
                                                                        runtime_lease_record
                                                                    ),
                                                                    "execution_lease_issued": True,
                                                                    "lease_armed": (
                                                                        runtime_lease.active
                                                                    ),
                                                                    "revocable": True,
                                                                    "revocation_conditions_bound": True,
                                                                    "dispatch_permit_issued": False,
                                                                    "tool_called": False,
                                                                    "handler_bound": False,
                                                                    "dispatch_enabled": False,
                                                                    "motion_authority": False,
                                                                    "execution_authority": False,
                                                                    "authority_scope": [],
                                                                }
                                                                episode_trace[
                                                                    "world_effect_tool_invocation_shadow"
                                                                ][
                                                                    "execution_lease_issued"
                                                                ] = True
                                                                print(
                                                                    "[world-effect-runtime-lease] "
                                                                    "VALID state="
                                                                    f"{runtime_lease.state} "
                                                                    "lease="
                                                                    f"{runtime_lease.lease.issued_lease_id} "
                                                                    "invalidations="
                                                                    f"{len(runtime_lease.lease.invalidation_bindings)} "
                                                                    "permit=false called=false "
                                                                    "dispatch=false authority=none",
                                                                    flush=True,
                                                                )
                                                            except Exception as runtime_lease_error:
                                                                episode_trace[
                                                                    "world_effect_runtime_lease"
                                                                ] = {
                                                                    "status": "invalid",
                                                                    "error": {
                                                                        "type": type(
                                                                            runtime_lease_error
                                                                        ).__name__,
                                                                        "message": str(
                                                                            runtime_lease_error
                                                                        ),
                                                                    },
                                                                    "execution_lease_issued": False,
                                                                    "lease_armed": False,
                                                                    "revocable": False,
                                                                    "dispatch_permit_issued": False,
                                                                    "tool_called": False,
                                                                    "handler_bound": False,
                                                                    "dispatch_enabled": False,
                                                                    "motion_authority": False,
                                                                    "execution_authority": False,
                                                                    "authority_scope": [],
                                                                }
                                                                print(
                                                                    "[world-effect-runtime-lease] "
                                                                    "INVALID "
                                                                    f"{type(runtime_lease_error).__name__}: "
                                                                    f"{runtime_lease_error} "
                                                                    "issued=false permit=false "
                                                                    "dispatch=false authority=none",
                                                                    flush=True,
                                                                )
                                                        else:
                                                            episode_trace[
                                                                "world_effect_runtime_lease"
                                                            ] = {
                                                                "status": "not_requested",
                                                                "reason": (
                                                                    "typed invocation decision did "
                                                                    "not propose an invocation"
                                                                ),
                                                                "execution_lease_issued": False,
                                                                "lease_armed": False,
                                                                "revocable": False,
                                                                "dispatch_permit_issued": False,
                                                                "tool_called": False,
                                                                "handler_bound": False,
                                                                "dispatch_enabled": False,
                                                                "motion_authority": False,
                                                                "execution_authority": False,
                                                                "authority_scope": [],
                                                            }
                                                    except Exception as invocation_error:
                                                        episode_trace["world_effect_tool_invocation_shadow"] = {
                                                            "status": "invalid",
                                                            "error": {
                                                                "type": type(
                                                                    invocation_error
                                                                ).__name__,
                                                                "message": str(
                                                                    invocation_error
                                                                ),
                                                            },
                                                            "latency_s": (
                                                                invocation_latency
                                                            ),
                                                            "image_digest": (
                                                                invocation_image_digest
                                                            ),
                                                            "invocation_validated": False,
                                                            "execution_lease_issued": False,
                                                            "tool_called": False,
                                                            "handler_bound": False,
                                                            "dispatch_enabled": False,
                                                            "motion_authority": False,
                                                            "execution_authority": False,
                                                            "authority_scope": [],
                                                        }
                                                        print(
                                                            "[world-effect-tool-invocation] "
                                                            "INVALID "
                                                            f"{type(invocation_error).__name__}: "
                                                            f"{invocation_error} "
                                                            "issued=false called=false "
                                                            "dispatch=false authority=none",
                                                            flush=True,
                                                        )
                                                else:
                                                    episode_trace["world_effect_tool_invocation_shadow"] = {
                                                        "status": "not_requested",
                                                        "reason": (
                                                            "execution lease decision did "
                                                            "not propose a lease"
                                                        ),
                                                        "invocation_validated": False,
                                                        "execution_lease_issued": False,
                                                        "tool_called": False,
                                                        "handler_bound": False,
                                                        "dispatch_enabled": False,
                                                        "motion_authority": False,
                                                        "execution_authority": False,
                                                        "authority_scope": [],
                                                    }
                                            except Exception as lease_error:
                                                episode_trace["world_effect_execution_lease_shadow"] = {
                                                    "status": "invalid",
                                                    "error": {
                                                        "type": type(
                                                            lease_error
                                                        ).__name__,
                                                        "message": str(
                                                            lease_error
                                                        ),
                                                    },
                                                    "latency_s": lease_latency,
                                                    "image_digest": (
                                                        lease_image_digest
                                                    ),
                                                    "configuration_validated": False,
                                                    "execution_lease_issued": False,
                                                    "tool_called": False,
                                                    "handler_bound": False,
                                                    "dispatch_enabled": False,
                                                    "motion_authority": False,
                                                    "execution_authority": False,
                                                    "authority_scope": [],
                                                }
                                                print(
                                                    "[world-effect-execution-lease] "
                                                    "INVALID "
                                                    f"{type(lease_error).__name__}: "
                                                    f"{lease_error} issued=false "
                                                    "dispatch=false authority=none",
                                                    flush=True,
                                                )
                                                episode_trace["world_effect_tool_invocation_shadow"] = {
                                                    "status": "not_requested",
                                                    "reason": (
                                                        "shadow execution-lease "
                                                        "proposal was invalid"
                                                    ),
                                                    "invocation_validated": False,
                                                    "execution_lease_issued": False,
                                                    "tool_called": False,
                                                    "handler_bound": False,
                                                    "dispatch_enabled": False,
                                                    "motion_authority": False,
                                                    "execution_authority": False,
                                                    "authority_scope": [],
                                                }
                                        else:
                                            episode_trace["world_effect_execution_lease_shadow"] = {
                                                "status": "not_requested",
                                                "reason": (
                                                    "semantic operation did not "
                                                    "propose a tool operation"
                                                ),
                                                "configuration_validated": False,
                                                "execution_lease_issued": False,
                                                "tool_called": False,
                                                "handler_bound": False,
                                                "dispatch_enabled": False,
                                                "motion_authority": False,
                                                "execution_authority": False,
                                                "authority_scope": [],
                                            }
                                    except Exception as operation_error:
                                        episode_trace[
                                            "world_effect_operation_plan_shadow"
                                        ] = {
                                            "status": "invalid",
                                            "error": {
                                                "type": type(
                                                    operation_error
                                                ).__name__,
                                                "message": str(
                                                    operation_error
                                                ),
                                            },
                                            "latency_s": operation_latency,
                                            "image_digest": (
                                                operation_image_digest
                                            ),
                                            "planning_provider_instantiated": False,
                                            "execution_provider_created": False,
                                            "handler_bound": False,
                                            "dispatch_enabled": False,
                                            "motion_authority": False,
                                            "execution_authority": False,
                                            "authority_scope": [],
                                        }
                                        print(
                                            "[world-effect-operation] INVALID "
                                            f"{type(operation_error).__name__}: "
                                            f"{operation_error} "
                                            "dispatch=false authority=none",
                                            flush=True,
                                        )
                                        episode_trace["world_effect_execution_lease_shadow"] = {
                                            "status": "not_requested",
                                            "reason": (
                                                "semantic operation proposal was "
                                                "invalid"
                                            ),
                                            "configuration_validated": False,
                                            "execution_lease_issued": False,
                                            "tool_called": False,
                                            "handler_bound": False,
                                            "dispatch_enabled": False,
                                            "motion_authority": False,
                                            "execution_authority": False,
                                            "authority_scope": [],
                                        }
                                else:
                                    episode_trace[
                                        "world_effect_operation_plan_shadow"
                                    ] = {
                                        "status": "not_requested",
                                        "reason": (
                                            "effect session did not select a "
                                            "provider"
                                        ),
                                        "planning_provider_instantiated": False,
                                        "execution_provider_created": False,
                                        "handler_bound": False,
                                        "dispatch_enabled": False,
                                        "motion_authority": False,
                                        "execution_authority": False,
                                        "authority_scope": [],
                                    }
                            except Exception as effect_session_error:
                                episode_trace["world_effect_session_shadow"] = {
                                    "status": "invalid",
                                    "error": {
                                        "type": type(effect_session_error).__name__,
                                        "message": str(effect_session_error),
                                    },
                                    "latency_s": effect_session_latency,
                                    "image_digest": effect_session_image_digest,
                                    "provider_instantiated": False,
                                    "motion_authority": False,
                                    "execution_authority": False,
                                    "authority_scope": [],
                                }
                                print(
                                    "[world-effect-session] INVALID "
                                    f"{type(effect_session_error).__name__}: "
                                    f"{effect_session_error} "
                                    "instantiated=false authority=none",
                                    flush=True,
                                )
                                episode_trace[
                                    "world_effect_operation_plan_shadow"
                                ] = {
                                    "status": "not_requested",
                                    "reason": "effect session was invalid",
                                    "planning_provider_instantiated": False,
                                    "execution_provider_created": False,
                                    "handler_bound": False,
                                    "dispatch_enabled": False,
                                    "motion_authority": False,
                                    "execution_authority": False,
                                    "authority_scope": [],
                                }
                        else:
                            episode_trace["world_effect_session_shadow"] = {
                                "status": "not_requested",
                                "reason": (
                                    "goal activation did not select a world "
                                    "goal/capability pair"
                                ),
                                "activation_decision": (
                                    goal_activation_decision.to_dict()
                                ),
                                "provider_instantiated": False,
                                "motion_authority": False,
                                "execution_authority": False,
                                "authority_scope": [],
                            }
                    except Exception as goal_activation_error:
                        episode_trace["world_goal_activation_shadow"] = {
                            "status": "invalid",
                            "motion_authority": False,
                            "execution_authority": False,
                            "authority_scope": [],
                            "error": {
                                "type": type(goal_activation_error).__name__,
                                "message": str(goal_activation_error),
                            },
                        }
                        print(
                            "[world-goal-activation] INVALID "
                            f"{type(goal_activation_error).__name__}: "
                            f"{goal_activation_error} authority=none",
                            flush=True,
                        )
                        episode_trace["world_effect_session_shadow"] = {
                            "status": "not_requested",
                            "reason": "goal activation was invalid",
                            "provider_instantiated": False,
                            "motion_authority": False,
                            "execution_authority": False,
                            "authority_scope": [],
                        }
                else:
                    episode_trace["world_goal_activation_shadow"] = {
                        "status": "not_admitted",
                        "motion_authority": False,
                        "execution_authority": False,
                        "authority_scope": [],
                        "reason": (
                            "goal graph task-membership, predicate, or scene "
                            "scope admission did not pass"
                        ),
                    }
                    episode_trace["world_effect_session_shadow"] = {
                        "status": "not_requested",
                        "reason": "world goal graph was not admitted",
                        "provider_instantiated": False,
                        "motion_authority": False,
                        "execution_authority": False,
                        "authority_scope": [],
                    }
            except Exception as goal_graph_error:
                if episode_trace[
                    "world_scope_membership_audit_shadow"
                ].get("status") == "pending":
                    episode_trace["world_scope_membership_audit_shadow"] = {
                        "status": "invalid",
                        "reason": "goal graph or membership audit failed",
                        "error": {
                            "type": type(goal_graph_error).__name__,
                            "message": str(goal_graph_error),
                        },
                        "feasibility_is_membership_authority": False,
                        "motion_authority": False,
                        "execution_authority": False,
                        "authority_scope": [],
                    }
                episode_trace["world_goal_graph_shadow"] = {
                    "status": "invalid",
                    "contract_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
                    "inventory_contract_version": (
                        SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION
                    ),
                    "motion_authority": False,
                    "authority_scope": [],
                    "instruction": args_cli.instruction,
                    "scene_inventory_digest": semantic_scene_inventory_digest,
                    "scene_inventory": semantic_scene_inventory,
                    "predicate_evaluator_advertisement": (
                        world_predicate_evaluator_advertisement
                    ),
                    "error": {
                        "type": type(goal_graph_error).__name__,
                        "message": str(goal_graph_error),
                    },
                }
                print(
                    "[world-goal-graph] INVALID "
                    f"{type(goal_graph_error).__name__}: {goal_graph_error} "
                    "authority=none",
                    flush=True,
                )
            if episode_trace[
                "world_effect_operation_plan_shadow"
            ].get("status") == "pending":
                episode_trace[
                    "world_effect_operation_plan_shadow"
                ] = {
                    "status": "not_requested",
                    "reason": (
                        "no selected world-effect provider reached the "
                        "planning-only operation boundary"
                    ),
                    "planning_provider_instantiated": False,
                    "execution_provider_created": False,
                    "handler_bound": False,
                    "dispatch_enabled": False,
                    "motion_authority": False,
                    "execution_authority": False,
                    "authority_scope": [],
                }
            if episode_trace[
                "world_effect_execution_lease_shadow"
            ].get("status") == "pending":
                episode_trace["world_effect_execution_lease_shadow"] = {
                    "status": "not_requested",
                    "reason": (
                        "no semantic provider operation reached the fresh "
                        "execution-lease boundary"
                    ),
                    "configuration_validated": False,
                    "execution_lease_issued": False,
                    "tool_called": False,
                    "handler_bound": False,
                    "dispatch_enabled": False,
                    "motion_authority": False,
                    "execution_authority": False,
                    "authority_scope": [],
                }
            if episode_trace[
                "world_effect_tool_invocation_shadow"
            ].get("status") == "pending":
                episode_trace["world_effect_tool_invocation_shadow"] = {
                    "status": "not_requested",
                    "reason": (
                        "no validated shadow execution lease reached the "
                        "typed invocation boundary"
                    ),
                    "invocation_validated": False,
                    "execution_lease_issued": False,
                    "tool_called": False,
                    "handler_bound": False,
                    "dispatch_enabled": False,
                    "motion_authority": False,
                    "execution_authority": False,
                    "authority_scope": [],
                }
            if episode_trace[
                "world_effect_runtime_lease"
            ].get("status") == "pending":
                episode_trace["world_effect_runtime_lease"] = {
                    "status": "not_requested",
                    "reason": (
                        "no validated typed invocation reached the runtime "
                        "lease issuance boundary"
                    ),
                    "execution_lease_issued": False,
                    "lease_armed": False,
                    "revocable": False,
                    "dispatch_permit_issued": False,
                    "tool_called": False,
                    "handler_bound": False,
                    "dispatch_enabled": False,
                    "motion_authority": False,
                    "execution_authority": False,
                    "authority_scope": [],
                }
            _write_trace(trace_path, episode_trace)

        if args_cli.guarded_world_effect_execution:
            guarded_trace = episode_trace["world_effect_guarded_dispatch"]
            try:
                required_handoff = {
                    "runtime_lease": issued_runtime_lease,
                    "lease_candidates": issued_execution_lease_candidates,
                    "lease_decision": issued_execution_lease_decision,
                    "invocation_candidates": issued_invocation_candidates,
                    "invocation_decision": issued_invocation_decision,
                    "planning_provider": issued_planning_provider_instance,
                    "motion_registry": motion_executor_registry,
                    "actuator_registry": actuator_executor_registry,
                }
                missing_handoff = sorted(
                    name for name, value in required_handoff.items() if value is None
                )
                if missing_handoff:
                    raise RuntimeError(
                        "guarded execution handoff is incomplete: "
                        f"{missing_handoff}"
                    )
                runtime_lease = issued_runtime_lease
                execution_lease_candidates = issued_execution_lease_candidates
                execution_lease_decision = issued_execution_lease_decision
                invocation_candidates = issued_invocation_candidates
                invocation_decision = issued_invocation_decision
                planning_provider_instance = issued_planning_provider_instance
                assert runtime_lease is not None
                assert execution_lease_candidates is not None
                assert execution_lease_decision is not None
                assert invocation_candidates is not None
                assert invocation_decision is not None
                assert planning_provider_instance is not None
                assert motion_executor_registry is not None
                assert actuator_executor_registry is not None

                lease_candidate = next(
                    item
                    for item in execution_lease_candidates.candidates
                    if item.candidate_id == execution_lease_decision.candidate_id
                )
                invocation_candidate = next(
                    item
                    for item in invocation_candidates.candidates
                    if item.candidate_id == invocation_decision.candidate_id
                )
                runtime_motion_spec = next(
                    (
                        item
                        for item in motion_executor_registry.specs()
                        if item.executor_id == runtime_lease.lease.tool_id
                    ),
                    None,
                )
                if runtime_motion_spec is None:
                    raise RuntimeError(
                        "issued tool has no active runtime motion executor"
                    )
                if runtime_motion_spec.invocation_schema is None:
                    raise RuntimeError(
                        "active runtime motion executor has no invocation schema"
                    )
                baseline_membership_ids = {
                    str(item["entity_id"])
                    for item in semantic_scene_inventory.get("entities", [])
                    if isinstance(item, Mapping)
                    and isinstance(item.get("entity_id"), str)
                }
                baseline_tracked_positions_m = {
                    entity_id: preflight_tracked_positions_m[entity_id]
                    for entity_id in lease_candidate.operation_target_entity_ids
                    if entity_id in preflight_tracked_positions_m
                }
                fresh_dispatch_state = _state(env, initial_object_z)
                fresh_tracked_positions_m = _tracked_entity_positions_m(
                    env, lease_candidate.operation_target_entity_ids
                )
                fresh_events = _guarded_dispatch_invalidation_events(
                    runtime_lease=runtime_lease,
                    lease_candidate=lease_candidate,
                    invocation_candidate=invocation_candidate,
                    invocation_decision=invocation_decision,
                    baseline_membership_ids=baseline_membership_ids,
                    current_provider_instance_id=(
                        planning_provider_instance.instance_id
                    ),
                    state=fresh_dispatch_state,
                    baseline_tracked_positions_m=(
                        baseline_tracked_positions_m
                    ),
                    current_tracked_positions_m=fresh_tracked_positions_m,
                )
                fresh_evidence = build_fresh_dispatch_evidence(
                    runtime_lease=runtime_lease,
                    source="live_simulator_rgbd_state",
                    observation={
                        "controlled_frame": {
                            "position_m": fresh_dispatch_state[
                                "eef_gripper_base_xyz"
                            ],
                            "quaternion_wxyz": fresh_dispatch_state[
                                "eef_gripper_base_quaternion_wxyz"
                            ],
                        },
                        "interaction_frame": fresh_dispatch_state.get(
                            "actuator_contact_geometry"
                        ),
                        "rgbd_scene_geometry": fresh_dispatch_state.get(
                            "rgbd_scene_geometry"
                        ),
                        "tracked_entity_positions_m": (
                            fresh_tracked_positions_m
                        ),
                    },
                    invalidation_events=fresh_events,
                )
                selected_orientation_axis = next(
                    (
                        item
                        for item in invocation_candidate.orientation_axes
                        if item.alignment_id
                        == invocation_decision.orientation_alignment_id
                    ),
                    None,
                )
                guarded_rgbd_axis_references = (
                    {
                        selected_orientation_axis.entity_id: np.asarray(
                            selected_orientation_axis.axis_robot_root,
                            dtype=np.float64,
                        )
                    }
                    if selected_orientation_axis is not None
                    else {}
                )
                issued_geometry_by_id = {
                    item.entity_id: item.geometry
                    for item in lease_candidate.geometry_bindings
                }
                obstacle_geometry_by_id = interaction_obstacle_geometry(
                    issued_geometry_by_id,
                    interaction_target_entity_id=(
                        _interaction_target_entity_id(
                            invocation_candidate,
                            invocation_decision,
                        )
                    ),
                )
                handler_registry = RuntimeWorldEffectHandlerRegistry()
                monitored_events: list[dict[str, Any]] = []

                def guarded_motion_handler(
                    invocation_arguments: Mapping[str, Any],
                    tool_configuration: Mapping[str, Any],
                    active_lease: Any,
                ) -> Mapping[str, Any]:
                    nonlocal obs, terminal
                    target_position = torch.tensor(
                        invocation_arguments["target_position_m"],
                        dtype=torch.float32,
                    )
                    target_quaternion = torch.tensor(
                        invocation_arguments["target_quaternion_wxyz"],
                        dtype=torch.float32,
                    )
                    initial_action = _current_robot_joint_action(
                        env,
                        gripper_closed_fraction=float(
                            fresh_dispatch_state["gripper_closed_fraction"]
                        ),
                    )

                    def observe_guarded_path_clearance() -> tuple[float, str]:
                        interaction = _actuator_contact_geometry(
                            env, _eef_position(env)
                        )
                        current_position = interaction.get(
                            "contact_center_xyz_m"
                        )
                        target_interaction_position = (
                            invocation_decision.grounding_assessment.get(
                                "realized_interaction_position_m"
                            )
                        )
                        if not isinstance(current_position, (list, tuple)) or not isinstance(
                            target_interaction_position, (list, tuple)
                        ):
                            raise RuntimeError(
                                "guarded clearance observer lacks a live interaction frame"
                            )
                        clearance, _ = _interaction_path_clearance_m(
                            current_position,
                            target_interaction_position,
                            obstacle_geometry_by_id,
                        )
                        if not math.isfinite(clearance):
                            raise RuntimeError(
                                "guarded clearance observer produced no finite path clearance"
                            )
                        return (
                            clearance,
                            "sim6.live_interaction_frame_plus_issued_rgbd_geometry",
                        )

                    def observe_guarded_orientation(
                        entity_id: str, reference_axis: np.ndarray
                    ) -> tuple[float, str, Mapping[str, Any]]:
                        scene_geometry = _rgbd_scene_geometry_observation(env)
                        current_geometry = next(
                            (
                                item
                                for item in scene_geometry.get("geometries", [])
                                if isinstance(item, Mapping)
                                and item.get("runtime_id") == entity_id
                            ),
                            None,
                        )
                        if not isinstance(current_geometry, Mapping):
                            raise ValueError(
                                f"RGB-D orientation target {entity_id!r} is not visible"
                            )
                        error_deg = _axis_set_error_deg(
                            reference_axis,
                            current_geometry.get("oriented_footprint_axes_base"),
                        )
                        if error_deg is None:
                            raise ValueError(
                                "RGB-D oriented-footprint axis set is unavailable"
                            )
                        return (
                            error_deg,
                            "rgbd.oriented_footprint_axis_set_robot_root",
                            {
                                "entity_id": entity_id,
                                "reference_axis_robot_root": (
                                    reference_axis.tolist()
                                ),
                                "observed_axes_robot_root": current_geometry.get(
                                    "oriented_footprint_axes_base"
                                ),
                                "orientation_error_deg": error_deg,
                            },
                        )

                    def monitor_lease() -> dict[str, Any] | None:
                        if not active_lease.active:
                            event = {
                                "condition_id": "runtime.maximum_duration_elapsed",
                                "reason": "issued runtime lease is no longer active",
                                "lease_state": active_lease.state,
                            }
                            monitored_events.append(event)
                            return {**event, "converged": False}
                        monitor_state = _state(env, initial_object_z)
                        monitor_tracked_positions_m = _tracked_entity_positions_m(
                            env, lease_candidate.operation_target_entity_ids
                        )
                        events = _guarded_dispatch_invalidation_events(
                            runtime_lease=active_lease,
                            lease_candidate=lease_candidate,
                            invocation_candidate=invocation_candidate,
                            invocation_decision=invocation_decision,
                            baseline_membership_ids=baseline_membership_ids,
                            current_provider_instance_id=(
                                planning_provider_instance.instance_id
                            ),
                            state=monitor_state,
                            baseline_tracked_positions_m=(
                                baseline_tracked_positions_m
                            ),
                            current_tracked_positions_m=(
                                monitor_tracked_positions_m
                            ),
                        )
                        if not events:
                            return None
                        event = events[0]
                        active_lease.observe_invalidation(
                            event.condition_id, event.evidence
                        )
                        serialized = event.to_dict()
                        monitored_events.append(serialized)
                        return {
                            **serialized,
                            "lease_state": active_lease.state,
                            "converged": False,
                        }

                    (
                        obs,
                        terminal,
                        final_action,
                        motion_report,
                    ) = _move_eef_to_target(
                        env,
                        obs,
                        initial_action,
                        target_position,
                        target_quaternion,
                        phase=(
                            "world_effect:"
                            f"{lease_candidate.purpose}"
                        ),
                        gripper_closed=bool(
                            float(initial_action[0, 7].detach().cpu()) > 0.5
                        ),
                        initial_object_z=initial_object_z,
                        executor_config=dict(tool_configuration),
                        tracked_position_references_m=(
                            baseline_tracked_positions_m
                        ),
                        rgbd_axis_references=guarded_rgbd_axis_references,
                        tracked_orientation_observer=(
                            observe_guarded_orientation
                        ),
                        observed_clearance_observer=(
                            observe_guarded_path_clearance
                        ),
                        checkpoint_callback=None,
                        early_stop_callback=monitor_lease,
                    )
                    post_state = _state(env, initial_object_z)
                    post_tracked_positions_m = _tracked_entity_positions_m(
                        env, lease_candidate.operation_target_entity_ids
                    )
                    post_events = _guarded_dispatch_invalidation_events(
                        runtime_lease=active_lease,
                        lease_candidate=lease_candidate,
                        invocation_candidate=invocation_candidate,
                        invocation_decision=invocation_decision,
                        baseline_membership_ids=baseline_membership_ids,
                        current_provider_instance_id=(
                            planning_provider_instance.instance_id
                        ),
                        state=post_state,
                        baseline_tracked_positions_m=(
                            baseline_tracked_positions_m
                        ),
                        current_tracked_positions_m=post_tracked_positions_m,
                    )
                    if post_events and active_lease.active:
                        event = post_events[0]
                        active_lease.observe_invalidation(
                            event.condition_id, event.evidence
                        )
                        monitored_events.append(event.to_dict())
                    if (
                        not motion_report.get("converged")
                        and active_lease.active
                    ):
                        active_lease.revoke(
                            reason="dispatch.motion_not_converged",
                            evidence={
                                "target_error_after_m": motion_report.get(
                                    "target_error_after_m"
                                ),
                                "orientation_error_after_deg": motion_report.get(
                                    "orientation_error_after_deg"
                                ),
                                "terminal": terminal,
                            },
                        )
                    post_frame = _single_exterior_frame(obs)
                    cv2.imwrite(
                        str(
                            args_cli.artifact_dir
                            / "01_guarded_post_dispatch.jpg"
                        ),
                        cv2.cvtColor(post_frame, cv2.COLOR_RGB2BGR),
                    )
                    return {
                        "executor_id": runtime_motion_spec.executor_id,
                        "executor_tool_name": runtime_motion_spec.tool_name,
                        "motion_report": motion_report,
                        "monitored_invalidation_events": monitored_events,
                        "terminal": terminal,
                        "final_action": final_action.detach().cpu().tolist(),
                        "post_dispatch_observation": {
                            "eef_gripper_base_xyz": post_state[
                                "eef_gripper_base_xyz"
                            ],
                            "eef_gripper_base_quaternion_wxyz": post_state[
                                "eef_gripper_base_quaternion_wxyz"
                            ],
                            "actuator_contact_geometry": post_state.get(
                                "actuator_contact_geometry"
                            ),
                            "rgbd_scene_geometry": post_state.get(
                                "rgbd_scene_geometry"
                            ),
                            "tracked_entity_positions_m": (
                                post_tracked_positions_m
                            ),
                            "current_contact": post_state.get("current_contact"),
                        },
                        "requires_model_replan": True,
                        "replan_reason": (
                            "operation_completed"
                            if motion_report.get("converged")
                            else "operation_invalidated_or_not_converged"
                        ),
                    }

                handler_registry.register(
                    runtime_lease.lease.tool_id, guarded_motion_handler
                )
                dispatcher = GuardedWorldEffectDispatcher(
                    runtime_lease=runtime_lease,
                    handlers=handler_registry,
                    maximum_evidence_age_s=(
                        args_cli.world_effect_dispatch_evidence_max_age_s
                    ),
                    maximum_permit_lifetime_s=(
                        args_cli.world_effect_dispatch_permit_lifetime_s
                    ),
                )
                permit = dispatcher.mint_permit(fresh_evidence)
                episode_trace["world_effect_guarded_dispatch"] = {
                    "status": "permitted",
                    "fresh_evidence": fresh_evidence.to_dict(),
                    "permit": permit.to_dict(),
                    "fresh_evidence_validated": True,
                    "dispatch_permit_issued": True,
                    "dispatch_performed": False,
                    "handler_bound": False,
                    "tool_called": False,
                    "requires_model_replan": False,
                    "dispatch_enabled": True,
                    "motion_authority": True,
                    "execution_authority": True,
                    "authority_scope": ["invoke_exact_tool_once"],
                }
                _write_trace(trace_path, episode_trace)
                print(
                    "[world-effect-guarded-dispatch] PERMITTED "
                    f"tool={permit.tool_id} evidence={permit.evidence_id} "
                    "single_use=true",
                    flush=True,
                )
                dispatch_outcome = dispatcher.dispatch(permit)
                outcome_record = dispatch_outcome.to_dict()
                episode_trace["world_effect_guarded_dispatch"] = {
                    "status": "valid",
                    "fresh_evidence": fresh_evidence.to_dict(),
                    "permit": permit.to_dict(),
                    "outcome": outcome_record,
                    "runtime_lease_after": runtime_lease.to_dict(),
                    "fresh_evidence_validated": True,
                    "dispatch_permit_issued": True,
                    "dispatch_performed": True,
                    "handler_bound": True,
                    "tool_called": True,
                    "requires_model_replan": True,
                    "dispatch_enabled": False,
                    "motion_authority": False,
                    "execution_authority": False,
                    "authority_scope": [],
                }
                episode_trace["world_effect_runtime_lease"] = {
                    **episode_trace["world_effect_runtime_lease"],
                    "lease": runtime_lease.to_dict(),
                    "lease_armed": runtime_lease.active,
                }
                handler_result = outcome_record["handler_result"]
                motion_report = handler_result["motion_report"]
                episode_trace["stages"].append(
                    {
                        "phase": f"world_effect:{lease_candidate.purpose}",
                        "source": "guarded_world_effect_dispatch",
                        "tool_id": runtime_lease.lease.tool_id,
                        "permit_id": permit.permit_id,
                        "motion_report": motion_report,
                    }
                )
                sequence_trace = episode_trace["world_effect_sequence"]
                sequence_trace["status"] = "running"
                sequence_trace["operations"].append(
                    {
                        "operation_index": 1,
                        "planning_source": "initial_world_effect_pipeline",
                        "selected_goal_id": goal_activation_decision.goal_id,
                        "tool_family": lease_candidate.tool_family,
                        "tool_id": runtime_lease.lease.tool_id,
                        "purpose": lease_candidate.purpose,
                        "dispatch": episode_trace[
                            "world_effect_guarded_dispatch"
                        ],
                    }
                )
                sequence_trace["completed_operation_count"] = 1
                _write_trace(trace_path, episode_trace)
                print(
                    "[world-effect-guarded-dispatch] OPERATION_COMPLETE "
                    "index=1 "
                    f"converged={bool(motion_report.get('converged'))} "
                    f"lease={runtime_lease.state} "
                    f"iterations={len(motion_report.get('iterations', []))} "
                    "replan=true authority=none",
                    flush=True,
                )

                selected_goal_id = goal_activation_decision.goal_id
                if selected_goal_id is None:
                    raise RuntimeError(
                        "guarded dispatch lacks the selected world goal id"
                    )
                current_operation_index = 1
                sequence_stop_reason: str | None = None
                while True:
                    continuation_state = _state(env, initial_object_z)
                    continuation_state["entity_physical_evidence"] = (
                        _runtime_scene_entity_physical_evidence(
                            env, continuation_state
                        )
                    )
                    continuation_inventory = semantic_scene_inventory_from_state(
                        continuation_state
                    )
                    continuation_frame = _single_exterior_frame(obs)
                    progress = assess_world_effect_progress(
                        graph=goal_graph,
                        membership_lease=goal_graph_membership_lease,
                        selected_goal_id=selected_goal_id,
                        predicate_registry=world_predicate_evaluator_registry,
                        capability_registry=world_capability_registry,
                        inventory=continuation_inventory,
                        operation_index=current_operation_index,
                    )
                    sequence_trace["progress_observations"].append(
                        progress.to_dict()
                    )
                    _write_trace(trace_path, episode_trace)
                    print(
                        "[world-effect-progress] FRESH "
                        f"after={current_operation_index} "
                        f"goal={selected_goal_id} status={progress.status} "
                        f"satisfied={progress.selected_goal_satisfied} "
                        "authority=none",
                        flush=True,
                    )
                    if not progress.may_plan_another_operation:
                        sequence_stop_reason = progress.status
                        break
                    next_operation_index = current_operation_index + 1
                    if not world_effect_sequence_budget.allows(
                        next_operation_index
                    ):
                        sequence_stop_reason = "operation_budget_exhausted"
                        break
                    continuation_entity_ids = {
                        str(item["entity_id"])
                        for item in continuation_inventory.get("entities", [])
                        if isinstance(item, Mapping)
                        and isinstance(item.get("entity_id"), str)
                    }
                    continuation_tracked_positions = (
                        _tracked_entity_positions_m(
                            env, continuation_entity_ids
                        )
                    )
                    continuation_bundle = (
                        _plan_guarded_world_effect_continuation(
                            coach=coach,
                            instruction=args_cli.instruction,
                            frame=continuation_frame,
                            graph=goal_graph,
                            membership_lease=goal_graph_membership_lease,
                            inventory=continuation_inventory,
                            predicate_registry=(
                                world_predicate_evaluator_registry
                            ),
                            capability_registry=world_capability_registry,
                            capability_advertisement=(
                                world_capability_advertisement
                            ),
                            provider_registry=(
                                world_effect_provider_registry
                            ),
                            runtime_effect_tools=runtime_effect_tools,
                            trackable_object_ids=trackable_object_ids,
                            runtime_state=continuation_state,
                            maximum_duration_s=(
                                args_cli.world_effect_runtime_lease_duration_s
                            ),
                            operation_index=next_operation_index,
                        )
                    )
                    operation_record: dict[str, Any] = {
                        "operation_index": next_operation_index,
                        "planning_source": (
                            "fresh_post_effect_model_replan"
                        ),
                        "planning": continuation_bundle["trace"],
                        "planning_status": continuation_bundle["status"],
                    }
                    sequence_trace["operations"].append(operation_record)
                    _write_trace(trace_path, episode_trace)
                    if (
                        continuation_bundle["status"]
                        != "runtime_lease_issued"
                    ):
                        sequence_stop_reason = str(
                            continuation_bundle["status"]
                        )
                        print(
                            "[world-effect-continuation] NOT_ADMITTED "
                            f"index={next_operation_index} "
                            f"status={sequence_stop_reason} authority=none",
                            flush=True,
                        )
                        break
                    selected_goal_id = (
                        continuation_bundle["activation_decision"].goal_id
                    )
                    if selected_goal_id is None:
                        raise RuntimeError(
                            "continuation lease lacks a selected world goal"
                        )
                    print(
                        "[world-effect-continuation] LEASE_ISSUED "
                        f"index={next_operation_index} "
                        f"goal={selected_goal_id} "
                        "authority=single_use_pending_fresh_evidence",
                        flush=True,
                    )
                    obs, continuation_terminal, continuation_dispatch = (
                        _dispatch_guarded_world_effect_continuation(
                            env=env,
                            obs=obs,
                            initial_object_z=initial_object_z,
                            bundle=continuation_bundle,
                            baseline_inventory=continuation_inventory,
                            baseline_tracked_positions_m=(
                                continuation_tracked_positions
                            ),
                            motion_registry=motion_executor_registry,
                            actuator_registry=actuator_executor_registry,
                            maximum_evidence_age_s=(
                                args_cli.world_effect_dispatch_evidence_max_age_s
                            ),
                            maximum_permit_lifetime_s=(
                                args_cli.world_effect_dispatch_permit_lifetime_s
                            ),
                            artifact_dir=args_cli.artifact_dir,
                            operation_index=next_operation_index,
                        )
                    )
                    operation_record["selected_goal_id"] = selected_goal_id
                    operation_record["dispatch"] = continuation_dispatch
                    operation_record["tool_family"] = continuation_dispatch[
                        "tool_family"
                    ]
                    operation_record["tool_id"] = continuation_dispatch[
                        "tool_id"
                    ]
                    operation_record["purpose"] = continuation_dispatch[
                        "purpose"
                    ]
                    sequence_trace["completed_operation_count"] = (
                        next_operation_index
                    )
                    dispatch_handler_result = continuation_dispatch["outcome"][
                        "handler_result"
                    ]
                    execution_report = dispatch_handler_result.get(
                        "execution_report", {}
                    )
                    episode_trace["stages"].append(
                        {
                            "phase": (
                                "world_effect:"
                                f"{continuation_dispatch['purpose']}"
                            ),
                            "source": (
                                "guarded_world_effect_continuation"
                            ),
                            "tool_id": continuation_dispatch["tool_id"],
                            "tool_family": continuation_dispatch[
                                "tool_family"
                            ],
                            "permit_id": continuation_dispatch["permit"][
                                "permit_id"
                            ],
                            "execution_report": execution_report,
                        }
                    )
                    current_operation_index = next_operation_index
                    _write_trace(trace_path, episode_trace)
                    print(
                        "[world-effect-continuation] OPERATION_COMPLETE "
                        f"index={current_operation_index} "
                        f"family={continuation_dispatch['tool_family']} "
                        f"lease={continuation_dispatch['runtime_lease_after']['state']} "
                        "replan=true authority=none",
                        flush=True,
                    )
                    if continuation_terminal:
                        sequence_stop_reason = "environment_terminal"
                        break

                sequence_trace["status"] = "stopped"
                sequence_trace["stop_reason"] = sequence_stop_reason
                sequence_trace["task_completion_claimed"] = False
                episode_trace["status"] = "guarded_world_effect_sequence_stopped"
                episode_trace["guarded_world_effect_result"] = {
                    "complete": True,
                    "dispatch_performed": True,
                    "operation_count": sequence_trace[
                        "completed_operation_count"
                    ],
                    "sequence_stop_reason": sequence_stop_reason,
                    "selected_goal_completed": (
                        sequence_stop_reason == "selected_goal_completed"
                    ),
                    "task_completion_claimed": False,
                    "fresh_complete_graph_required": True,
                    "requires_model_replan": True,
                    "motion_stage_count": len(episode_trace["stages"]),
                    "demonstration_loaded": False,
                }
                _write_trace(trace_path, episode_trace)
                print(
                    "[world-effect-sequence] STOPPED "
                    f"operations={sequence_trace['completed_operation_count']} "
                    f"reason={sequence_stop_reason} "
                    "task_complete=false authority=none",
                    flush=True,
                )
                print(f"Trace: {trace_path}", flush=True)
                return 0
            except Exception as guarded_error:
                episode_trace["world_effect_guarded_dispatch"] = {
                    **guarded_trace,
                    "status": "invalid",
                    "error": {
                        "type": type(guarded_error).__name__,
                        "message": str(guarded_error),
                    },
                    "fresh_evidence_validated": False,
                    "dispatch_performed": False,
                    "requires_model_replan": True,
                    "dispatch_enabled": False,
                    "motion_authority": False,
                    "execution_authority": False,
                    "authority_scope": [],
                }
                episode_trace["status"] = "guarded_first_operation_not_admitted"
                _write_trace(trace_path, episode_trace)
                print(
                    "[world-effect-guarded-dispatch] INVALID "
                    f"{type(guarded_error).__name__}: {guarded_error} "
                    "authority=none",
                    flush=True,
                )
                print(f"Trace: {trace_path}", flush=True)
                return 2

        if args_cli.shadow_plan_only:
            required_shadow_statuses = {
                "world_intent_shadow": episode_trace[
                    "world_intent_shadow"
                ].get("status"),
                "world_goal_graph_shadow": episode_trace[
                    "world_goal_graph_shadow"
                ].get("status"),
                "world_scope_membership_audit_shadow": episode_trace[
                    "world_scope_membership_audit_shadow"
                ].get("status"),
                "world_goal_activation_shadow": episode_trace[
                    "world_goal_activation_shadow"
                ].get("status"),
                "world_effect_session_shadow": episode_trace[
                    "world_effect_session_shadow"
                ].get("status"),
                "world_effect_operation_plan_shadow": episode_trace[
                    "world_effect_operation_plan_shadow"
                ].get("status"),
                "world_effect_execution_lease_shadow": episode_trace[
                    "world_effect_execution_lease_shadow"
                ].get("status"),
                "world_effect_tool_invocation_shadow": episode_trace[
                    "world_effect_tool_invocation_shadow"
                ].get("status"),
                "world_effect_runtime_lease": episode_trace[
                    "world_effect_runtime_lease"
                ].get("status"),
            }
            optional_handoff_components = {
                "world_effect_session_shadow",
                "world_effect_operation_plan_shadow",
                "world_effect_execution_lease_shadow",
                "world_effect_tool_invocation_shadow",
                "world_effect_runtime_lease",
            }
            shadow_complete = all(
                status == "valid"
                for name, status in required_shadow_statuses.items()
                if name not in optional_handoff_components
            ) and all(
                required_shadow_statuses[name] in {"valid", "not_requested"}
                for name in optional_handoff_components
            )
            episode_trace["status"] = (
                "shadow_plan_complete"
                if shadow_complete
                else "shadow_plan_not_admitted"
            )
            episode_trace["shadow_plan_only_result"] = {
                "complete": shadow_complete,
                "component_statuses": required_shadow_statuses,
                "motion_stage_count": len(episode_trace["stages"]),
                "feasibility_called": False,
                "demonstration_loaded": False,
                "execution_provider_created": False,
                "planning_provider_instantiated": episode_trace[
                    "world_effect_operation_plan_shadow"
                ].get("planning_provider_instantiated", False),
                "execution_lease_issued": episode_trace[
                    "world_effect_runtime_lease"
                ].get("execution_lease_issued", False),
                "lease_armed": episode_trace[
                    "world_effect_runtime_lease"
                ].get("lease_armed", False),
                "invocation_validated": episode_trace[
                    "world_effect_tool_invocation_shadow"
                ].get("invocation_validated", False),
                "tool_called": False,
                "handler_bound": False,
                "dispatch_enabled": False,
                "motion_authority": False,
                "execution_authority": False,
            }
            _write_trace(trace_path, episode_trace)
            print(
                "[shadow-plan-only] "
                f"status={episode_trace['status']} "
                "motion_stages=0 feasibility=skipped "
                "demonstration=not_loaded execution_providers=not_created "
                "lease="
                f"{episode_trace['world_effect_runtime_lease'].get('status')} "
                "dispatch=false",
                flush=True,
            )
            print(f"Trace: {trace_path}", flush=True)
            return 0 if shadow_complete else 2

        preflight_frame, preflight_depth_summary = _rgbd_checkpoint_frame(
            env, frame
        )
        capability_evidence = _runtime_task_capability_evidence(
            env,
            preflight_state,
            motion_executor_registry,
            actuator_executor_registry,
        )
        scene, latency, digest = _choose_observation_bound_task_feasibility(
            motion_tool_provider,
            frame=preflight_frame,
            state=preflight_state,
            capability_evidence=capability_evidence,
            critic_context=_critic_context(critic_memory, "global"),
        )
        model_calls += 1
        digests.append(digest)
        tests["model"] = True
        _test_line(
            2,
            "ER 2 API + feasibility tool",
            True,
            f"{latency:.2f}s image={digest}",
        )
        scene_ok = bool(scene.get("scene_ok")) and bool(
            scene.get("movable_object_visible")
        ) and bool(
            scene.get("target_receptacle_visible")
        )
        tests["scene"] = scene_ok
        _test_line(3, "visual scene grounding", scene_ok, str(scene.get("assessment", "")))
        episode_trace["scene_decision"] = scene
        episode_trace["task_feasibility_preflight"] = {
            "observation_bound": True,
            "motion_authority": bool(scene.get("motion_authorized")),
            "capability_evidence": capability_evidence,
            "rgbd_summary": preflight_depth_summary,
            "decision": scene,
        }
        episode_trace["motion_checkpoints"] = []
        _write_trace(trace_path, episode_trace)
        print(
            "[feasibility preflight] "
            f"reachability={scene.get('reachability')} "
            f"grasp={scene.get('grasp_feasibility')} "
            f"payload={scene.get('payload_feasibility')} "
            f"task={scene.get('task_feasibility')} "
            f"motion_authorized={scene.get('motion_authorized')}",
            flush=True,
        )
        if not bool(scene.get("motion_authorized")):
            raise RuntimeError(
                "Task feasibility preflight withheld motion authority: "
                f"blocking={scene.get('blocking_reasons')} "
                f"required_evidence={scene.get('required_runtime_evidence')}"
            )

        def motion_checkpoint_handler(
            checkpoint_obs: dict[str, Any], checkpoint: dict[str, Any]
        ) -> dict[str, Any]:
            nonlocal model_calls
            # Recovery/status checkpoints may report a measured condition
            # without proposing a destination. Bind those calls to the fresh
            # measured end-effector pose so the model can hold, abort, or apply
            # a bounded correction through the same executor protocol.
            previous_outcome: dict[str, Any] | None = checkpoint.get(
                "previous_motion_tool_outcome"
            )
            max_attempts = max(1, int(args_cli.motion_checkpoint_replans))
            decision: dict[str, Any] = {}
            for attempt in range(max_attempts):
                checkpoint_state = _state(env, initial_object_z)
                checkpoint_frame = _single_exterior_frame(checkpoint_obs)
                checkpoint_frame, depth_summary = _rgbd_checkpoint_frame(
                    env, checkpoint_frame
                )
                checkpoint_index = len(episode_trace["motion_checkpoints"])
                frame_name = f"motion_checkpoint_{checkpoint_index:03d}.jpg"
                cv2.imwrite(
                    str(args_cli.artifact_dir / frame_name),
                    cv2.cvtColor(checkpoint_frame, cv2.COLOR_RGB2BGR),
                )
                motion_context = {
                    **checkpoint,
                    "rgbd": depth_summary,
                    "replan_attempt": attempt + 1,
                    "maximum_replan_attempts": max_attempts,
                    "previous_motion_tool_outcome": previous_outcome,
                }
                motion_context.setdefault(
                    "current_target_xyz_m",
                    checkpoint_state["eef_gripper_base_xyz"],
                )
                motion_context.setdefault(
                    "current_target_quaternion_wxyz",
                    checkpoint_state["eef_gripper_base_quaternion_wxyz"],
                )
                scheduler_handoff_reason = (
                    motion_checkpoint_scheduler_handoff_reason(checkpoint)
                )
                if scheduler_handoff_reason is not None:
                    observation_id = f"checkpoint-{checkpoint_index}"
                    digest = hashlib.sha256(checkpoint_frame.tobytes()).hexdigest()[
                        :12
                    ]
                    decision = {
                        "decision": "retry",
                        "grasp_ready": False,
                        "confidence": 1.0,
                        "assessment": (
                            "Local kinematic evidence invalidated the current "
                            "motion; yield immediately to the fresh operation "
                            "scheduler so it can choose motion, actuator "
                            "evaluation, completion, or abort."
                        ),
                        "motion_tool": {
                            "tool_name": "local_scheduler_handoff",
                            "observation_id": observation_id,
                            "action": "hold",
                            "confidence": 1.0,
                            "reason": scheduler_handoff_reason,
                            "status": "scheduler_handoff",
                        },
                        "target_xyz_m": motion_context["current_target_xyz_m"],
                        "target_quaternion_wxyz": motion_context[
                            "current_target_quaternion_wxyz"
                        ],
                        "executor_id": motion_context.get(
                            "executor_id", "bounded_dls_ik"
                        ),
                        "executor_config": dict(
                            motion_context.get("executor_config") or {}
                        ),
                        "scheduler_handoff_reason": scheduler_handoff_reason,
                    }
                    event = {
                        **motion_context,
                        "frame": frame_name,
                        "state": checkpoint_state,
                        "local_scheduler_handoff": decision,
                        "coach_latency_s": 0.0,
                        "image_digest": digest,
                    }
                    episode_trace["motion_checkpoints"].append(event)
                    _write_trace(trace_path, episode_trace)
                    print(
                        f"[motion handoff] phase={checkpoint['phase']} "
                        f"iteration={checkpoint['iteration']} "
                        f"reason={scheduler_handoff_reason} → fresh operation "
                        "scheduler",
                        flush=True,
                    )
                    return decision
                current_target = torch.tensor(
                    motion_context["current_target_xyz_m"], dtype=torch.float32
                )
                decision, latency, digest = _choose_observation_bound_motion_tool(
                    motion_tool_provider,
                    motion_executor_registry,
                    instruction=args_cli.instruction,
                    observation_prefix=f"checkpoint-{checkpoint_index}",
                    frame=checkpoint_frame,
                    state=checkpoint_state,
                    current_target=current_target,
                    motion_context=motion_context,
                    rgbd_summary=depth_summary,
                    critic_context=_critic_context(
                        critic_memory, str(checkpoint["phase"])
                    ),
                )
                model_calls += 1
                digests.append(digest)
                event = {
                    **motion_context,
                    "frame": frame_name,
                    "state": checkpoint_state,
                    "coach_decision": decision,
                    "coach_latency_s": latency,
                    "image_digest": digest,
                }
                episode_trace["motion_checkpoints"].append(event)
                episode_trace["motion_tool_protocol"]["calls"].append(
                    decision["motion_tool"]
                )
                _write_trace(trace_path, episode_trace)
                tool = decision["motion_tool"]
                scheduler_selected_motion = bool(
                    isinstance(checkpoint.get("scheduler_decision"), dict)
                    and checkpoint["scheduler_decision"].get("decision")
                    == "dispatch"
                    and checkpoint["scheduler_decision"].get("operation_id")
                    == "continue.runtime_motion"
                )
                if (
                    scheduler_selected_motion
                    and tool.get("action") == "hold"
                    and tool.get("status") != "rejected"
                ):
                    semantic_error = (
                        "hold_motion cannot repeat after the fresh operation "
                        "scheduler dispatched continue.runtime_motion; issue a "
                        "bounded corrective movement or abort"
                    )
                    tool["status"] = "rejected"
                    tool["error"] = semantic_error
                    decision["assessment"] = (
                        "Motion tool rejected by scheduler-motion contract: "
                        f"{semantic_error}"
                    )
                previous_recovery_outcome = motion_context.get(
                    "previous_recovery_motion_outcome"
                )
                stalled_target_comparison = None
                if tool.get("action") == "execute":
                    stalled_target_comparison = compare_target_to_stalled_recovery(
                        previous_recovery_outcome=previous_recovery_outcome,
                        proposed_target_xyz_m=decision["target_xyz_m"],
                        proposed_target_quaternion_wxyz=decision[
                            "target_quaternion_wxyz"
                        ],
                    )
                if stalled_target_comparison is not None:
                    decision["stalled_target_comparison"] = (
                        stalled_target_comparison
                    )
                    tool["stalled_target_comparison"] = (
                        stalled_target_comparison
                    )
                    if stalled_target_comparison["effectively_identical"]:
                        semantic_error = (
                            "proposed target repeats a physically stalled "
                            "target within the previous executor's configured "
                            "position and orientation tolerances; use the "
                            "measured target deltas to propose a materially "
                            "different safe movement or abort"
                        )
                        tool["status"] = "rejected"
                        tool["error"] = semantic_error
                        decision["assessment"] = (
                            "Motion tool rejected by measured stalled-target "
                            f"evidence: {semantic_error}"
                        )
                print(
                    f"[ER2 checkpoint] phase={checkpoint['phase']} "
                    f"iteration={checkpoint['iteration']} reason={checkpoint['reason']} "
                    f"replan={attempt + 1}/{max_attempts} "
                    f"tool={tool['tool_name']} status={tool.get('status')} "
                    f"decision={decision.get('decision')} latency={latency:.2f}s",
                    flush=True,
                )
                # A valid execute, explicit model hold, or explicit abort is a
                # real decision. Only locally rejected calls are coached again.
                if tool.get("status") != "rejected":
                    return decision
                previous_outcome = {
                    "decision": decision.get("decision"),
                    "assessment": decision.get("assessment"),
                    "motion_tool": tool,
                }
            decision["replan_attempts_exhausted"] = True
            decision["replan_attempt_count"] = max_attempts
            return decision

        def operation_scheduler_handler(
            schedule_obs: dict[str, Any],
            current_action: torch.Tensor,
            *,
            phase_label: str,
            observation_prefix: str,
            motion_report: dict[str, Any],
            trigger_event: dict[str, Any] | None = None,
        ) -> tuple[dict[str, Any], bool, dict[str, Any], float, str]:
            """Route the next runtime operation, with one fail-closed retry."""
            nonlocal model_calls
            previous_outcome: dict[str, Any] | None = None
            total_latency = 0.0
            terminal = False
            digest = ""
            for attempt in range(2):
                schedule_state = _state(env, initial_object_z)
                schedule_frame = _single_exterior_frame(schedule_obs)
                schedule_frame, depth_summary = _rgbd_checkpoint_frame(
                    env, schedule_frame
                )
                frame_name = (
                    f"{observation_prefix}"
                    f"{'_retry' if attempt else ''}.jpg"
                )
                cv2.imwrite(
                    str(args_cli.artifact_dir / frame_name),
                    cv2.cvtColor(schedule_frame, cv2.COLOR_RGB2BGR),
                )
                current_engaged = bool(
                    float(current_action[0, 7].detach().cpu()) > 0.5
                )
                # The task adapter supplies the measured goal relation while
                # the contact sensor supplies retained-contact evidence.
                # Preserve a loaded actuator away from the goal, but expose it
                # at the goal or after contact loss so the model can transition
                # or recover instead of being forced into motion-only routing.
                goal_relation = schedule_state.get("goal_relation", {})
                goal_relation_observed = bool(
                    isinstance(goal_relation, Mapping)
                    and goal_relation.get("satisfied") is True
                )
                current_contact = schedule_state.get("current_contact")
                touch_observed = bool(
                    isinstance(current_contact, dict)
                    and current_contact.get("available")
                    and current_contact.get("touch")
                )
                retained_contact_observed = (
                    retained_contact_supports_loaded_actuator(current_contact)
                )
                actuator_recovery_observed = bool(
                    current_engaged and not retained_contact_observed
                )
                measured_actuator_outcome_invalidated = bool(
                    current_engaged
                    and isinstance(trigger_event, dict)
                    and trigger_event.get("actuator_outcome_invalidated") is True
                )
                interaction_distance_m = float(
                    schedule_state["fingertip_object_distance_m"]
                )
                maximum_interaction_distance_m = (
                    args_cli.maximum_actuator_interaction_distance
                )
                failed_grasp_pose_comparisons = (
                    compare_grasp_pose_to_failed_attempts(
                        failed_attempts=failed_grasp_attempts,
                        current_eef_xyz_m=schedule_state[
                            "eef_gripper_base_xyz"
                        ],
                        current_object_xyz_m=schedule_state[
                            "movable_object_xyz"
                        ],
                        current_eef_quaternion_wxyz=schedule_state[
                            "eef_gripper_base_quaternion_wxyz"
                        ],
                    )
                )
                grasp_pose_lease_released = failed_grasp_pose_lease_released(
                    pose_comparisons=failed_grasp_pose_comparisons,
                    minimum_translation_delta_m=(
                        args_cli.failed_grasp_retry_minimum_translation
                    ),
                    minimum_orientation_delta_deg=(
                        args_cli.failed_grasp_retry_minimum_orientation_deg
                    ),
                )
                pregrasp_axis_alignment = schedule_state.get(
                    "pregrasp_axis_alignment", {}
                )
                pregrasp_axis_alignment_ready = bool(
                    current_engaged
                    or (
                        isinstance(pregrasp_axis_alignment, Mapping)
                        and pregrasp_axis_alignment.get("available") is True
                        and pregrasp_axis_alignment.get("aligned") is True
                    )
                )
                actuator_transition_available = (
                    pregrasp_axis_alignment_ready
                    and actuator_transition_is_admissible(
                        actuator_engaged=current_engaged,
                        goal_contact_observed=goal_relation_observed,
                        retained_contact_observed=(
                            retained_contact_observed
                            if current_engaged
                            else touch_observed
                        ),
                        measured_actuator_outcome_invalidated=(
                            measured_actuator_outcome_invalidated
                        ),
                        failed_grasp_pose_lease_released=(
                            grasp_pose_lease_released
                        ),
                        interaction_distance_m=interaction_distance_m,
                        maximum_interaction_distance_m=(
                            maximum_interaction_distance_m
                        ),
                    )
                )
                candidates = _post_motion_operation_candidates(
                    actuator_transition_available=(
                        actuator_transition_available
                    )
                )
                decision, latency, digest = _choose_observation_bound_operation(
                    motion_tool_provider,
                    instruction=args_cli.instruction,
                    observation_prefix=(
                        f"{observation_prefix}-attempt-{attempt + 1}"
                    ),
                    frame=schedule_frame,
                    state=schedule_state,
                    operation_context={
                        "runtime_label": phase_label,
                        "current_actuator_state": (
                            "engaged" if current_engaged else "disengaged"
                        ),
                        "actuator_transition_admission": {
                            "available": actuator_transition_available,
                            "source": "measured_runtime_actuator_preconditions",
                            "goal_relation_observed": goal_relation_observed,
                            "goal_relation": goal_relation,
                            "retained_contact_observed": (
                                retained_contact_observed
                            ),
                            "touch_observed": touch_observed,
                            "loaded_contact_quality_source": (
                                "runtime_contact_channel_force_geometry"
                            ),
                            "contact_loss_recovery": actuator_recovery_observed,
                            "measured_outcome_recovery": (
                                measured_actuator_outcome_invalidated
                            ),
                            "interaction_distance_m": interaction_distance_m,
                            "maximum_interaction_distance_m": (
                                maximum_interaction_distance_m
                            ),
                            "failed_grasp_pose_lease_released": (
                                grasp_pose_lease_released
                            ),
                            "failed_grasp_pose_comparisons": (
                                failed_grasp_pose_comparisons
                            ),
                            "failed_grasp_retry_minimum_translation_m": (
                                args_cli.failed_grasp_retry_minimum_translation
                            ),
                            "failed_grasp_retry_minimum_orientation_deg": (
                                args_cli.failed_grasp_retry_minimum_orientation_deg
                            ),
                            "pregrasp_axis_alignment_ready": (
                                pregrasp_axis_alignment_ready
                            ),
                            "pregrasp_axis_alignment": pregrasp_axis_alignment,
                        },
                        "previous_operation_outcome": previous_outcome,
                        "trigger_event": trigger_event,
                        "completed_motion": {
                            "converged": motion_report.get("converged"),
                            "yielded_to_scheduler": motion_report.get(
                                "yielded_to_scheduler", False
                            ),
                            "target_error_after_m": motion_report.get(
                                "target_error_after_m"
                            ),
                            "orientation_error_after_deg": motion_report.get(
                                "orientation_error_after_deg"
                            ),
                            "recovery_request": motion_report.get(
                                "recovery_request"
                            ),
                        },
                    },
                    candidates=candidates,
                    rgbd_summary=depth_summary,
                    critic_context=_critic_context(critic_memory, phase_label),
                )
                total_latency += latency
                model_calls += 1
                digests.append(digest)
                event = {
                    **decision["scheduler_tool"],
                    "runtime_label": phase_label,
                    "attempt": attempt + 1,
                    "frame": frame_name,
                    "state": schedule_state,
                    "advertised_candidates": [
                        candidate.to_dict() for candidate in candidates
                    ],
                    "latency_s": latency,
                }
                episode_trace["operation_scheduler_protocol"]["calls"].append(event)
                _write_trace(trace_path, episode_trace)
                print(
                    f"[ER2 scheduler] label={phase_label} "
                    f"tool={decision['scheduler_tool'].get('tool_name')} "
                    f"operation={decision.get('operation_id')} "
                    f"kind={decision.get('operation_kind')} "
                    f"decision={decision.get('decision')} "
                    f"confidence={decision.get('confidence', 0.0):.2f} "
                    f"latency={latency:.2f}s image={digest}\n"
                    f"      {decision.get('assessment', '')}",
                    flush=True,
                )
                if decision.get("decision") in {"dispatch", "complete"}:
                    return schedule_obs, terminal, decision, total_latency, digest
                if decision.get("decision") == "abort":
                    raise RuntimeError(
                        f"Operation scheduler aborted during {phase_label}: {decision}"
                    )
                previous_outcome = decision
                if attempt == 0:
                    schedule_obs, terminal = _hold_joint_action(
                        env,
                        schedule_obs,
                        current_action,
                        args_cli.retry_steps,
                        gripper_closed=None,
                    )
                    if terminal:
                        raise RuntimeError(
                            "Environment terminated during scheduler retry hold"
                        )
            raise RuntimeError(
                f"Operation scheduler did not dispatch during {phase_label}: "
                f"{previous_outcome}"
            )

        def actuator_transition_handler(
            transition_obs: dict[str, Any],
            current_action: torch.Tensor,
            *,
            phase_label: str,
            observation_prefix: str,
            trigger_event: dict[str, Any] | None = None,
            scheduler_dispatch: dict[str, Any] | None = None,
            yield_on_hold: bool = False,
        ) -> tuple[dict[str, Any], bool, dict[str, Any], float, str]:
            """Obtain an actuator call, optionally yielding holds to scheduling."""
            nonlocal model_calls
            previous_outcome: dict[str, Any] | None = None
            total_latency = 0.0
            terminal = False
            digest = ""
            for attempt in range(2):
                transition_state = _state(env, initial_object_z)
                transition_frame = _single_exterior_frame(transition_obs)
                transition_frame, depth_summary = _rgbd_checkpoint_frame(
                    env, transition_frame
                )
                frame_name = (
                    f"{observation_prefix}"
                    f"{'_retry' if attempt else ''}.jpg"
                )
                cv2.imwrite(
                    str(args_cli.artifact_dir / frame_name),
                    cv2.cvtColor(transition_frame, cv2.COLOR_RGB2BGR),
                )
                current_engaged = bool(
                    float(current_action[0, 7].detach().cpu()) > 0.5
                )
                decision, latency, digest = (
                    _choose_observation_bound_actuator_tool(
                        motion_tool_provider,
                        actuator_executor_registry,
                        instruction=args_cli.instruction,
                        observation_prefix=(
                            f"{observation_prefix}-attempt-{attempt + 1}"
                        ),
                        frame=transition_frame,
                        state=transition_state,
                        actuator_context={
                            "phase_label": phase_label,
                            "current_binary_command": (
                                "engaged" if current_engaged else "disengaged"
                            ),
                            "transition_opportunity": True,
                            "trigger_event": trigger_event,
                            "scheduler_dispatch": scheduler_dispatch,
                            "failed_grasp_attempts": (
                                list(failed_grasp_attempts)
                            ),
                            "previous_tool_outcome": previous_outcome,
                            "executor_candidates": [
                                spec.executor_id
                                for spec in actuator_executor_registry.specs()
                            ],
                        },
                        rgbd_summary=depth_summary,
                        critic_context=_critic_context(
                            critic_memory, phase_label
                        ),
                    )
                )
                total_latency += latency
                model_calls += 1
                digests.append(digest)
                event = {
                    **decision["actuator_tool"],
                    "phase_label": phase_label,
                    "attempt": attempt + 1,
                    "frame": frame_name,
                    "state": transition_state,
                    "latency_s": latency,
                }
                episode_trace["actuator_tool_protocol"]["calls"].append(event)
                _write_trace(trace_path, episode_trace)
                print(
                    f"[ER2 actuator] phase={phase_label} "
                    f"tool={decision['actuator_tool'].get('tool_name')} "
                    f"decision={decision.get('decision')} "
                    f"confidence={decision.get('confidence', 0.0):.2f} "
                    f"latency={latency:.2f}s image={digest}\n"
                    f"      {decision.get('assessment', '')}",
                    flush=True,
                )
                if decision.get("decision") == "execute":
                    return transition_obs, terminal, decision, total_latency, digest
                if decision.get("decision") == "abort":
                    if attempt == 0:
                        previous_outcome = {
                            **decision["actuator_tool"],
                            "status": "confirmation_required",
                            "error": (
                                "abort_actuation terminates the overall task; "
                                "use hold_actuation for recoverable replanning "
                                "or confirm abort on the next fresh observation"
                            ),
                        }
                        transition_obs, terminal = _hold_joint_action(
                            env,
                            transition_obs,
                            current_action,
                            args_cli.retry_steps,
                            gripper_closed=None,
                        )
                        if terminal:
                            raise RuntimeError(
                                "Environment terminated during actuator abort "
                                "confirmation hold"
                            )
                        continue
                    raise RuntimeError(
                        f"Actuator governor aborted during {phase_label}: {decision}"
                    )
                previous_outcome = decision.get("actuator_tool")
                if yield_on_hold:
                    transition_obs, terminal = _hold_joint_action(
                        env,
                        transition_obs,
                        current_action,
                        args_cli.retry_steps,
                        gripper_closed=None,
                    )
                    if terminal:
                        raise RuntimeError(
                            "Environment terminated during actuator scheduler "
                            "handoff hold"
                        )
                    return transition_obs, terminal, decision, total_latency, digest
                if attempt == 0:
                    transition_obs, terminal = _hold_joint_action(
                        env,
                        transition_obs,
                        current_action,
                        args_cli.retry_steps,
                        gripper_closed=None,
                    )
                    if terminal:
                        raise RuntimeError(
                            "Environment terminated during actuator retry hold"
                        )
            raise RuntimeError(
                f"Actuator governor did not admit a transition during "
                f"{phase_label}: {previous_outcome}"
            )

        stages = []
        for phase, start, end in zip(phase_names, boundaries[:-1], boundaries[1:]):
            stages.append(
                (
                    phase,
                    int(start),
                    int(end),
                    # Retained only for the explicitly requested legacy fixed
                    # replay path. Adaptive execution must not route actuator
                    # opportunities from this recorded value.
                    bool(recorded_actions[start, 7] > 0.5),
                )
            )
        # Placement is a fresh model-issued world-space operation. Its motion
        # seed is the measured current pose, not a locally computed XY/Z target.
        if not args_cli.disable_residual_centering:
            stages.append(
                (
                    "place",
                    len(recorded_actions),
                    len(recorded_actions),
                    True,
                )
            )
        # The source episode reached RoboLab's success predicate while carrying
        # the object above its target. Open the gripper visibly to finish the task.
        stages.append(
            (
                "release",
                len(recorded_actions),
                len(recorded_actions),
                False,
            )
        )

        latched_carry_offset: torch.Tensor | None = None
        latched_carry_quaternion: torch.Tensor | None = None
        latched_rgbd_axis_references: dict[str, np.ndarray] = {}
        transport_recovery_count = 0
        placement_completed_during_recovery = False
        grasp_attempt_counter = 0
        latest_grasp_attempt: dict[str, Any] | None = None
        failed_grasp_attempts: list[dict[str, Any]] = []

        def reconcile_carry_latch_after_actuation(
            execution: dict[str, Any], *, source: str
        ) -> dict[str, Any] | None:
            """Expire carried-object state after an observed disengagement."""
            nonlocal latched_carry_offset
            nonlocal latched_carry_quaternion
            nonlocal latched_rgbd_axis_references
            if bool(execution.get("engaged_after")):
                return None
            if (
                latched_carry_offset is None
                and latched_carry_quaternion is None
                and not latched_rgbd_axis_references
            ):
                return None
            expiration = {
                "source": source,
                "reason": "actuator_disengaged",
                "requested_state": execution.get("requested_state"),
                "retained_contact_after": retained_contact_supports_loaded_actuator(
                    execution.get("state_after", {}).get("current_contact")
                ),
                "expired_rgbd_object_ids": sorted(
                    latched_rgbd_axis_references
                ),
            }
            latched_carry_offset = None
            latched_carry_quaternion = None
            latched_rgbd_axis_references = {}
            execution["carry_latch_expiration"] = expiration
            episode_trace.setdefault("carry_latch_expirations", []).append(
                expiration
            )
            print(
                "[carry latch] expired after actuator disengagement "
                f"source={source}",
                flush=True,
            )
            return expiration

        def capture_grasp_attempt(source: str) -> dict[str, Any]:
            """Capture task-neutral pose/contact evidence at clamp engagement."""
            nonlocal grasp_attempt_counter, latest_grasp_attempt
            grasp_attempt_counter += 1
            attempt_state = _state(env, initial_object_z)
            eef_xyz = np.asarray(
                attempt_state["eef_gripper_base_xyz"], dtype=np.float64
            )
            object_xyz = np.asarray(
                attempt_state["movable_object_xyz"], dtype=np.float64
            )
            contact = attempt_state.get("current_contact")
            latest_grasp_attempt = {
                "attempt_id": grasp_attempt_counter,
                "source": source,
                "eef_minus_object_m": (eef_xyz - object_xyz).tolist(),
                "eef_quaternion_wxyz": list(
                    attempt_state["eef_gripper_base_quaternion_wxyz"]
                ),
                "physical_gripper_closed_fraction": float(
                    attempt_state["gripper_closed_fraction"]
                ),
                "touch": bool(
                    isinstance(contact, dict) and contact.get("touch")
                ),
                "net_force_xyz_n": (
                    list(contact.get("net_force_xyz_n", []))
                    if isinstance(contact, dict)
                    else []
                ),
                "contact_bodies": (
                    contact.get("contact_bodies")
                    if isinstance(contact, dict)
                    else None
                ),
            }
            return latest_grasp_attempt

        def record_unsupported_grasp_attempt(
            execution: dict[str, Any],
            feedback_event: dict[str, Any],
            *,
            source: str,
        ) -> dict[str, Any] | None:
            """Lease a measured failed engagement pose against exact retries."""
            if execution.get("failed_grasp_attempt_id") is not None:
                return None
            if execution.get("requested_state") != "engage":
                return None
            if feedback_event.get("loaded_contact_supported_after") is True:
                return None
            attempt = capture_grasp_attempt(source)
            failure = {
                **attempt,
                "outcome": "engagement_lacked_supported_loaded_contact",
                "actuator_outcome_invalidation_reason": feedback_event.get(
                    "actuator_outcome_invalidation_reason"
                ),
                "fresh_failure_state": {
                    "gripper_closed_fraction": execution["state_after"][
                        "gripper_closed_fraction"
                    ],
                    "touch": execution["state_after"]["current_contact"].get(
                        "touch"
                    ),
                    "loaded_contact_supported": False,
                },
            }
            failed_grasp_attempts.append(failure)
            execution["failed_grasp_attempt_id"] = attempt["attempt_id"]
            print(
                "[failed-grasp lease] remembered unsupported engagement "
                f"attempt={attempt['attempt_id']} source={source}; a materially "
                "different object-relative position or wrist orientation is "
                "required before another engagement",
                flush=True,
            )
            return failure

        def admit_pregrasp_transition(
            actuator_tool_decision: dict[str, Any],
            *,
            current_target: torch.Tensor,
        ) -> None:
            """Evaluate fresh pre-grasp evidence at every actual close edge."""
            requested_state = actuator_tool_decision.get("command", {}).get(
                "state"
            )
            currently_engaged = bool(
                float(last_action[0, 7].detach().cpu()) > 0.5
            )
            if (
                requested_state != "engage"
                or currently_engaged
            ):
                return

            pregrasp_state = _state(env, initial_object_z)
            unique_images = len(set(digests))
            eef_motion = float(
                torch.linalg.norm(
                    _eef_position(env) - torch.tensor(eef_trace[0])
                )
            )
            feedback_ok = unique_images >= 2 and eef_motion >= 0.02
            tests["feedback"] = feedback_ok
            _test_line(
                4,
                "fresh observation before actuator transition",
                feedback_ok,
                f"unique_images={unique_images}/{len(digests)} "
                f"eef_motion={eef_motion:.3f}m",
            )

            base_distance = float(
                torch.linalg.norm(_eef_position(env) - current_target)
            )
            fingertip_distance = float(
                pregrasp_state["fingertip_object_distance_m"]
            )
            pregrasp_contact = pregrasp_state.get("current_contact", {})
            retained_touch = bool(
                isinstance(pregrasp_contact, dict)
                and pregrasp_contact.get("touch") is True
            )
            confidence = float(actuator_tool_decision.get("confidence", 0.0))
            jaw_alignment = pregrasp_state.get(
                "pregrasp_axis_alignment", {}
            )
            jaw_axis_aligned = bool(
                isinstance(jaw_alignment, Mapping)
                and jaw_alignment.get("available") is True
                and jaw_alignment.get("aligned") is True
            )
            pregrasp_passed = pregrasp_evidence_ready(
                model_ready=(
                    actuator_tool_decision.get("decision") == "execute"
                ),
                confidence=confidence,
                base_target_distance_m=base_distance,
                fingertip_object_distance_m=fingertip_distance,
                actuator_engaged=False,
                touch_observed=retained_touch,
                jaw_axis_aligned=jaw_axis_aligned,
            )
            tests["pregrasp"] = pregrasp_passed
            _test_line(
                5,
                "fresh visual pre-grasp gate",
                pregrasp_passed,
                f"ready="
                f"{actuator_tool_decision.get('decision') == 'execute'} "
                f"confidence={confidence:.2f} "
                f"base_target={base_distance:.3f}m "
                f"fingertip_object={fingertip_distance:.3f}m "
                f"engaged={currently_engaged} "
                f"touch={retained_touch} "
                f"jaw_axis_aligned={jaw_axis_aligned} "
                f"axis_error_deg="
                f"{jaw_alignment.get('minimum_axis_error_deg')} "
                f"candidate_yaw_corrections_deg="
                f"{[item.get('candidate_yaw_correction_deg') for item in jaw_alignment.get('axis_comparisons', [])]}",
            )
            if not pregrasp_passed:
                raise RuntimeError(
                    "Visual/metric pre-grasp gate rejected gripper closure"
                )

        def transition_admission_snapshot(
            required_capability: str,
            current_action: torch.Tensor,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            """Read the fresh evidence that may admit a runtime transition."""
            transition_state = _state(env, initial_object_z)
            current_contact = transition_state.get("current_contact")
            admission = runtime_transition_admission(
                required_capability,
                actuator_engaged=bool(
                    float(current_action[0, 7].detach().cpu()) > 0.5
                ),
                retained_contact_observed=(
                    retained_contact_supports_loaded_actuator(current_contact)
                ),
                interaction_candidate_observed=bool(
                    transition_state.get("grasp_candidate")
                ),
                interaction_confirmed_observed=bool(
                    transition_state.get("grasp_confirmed")
                ),
                actuator_disengaged_observed=bool(
                    transition_state["gripper_closed_fraction"] <= 0.10
                ),
            )
            admission["state"] = transition_state
            return transition_state, admission

        def resolve_runtime_transition(
            transition_obs: dict[str, Any],
            current_action: torch.Tensor,
            *,
            required_capability: str,
            runtime_label: str,
            observation_prefix: str,
        ) -> tuple[
            dict[str, Any],
            bool,
            torch.Tensor,
            dict[str, Any],
        ]:
            """Keep scheduling fresh operations until evidence admits a boundary."""
            nonlocal latched_carry_offset
            nonlocal latched_carry_quaternion
            nonlocal latched_rgbd_axis_references
            terminal = False
            operation_events: list[dict[str, Any]] = []
            pending_trigger_event: dict[str, Any] | None = None
            previous_transition_motion_outcome: dict[str, Any] | None = None
            maximum_operations = max(
                3, 2 * int(args_cli.motion_checkpoint_replans) + 1
            )

            for operation_index in range(maximum_operations + 1):
                transition_state, admission = transition_admission_snapshot(
                    required_capability,
                    current_action,
                )
                if admission["admitted"]:
                    report = {
                        "runtime_label": runtime_label,
                        "required_capability": required_capability,
                        "admitted": True,
                        "operation_count": len(operation_events),
                        "maximum_operations": maximum_operations,
                        "final_admission": admission,
                        "operations": operation_events,
                    }
                    print(
                        "[runtime transition] "
                        f"label={runtime_label} "
                        f"required={required_capability} admitted=True "
                        f"operations={len(operation_events)}",
                        flush=True,
                    )
                    return (
                        transition_obs,
                        terminal,
                        current_action,
                        report,
                    )
                if operation_index >= maximum_operations:
                    break

                trigger_event = pending_trigger_event or {
                    "type": "runtime_transition_not_admitted",
                    "runtime_label": runtime_label,
                    "required_capability": required_capability,
                    "admission": admission,
                    "instruction": args_cli.instruction,
                }
                pending_trigger_event = None
                print(
                    "[runtime transition] "
                    f"label={runtime_label} "
                    f"required={required_capability} admitted=False "
                    f"missing={admission['missing_evidence']} "
                    f"operation={operation_index + 1}/{maximum_operations}",
                    flush=True,
                )
                (
                    transition_obs,
                    terminal,
                    scheduler_operation,
                    scheduler_latency,
                    scheduler_digest,
                ) = operation_scheduler_handler(
                    transition_obs,
                    current_action,
                    phase_label=f"{runtime_label}:transition_pending",
                    observation_prefix=(
                        f"{observation_prefix}_{operation_index + 1}"
                    ),
                    motion_report={
                        "converged": False,
                        "yielded_to_scheduler": True,
                        "recovery_request": trigger_event,
                    },
                    trigger_event=trigger_event,
                )
                event: dict[str, Any] = {
                    "index": operation_index + 1,
                    "runtime_label": runtime_label,
                    "required_capability": required_capability,
                    "admission_before": admission,
                    "trigger_event": trigger_event,
                    "scheduler_decision": scheduler_operation,
                    "scheduler_latency_s": scheduler_latency,
                    "scheduler_image_digest": scheduler_digest,
                }
                if scheduler_operation.get("decision") == "complete":
                    event["completion_admitted"] = False
                    event["completion_rejection_reason"] = (
                        "required runtime capability remains unobserved"
                    )
                    operation_events.append(event)
                    episode_trace["runtime_transition_protocol"]["calls"].append(
                        event
                    )
                    _write_trace(trace_path, episode_trace)
                    raise RuntimeError(
                        "Operation scheduler declared completion before the "
                        f"{required_capability} transition was physically admitted"
                    )

                operation_kind = scheduler_operation.get("operation_kind")
                if operation_kind == "actuation":
                    (
                        transition_obs,
                        terminal,
                        transition_actuator_decision,
                        transition_actuator_latency,
                        transition_actuator_digest,
                    ) = actuator_transition_handler(
                        transition_obs,
                        current_action,
                        phase_label=f"{runtime_label}:transition_pending",
                        observation_prefix=(
                            f"actuator_{observation_prefix}_"
                            f"{operation_index + 1}"
                        ),
                        trigger_event=trigger_event,
                        scheduler_dispatch=scheduler_operation,
                        yield_on_hold=True,
                    )
                    event["actuator_decision"] = transition_actuator_decision
                    event["actuator_latency_s"] = transition_actuator_latency
                    event["actuator_image_digest"] = transition_actuator_digest
                    if transition_actuator_decision.get("decision") == "execute":
                        admit_pregrasp_transition(
                            transition_actuator_decision,
                            current_target=_eef_position(env),
                        )
                        engaged_before = bool(
                            float(current_action[0, 7].detach().cpu()) > 0.5
                        )
                        (
                            transition_obs,
                            terminal,
                            current_action,
                            transition_actuator_execution,
                        ) = _execute_binary_actuator_tool(
                            env,
                            transition_obs,
                            current_action,
                            transition_actuator_decision,
                            initial_object_z=initial_object_z,
                        )
                        reconcile_carry_latch_after_actuation(
                            transition_actuator_execution,
                            source=f"{runtime_label}:transition_pending",
                        )
                        feedback_event = _actuator_feedback_event_from_execution(
                            transition_actuator_execution,
                            actuator_feedback_policy,
                        )
                        transition_actuator_execution["feedback_event"] = (
                            feedback_event
                        )
                        record_unsupported_grasp_attempt(
                            transition_actuator_execution,
                            feedback_event,
                            source=f"{runtime_label}:transition_pending",
                        )
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "execution"
                        ] = transition_actuator_execution
                        event["actuator_execution"] = transition_actuator_execution
                        engaged_after = bool(
                            transition_actuator_execution.get("engaged_after")
                        )
                        if not engaged_before and engaged_after:
                            event["grasp_attempt"] = capture_grasp_attempt(
                                f"runtime_transition:{runtime_label}"
                            )
                            latched_carry_offset = (
                                _eef_position(env) - _movable_object_position(env)
                            )
                            latched_carry_quaternion = _eef_quaternion(env)
                            latched_rgbd_axis_references = {}
                            for object_id in trackable_object_ids:
                                try:
                                    axis_observation = (
                                        _rgbd_object_axis_observation(
                                            env,
                                            prim_label_fragment=(
                                                f"/scene/{object_id}"
                                            ),
                                        )
                                    )
                                    latched_rgbd_axis_references[object_id] = (
                                        np.asarray(
                                            axis_observation["major_axis_camera"],
                                            dtype=np.float64,
                                        )
                                    )
                                except ValueError:
                                    continue
                            event["carry_latched_after_actuation"] = {
                                "eef_minus_object_m": (
                                    latched_carry_offset.tolist()
                                ),
                                "eef_quaternion_wxyz": (
                                    latched_carry_quaternion.tolist()
                                ),
                                "tracked_rgbd_objects": sorted(
                                    latched_rgbd_axis_references
                                ),
                            }
                        pending_trigger_event = {
                            "type": "runtime_transition_actuation_observed",
                            "runtime_label": runtime_label,
                            "required_capability": required_capability,
                            **feedback_event,
                        }
                    else:
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "scheduler_handoff"
                        ] = True
                        pending_trigger_event = {
                            "type": "actuator_governor_yielded_to_scheduler",
                            "runtime_label": runtime_label,
                            "required_capability": required_capability,
                            "actuator_governor_decision": (
                                transition_actuator_decision
                            ),
                        }
                elif operation_kind == "motion":
                    transition_motion_decision = motion_checkpoint_handler(
                        transition_obs,
                        {
                            "reason": "runtime_transition_not_admitted",
                            "phase": f"{runtime_label}:transition_pending",
                            "iteration": operation_index + 1,
                            "current_target_xyz_m": (
                                _eef_position(env).tolist()
                            ),
                            "current_target_quaternion_wxyz": (
                                _eef_quaternion(env).tolist()
                            ),
                            "scheduler_decision": scheduler_operation,
                            "runtime_transition_admission": admission,
                            "previous_recovery_motion_outcome": (
                                previous_transition_motion_outcome
                            ),
                            "lease_condition_sources": {
                                "contact": "sim6.gripper_contact_sensor",
                                "tracked_pose": (
                                    "sim6.privileged_relative_pose_adapter"
                                    if latched_carry_offset is not None
                                    else None
                                ),
                                "tracked_orientation": (
                                    {
                                        object_id: (
                                            "rgbd.instance_depth_major_axis"
                                        )
                                        for object_id in (
                                            latched_rgbd_axis_references
                                        )
                                    }
                                    if latched_rgbd_axis_references
                                    else {}
                                ),
                                "observed_clearance": None,
                            },
                        },
                    )
                    event["motion_decision"] = transition_motion_decision
                    if transition_motion_decision.get("decision") != "execute":
                        pending_trigger_event = {
                            "type": "motion_governor_yielded_to_scheduler",
                            "runtime_label": runtime_label,
                            "required_capability": required_capability,
                            "motion_governor_decision": (
                                transition_motion_decision
                            ),
                        }
                    else:
                        (
                            transition_obs,
                            terminal,
                            current_action,
                            transition_motion_report,
                        ) = _move_eef_to_target(
                            env,
                            transition_obs,
                            current_action,
                            torch.tensor(
                                transition_motion_decision["target_xyz_m"],
                                dtype=torch.float32,
                            ),
                            torch.tensor(
                                transition_motion_decision[
                                    "target_quaternion_wxyz"
                                ],
                                dtype=torch.float32,
                            ),
                            f"{runtime_label}:transition_pending",
                            gripper_closed=bool(
                                float(current_action[0, 7].detach().cpu()) > 0.5
                            ),
                            initial_object_z=initial_object_z,
                            executor_config=dict(
                                transition_motion_decision.get(
                                    "executor_config"
                                )
                                or {}
                            ),
                            carry_reference_offset=latched_carry_offset,
                            rgbd_axis_references=latched_rgbd_axis_references,
                            checkpoint_callback=motion_checkpoint_handler,
                        )
                        event["motion_report"] = transition_motion_report
                        pending_trigger_event = {
                            "type": "runtime_transition_motion_completed",
                            "runtime_label": runtime_label,
                            "required_capability": required_capability,
                            "motion_outcome": recovery_motion_handoff_from_report(
                                transition_motion_report
                            ),
                        }
                else:
                    raise RuntimeError(
                        "Runtime transition scheduler returned unsupported "
                        f"operation: {scheduler_operation}"
                    )

                if terminal:
                    raise RuntimeError(
                        "Environment terminated before runtime transition "
                        f"{required_capability} was admitted"
                    )
                _, event["admission_after"] = transition_admission_snapshot(
                    required_capability,
                    current_action,
                )
                if isinstance(event.get("motion_report"), Mapping):
                    previous_transition_motion_outcome = (
                        runtime_transition_motion_handoff(
                            event["motion_report"],
                            admission_before=admission,
                            admission_after=event["admission_after"],
                        )
                    )
                    event["motion_handoff_outcome"] = (
                        previous_transition_motion_outcome
                    )
                    if isinstance(pending_trigger_event, dict):
                        pending_trigger_event["motion_outcome"] = (
                            previous_transition_motion_outcome
                        )
                operation_events.append(event)
                episode_trace["runtime_transition_protocol"]["calls"].append(
                    event
                )
                _write_trace(trace_path, episode_trace)

            raise RuntimeError(
                "Runtime transition operation budget exhausted without fresh "
                f"evidence for {required_capability}: "
                f"{len(operation_events)}/{maximum_operations} operations"
            )

        episode_trace["recoveries"] = []
        last_action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
        last_action[0, :7] = torch.as_tensor(
            joint_states[0, :7], dtype=torch.float32, device=env.device
        )
        task_completed_by_scheduler = False
        for stage_index, (
            phase,
            start,
            end,
            legacy_recorded_gripper_closed,
        ) in enumerate(stages, start=1):
            actuator_engaged_at_stage_start = bool(
                float(last_action[0, 7].detach().cpu()) > 0.5
            )
            actuator_decision: dict[str, Any] | None = None
            actuator_execution: dict[str, Any] | None = None
            actuator_latency = 0.0
            actuator_digest: str | None = None
            scheduler_decision: dict[str, Any] | None = None
            scheduler_latency = 0.0
            scheduler_digest: str | None = None
            current = _state(env, initial_object_z)
            if (
                not args_cli.disable_adaptive_ik
                and phase == "place"
            ):
                nominal = torch.tensor(
                    current["eef_gripper_base_xyz"], dtype=torch.float32
                )
                nominal_quaternion = (
                    latched_carry_quaternion
                    if latched_carry_quaternion is not None
                    else torch.tensor(
                        current["eef_gripper_base_quaternion_wxyz"],
                        dtype=torch.float32,
                    )
                )
                target_source = "current_observation_model_seed"
            elif not args_cli.disable_adaptive_ik and phase != "release":
                movable_object_xyz = torch.tensor(current["movable_object_xyz"], dtype=torch.float32)
                if phase in {"lift", "above_plate"} and latched_carry_offset is not None:
                    grasp_xyz = movable_object_xyz + latched_carry_offset
                    assert latched_carry_quaternion is not None
                    nominal_quaternion = latched_carry_quaternion
                else:
                    grasp_xyz, nominal_quaternion = apply_object_relative_grasp(
                        movable_object_xyz,
                        torch.tensor(
                            current["movable_object_quaternion_wxyz"], dtype=torch.float32
                        ),
                        grasp_offset_object,
                        object_to_grasp_quat,
                    )
                nominal = live_phase_target(
                    phase,
                    movable_object_xyz,
                    torch.tensor(current["target_receptacle_xyz"], dtype=torch.float32),
                    grasp_xyz - movable_object_xyz,
                    eef_xyz=torch.tensor(
                        current["eef_gripper_base_xyz"], dtype=torch.float32
                    ),
                    approach_clearance=args_cli.approach_clearance,
                    lift_clearance=args_cli.lift_clearance,
                    plate_hover_height=args_cli.plate_hover_height,
                )
                target_source = "live_object_pose"
            elif phase == "release":
                nominal = _eef_position(env)
                nominal_quaternion = _eef_quaternion(env)
                target_source = "current_eef_hold"
            else:
                action_index = min(start, len(recorded_actions) - 1)
                nominal = torch.from_numpy(recorded_actions[action_index, :3].copy())
                nominal_quaternion = torch.from_numpy(
                    recorded_actions[action_index, 3:7].copy()
                )
                target_source = "recorded_demonstration"
            orientation_target_source = target_source
            if (
                not actuator_engaged_at_stage_start
                and phase != "release"
            ):
                nominal_quaternion = torch.tensor(
                    current["eef_gripper_base_quaternion_wxyz"],
                    dtype=torch.float32,
                )
                orientation_target_source = (
                    "fresh_measured_disengaged_wrist_pose"
                )
            frame = _single_exterior_frame(obs)
            stage_depth_summary = None
            if not args_cli.disable_adaptive_ik and phase != "release":
                frame, stage_depth_summary = _rgbd_checkpoint_frame(env, frame)
                if latched_rgbd_axis_references:
                    tracked_axes: dict[str, Any] = {}
                    for object_id, reference_axis in (
                        latched_rgbd_axis_references.items()
                    ):
                        try:
                            tracked_axes[object_id] = _rgbd_object_axis_observation(
                                env,
                                prim_label_fragment=f"/scene/{object_id}",
                                reference_axis=reference_axis,
                            )
                        except ValueError as exc:
                            tracked_axes[object_id] = {
                                "available": False,
                                "error": str(exc),
                            }
                    if stage_depth_summary is None:
                        stage_depth_summary = {}
                    stage_depth_summary["tracked_object_axes"] = tracked_axes
            frame_path = args_cli.artifact_dir / f"{stage_index:02d}_{phase}_before.jpg"
            cv2.imwrite(str(frame_path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            selected_executor_config: dict[str, Any] = {}
            if not args_cli.disable_adaptive_ik and phase != "release":
                decision, latency, digest = _choose_observation_bound_motion_tool(
                    motion_tool_provider,
                    motion_executor_registry,
                    instruction=args_cli.instruction,
                    observation_prefix=f"stage-{stage_index}",
                    frame=frame,
                    state=current,
                    current_target=nominal,
                    motion_context={
                        "phase_label": phase,
                        "current_target_xyz_m": nominal.tolist(),
                        "current_target_quaternion_wxyz": (
                            nominal_quaternion.tolist()
                        ),
                        "target_seed_semantics": (
                            "The target is the current measured workspace pose; "
                            "select any evidence-grounded correction needed to "
                            "advance the human instruction."
                            if phase == "place"
                            else "runtime-proposed phase target"
                        ),
                        "current_actuator_state": (
                            "engaged"
                            if actuator_engaged_at_stage_start
                            else "disengaged"
                        ),
                        "executor_candidates": [
                            spec.executor_id
                            for spec in motion_executor_registry.specs()
                        ],
                        "lease_condition_sources": {
                            "contact": "sim6_gripper_contact_sensor",
                            "tracked_pose": (
                                "sim_privileged_relative_pose"
                                if latched_carry_offset is not None
                                else None
                            ),
                            "tracked_orientation": (
                                {
                                    object_id: "rgbd_instance_depth_major_axis"
                                    for object_id in latched_rgbd_axis_references
                                }
                                if latched_rgbd_axis_references
                                else {}
                            ),
                            "observed_clearance": (
                                "sim_privileged_object_to_support_height"
                                if phase in {"above_plate", "place"}
                                else None
                            ),
                            "model_polling": (
                                "periodic_or_event"
                                if args_cli.periodic_motion_observations
                                else "event_or_completion_only"
                            ),
                        },
                    },
                    rgbd_summary=stage_depth_summary,
                    critic_context=_critic_context(critic_memory, phase),
                )
                nominal = torch.tensor(decision["target_xyz_m"], dtype=torch.float32)
                nominal_quaternion = torch.tensor(
                    decision["target_quaternion_wxyz"], dtype=torch.float32
                )
                orientation_target_source = "model_motion_tool"
                selected_executor_config = dict(decision["executor_config"])
                target_source = "model_motion_tool"
                episode_trace["motion_tool_protocol"]["calls"].append(
                    decision["motion_tool"]
                )
                stage_model_called = True
            elif not args_cli.disable_adaptive_ik and phase == "release":
                _, digest = _encode_frame(frame)
                decision = {
                    "decision": "execute",
                    "grasp_ready": True,
                    "confidence": 1.0,
                    "assessment": (
                        "Workspace pose is held; the fresh actuator tool call "
                        "governs the physical transition."
                    ),
                }
                latency = 0.0
                stage_model_called = False
            else:
                decision, latency, digest = coach.reason(
                    _stage_prompt(
                        phase,
                        current,
                        nominal,
                        nominal_quaternion,
                        legacy_recorded_gripper_closed,
                        _critic_context(critic_memory, phase),
                    ),
                        frame,
                    )
                stage_model_called = True
            if stage_model_called:
                model_calls += 1
                digests.append(digest)
            confidence = float(decision.get("confidence", 0.0))
            assessment = str(decision.get("assessment", ""))
            print(
                f"[ER2] phase={phase} decision={decision.get('decision')} "
                f"confidence={confidence:.2f} latency={latency:.2f}s image={digest}\n"
                f"      {assessment}",
                flush=True,
            )

            initial_decision = dict(decision)
            retry_performed = False
            needs_retry = decision.get("decision") != "execute" or (
                phase == "grasp" and not bool(decision.get("grasp_ready"))
            )
            if needs_retry:
                if phase in {"grasp", "lift"} or "motion_tool" in decision:
                    retry_performed = True
                    previous_motion_tool = decision.get("motion_tool")
                    print(
                        f"[coach] {phase} requested retry; holding for a fresh "
                        "observation",
                        flush=True,
                    )
                    obs, terminal = _hold_joint_action(
                        env,
                        obs,
                        last_action,
                        args_cli.retry_steps,
                        gripper_closed=None,
                    )
                    current = _state(env, initial_object_z)
                    if not args_cli.disable_adaptive_ik:
                        movable_object_xyz = torch.tensor(
                            current["movable_object_xyz"], dtype=torch.float32
                        )
                        if phase == "place":
                            nominal = torch.tensor(
                                current["eef_gripper_base_xyz"],
                                dtype=torch.float32,
                            )
                            nominal_quaternion = (
                                latched_carry_quaternion
                                if latched_carry_quaternion is not None
                                else torch.tensor(
                                    current[
                                        "eef_gripper_base_quaternion_wxyz"
                                    ],
                                    dtype=torch.float32,
                                )
                            )
                        elif (
                            phase in {"lift", "above_plate"}
                            and latched_carry_offset is not None
                        ):
                            grasp_xyz = movable_object_xyz + latched_carry_offset
                            assert latched_carry_quaternion is not None
                            nominal_quaternion = latched_carry_quaternion
                        else:
                            grasp_xyz, nominal_quaternion = apply_object_relative_grasp(
                                movable_object_xyz,
                                torch.tensor(
                                    current["movable_object_quaternion_wxyz"], dtype=torch.float32
                                ),
                                grasp_offset_object,
                                object_to_grasp_quat,
                            )
                        if phase != "place":
                            nominal = live_phase_target(
                                phase,
                                movable_object_xyz,
                                torch.tensor(
                                    current["target_receptacle_xyz"], dtype=torch.float32
                                ),
                                grasp_xyz - movable_object_xyz,
                                eef_xyz=torch.tensor(
                                    current["eef_gripper_base_xyz"],
                                    dtype=torch.float32,
                                ),
                                approach_clearance=args_cli.approach_clearance,
                                lift_clearance=args_cli.lift_clearance,
                                plate_hover_height=args_cli.plate_hover_height,
                            )
                        if (
                            not bool(
                                float(last_action[0, 7].detach().cpu()) > 0.5
                            )
                            and phase != "release"
                        ):
                            nominal_quaternion = torch.tensor(
                                current[
                                    "eef_gripper_base_quaternion_wxyz"
                                ],
                                dtype=torch.float32,
                            )
                            orientation_target_source = (
                                "fresh_measured_disengaged_wrist_pose"
                            )
                    frame = _single_exterior_frame(obs)
                    retry_depth_summary = None
                    if not args_cli.disable_adaptive_ik:
                        frame, retry_depth_summary = _rgbd_checkpoint_frame(env, frame)
                    decision, latency, digest = (
                        _choose_observation_bound_motion_tool(
                            motion_tool_provider,
                            motion_executor_registry,
                            instruction=args_cli.instruction,
                            observation_prefix=f"stage-{stage_index}-retry",
                            frame=frame,
                            state=current,
                            current_target=nominal,
                            motion_context={
                                "phase_label": phase,
                                "retry": True,
                                "current_target_xyz_m": nominal.tolist(),
                                "current_target_quaternion_wxyz": (
                                    nominal_quaternion.tolist()
                                ),
                                "target_seed_semantics": (
                                    "The target is the current measured workspace "
                                    "pose; correct the rejected proposal using "
                                    "fresh evidence and the human instruction."
                                    if phase == "place"
                                    else "runtime-proposed phase target"
                                ),
                                "current_actuator_state": (
                                    "engaged"
                                    if bool(
                                        float(
                                            last_action[0, 7].detach().cpu()
                                        )
                                        > 0.5
                                    )
                                    else "disengaged"
                                ),
                                "previous_tool_outcome": previous_motion_tool,
                                "lease_condition_sources": {
                                    "contact": "sim6_gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim_privileged_relative_pose"
                                        if latched_carry_offset is not None
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: "rgbd_instance_depth_major_axis"
                                            for object_id in latched_rgbd_axis_references
                                        }
                                        if latched_rgbd_axis_references
                                        else {}
                                    ),
                                    "observed_clearance": (
                                        "sim_privileged_object_to_support_height"
                                        if phase in {"above_plate", "place"}
                                        else None
                                    ),
                                    "model_polling": (
                                        "periodic_or_event"
                                        if args_cli.periodic_motion_observations
                                        else "event_or_completion_only"
                                    ),
                                },
                            },
                            rgbd_summary=retry_depth_summary,
                            critic_context=_critic_context(critic_memory, phase),
                        )
                    )
                    nominal = torch.tensor(
                        decision["target_xyz_m"], dtype=torch.float32
                    )
                    nominal_quaternion = torch.tensor(
                        decision["target_quaternion_wxyz"], dtype=torch.float32
                    )
                    orientation_target_source = "model_motion_tool"
                    selected_executor_config = dict(decision["executor_config"])
                    episode_trace["motion_tool_protocol"]["calls"].append(
                        decision["motion_tool"]
                    )
                    model_calls += 1
                    digests.append(digest)
                    confidence = float(decision.get("confidence", 0.0))
                    print(
                        f"[ER2] {phase} retry decision={decision.get('decision')} "
                        f"ready={decision.get('grasp_ready')} confidence={decision.get('confidence')} "
                        f"image={digest}",
                        flush=True,
                    )
                if (
                    decision.get("decision") != "execute"
                    and isinstance(decision.get("motion_tool"), dict)
                    and decision["motion_tool"].get("status") == "rejected"
                ):
                    rejected_boundary_decision = decision
                    rejected_boundary_tool = dict(decision["motion_tool"])
                    decision = {
                        "decision": "retry",
                        "grasp_ready": False,
                        "confidence": float(
                            rejected_boundary_decision.get("confidence", 0.0)
                        ),
                        "assessment": (
                            "The phase-boundary motion proposal was rejected by "
                            "the observation-bound safety gate; return to the "
                            "fresh operation scheduler before any movement."
                        ),
                        "motion_tool": {
                            **rejected_boundary_tool,
                            "proposed_action": rejected_boundary_tool.get(
                                "action"
                            ),
                            "action": "hold",
                            "handoff_reason": (
                                "phase_boundary_motion_tool_rejected"
                            ),
                        },
                    }
                if (
                    decision.get("decision") == "retry"
                    and isinstance(decision.get("motion_tool"), dict)
                    and decision["motion_tool"].get("action") == "hold"
                ):
                    boundary_hold = decision
                    (
                        obs,
                        terminal,
                        boundary_schedule,
                        boundary_scheduler_latency,
                        boundary_scheduler_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label=f"{phase}:boundary_hold",
                        observation_prefix=(
                            f"scheduler_boundary_hold_{stage_index:02d}_{phase}"
                        ),
                        motion_report={
                            "converged": False,
                            "yielded_to_scheduler": True,
                            "recovery_request": {
                                "reason": "model_requested_hold",
                                "coach_decision": boundary_hold,
                            },
                        },
                        trigger_event={
                            "type": "phase_boundary_model_hold",
                            "phase_label": phase,
                            "model_decision": boundary_hold,
                        },
                    )
                    scheduler_decision = boundary_schedule
                    scheduler_latency += boundary_scheduler_latency
                    scheduler_digest = boundary_scheduler_digest
                    maximum_boundary_operations = max(
                        3, 2 * int(args_cli.motion_checkpoint_replans) + 1
                    )
                    for boundary_operation_index in range(
                        maximum_boundary_operations
                    ):
                        if boundary_schedule.get("decision") == "complete":
                            task_completed_by_scheduler = True
                            break
                        if boundary_schedule.get("operation_kind") == "motion":
                            break
                        if boundary_schedule.get("operation_kind") != "actuation":
                            raise RuntimeError(
                                "Boundary scheduler returned unsupported "
                                f"operation: {boundary_schedule}"
                            )
                        (
                            obs,
                            terminal,
                            boundary_actuator_decision,
                            boundary_actuator_latency,
                            boundary_actuator_digest,
                        ) = actuator_transition_handler(
                            obs,
                            last_action,
                            phase_label=f"{phase}:boundary_hold",
                            observation_prefix=(
                                f"actuator_boundary_hold_{stage_index:02d}_"
                                f"{phase}_{boundary_operation_index + 1}"
                            ),
                            scheduler_dispatch=boundary_schedule,
                            yield_on_hold=True,
                        )
                        actuator_decision = boundary_actuator_decision
                        actuator_latency += boundary_actuator_latency
                        actuator_digest = boundary_actuator_digest
                        if boundary_actuator_decision.get("decision") == "execute":
                            admit_pregrasp_transition(
                                boundary_actuator_decision,
                                current_target=nominal,
                            )
                            (
                                obs,
                                terminal,
                                last_action,
                                boundary_actuator_execution,
                            ) = _execute_binary_actuator_tool(
                                env,
                                obs,
                                last_action,
                                boundary_actuator_decision,
                                initial_object_z=initial_object_z,
                            )
                            reconcile_carry_latch_after_actuation(
                                boundary_actuator_execution,
                                source=f"{phase}:boundary_hold",
                            )
                            boundary_feedback_event = (
                                _actuator_feedback_event_from_execution(
                                    boundary_actuator_execution,
                                    actuator_feedback_policy,
                                )
                            )
                            boundary_actuator_execution["feedback_event"] = (
                                boundary_feedback_event
                            )
                            record_unsupported_grasp_attempt(
                                boundary_actuator_execution,
                                boundary_feedback_event,
                                source=f"{phase}:boundary_hold",
                            )
                            episode_trace["actuator_tool_protocol"]["calls"][-1][
                                "execution"
                            ] = boundary_actuator_execution
                            actuator_execution = boundary_actuator_execution
                            boundary_trigger_event = {
                                "type": (
                                    "phase_boundary_actuator_transition_completed"
                                ),
                                "phase_label": phase,
                                **boundary_feedback_event,
                            }
                        else:
                            episode_trace["actuator_tool_protocol"]["calls"][-1][
                                "scheduler_handoff"
                            ] = True
                            boundary_trigger_event = {
                                "type": "actuator_governor_yielded_to_scheduler",
                                "phase_label": phase,
                                "actuator_governor_decision": (
                                    boundary_actuator_decision
                                ),
                            }
                        (
                            obs,
                            terminal,
                            boundary_schedule,
                            boundary_post_scheduler_latency,
                            boundary_post_scheduler_digest,
                        ) = operation_scheduler_handler(
                            obs,
                            last_action,
                            phase_label=f"{phase}:boundary_actuation_completed",
                            observation_prefix=(
                                f"scheduler_boundary_actuation_"
                                f"{stage_index:02d}_{phase}_"
                                f"{boundary_operation_index + 1}"
                            ),
                            motion_report={
                                "converged": False,
                                "yielded_to_scheduler": True,
                                "recovery_request": boundary_trigger_event,
                            },
                            trigger_event=boundary_trigger_event,
                        )
                        scheduler_decision = boundary_schedule
                        scheduler_latency += boundary_post_scheduler_latency
                        scheduler_digest = boundary_post_scheduler_digest
                    else:
                        raise RuntimeError(
                            "Boundary operation handoff budget exhausted "
                            "without selecting motion or completion: "
                            f"{maximum_boundary_operations} operations"
                        )
                    if not task_completed_by_scheduler:
                        decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": "scheduler_requested_boundary_replan",
                                "phase": phase,
                                "iteration": 0,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "previous_motion_tool_outcome": boundary_hold[
                                    "motion_tool"
                                ],
                                "scheduler_decision": boundary_schedule,
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if latched_carry_offset is not None
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if latched_rgbd_axis_references
                                        else {}
                                    ),
                                    "observed_clearance": (
                                        "sim6.privileged_object_to_support_height_adapter"
                                        if phase in {"above_plate", "place"}
                                        else None
                                    ),
                                },
                            },
                        )
                        nominal = torch.tensor(
                            decision["target_xyz_m"], dtype=torch.float32
                        )
                        nominal_quaternion = torch.tensor(
                            decision["target_quaternion_wxyz"],
                            dtype=torch.float32,
                        )
                        selected_executor_config = dict(
                            decision.get("executor_config") or {}
                        )
                        confidence = float(decision.get("confidence", 0.0))
                if decision.get("decision") != "execute" or (
                    phase == "grasp" and not bool(decision.get("grasp_ready"))
                ):
                    raise RuntimeError(f"ER 2 stopped at phase {phase}: {decision}")

            visualize_axes(
                nominal + env.scene.env_origins[0].detach().cpu(),
                nominal_quaternion,
                "gemini_er2_target",
                axis_length=0.12,
            )
            print(
                f"[executor] {phase}: source={target_source} "
                f"orientation_source={orientation_target_source} "
                f"target={nominal.tolist()} actuator_current="
                f"{'engaged' if bool(float(last_action[0, 7].detach().cpu()) > 0.5) else 'disengaged'}",
                flush=True,
            )
            motion_report: dict[str, Any]
            if phase == "release":
                motion_report = {
                    "enabled": True,
                    "executor": "current_workspace_pose_hold",
                    "target_source": target_source,
                    "converged": not terminal,
                }
            elif not args_cli.disable_adaptive_ik:
                seed_steps = 0
                if phase == "approach_object":
                    # Establish the demonstrated downward grasp orientation at
                    # a safe hover height before translating to a moved banana.
                    obs, terminal, last_action = _run_joint_segment(
                        env, obs, joint_states, recorded_actions, start, end
                    )
                    seed_steps = end - start
                if terminal:
                    raise RuntimeError(f"Environment terminated during {phase} seed motion")
                motion_attempts: list[dict[str, Any]] = []
                while True:
                    obs, terminal, last_action, attempt_report = _move_eef_to_target(
                        env,
                        obs,
                        last_action,
                        nominal,
                        nominal_quaternion,
                        phase,
                        gripper_closed=bool(
                            float(last_action[0, 7].detach().cpu()) > 0.5
                        ),
                        initial_object_z=initial_object_z,
                        executor_config=selected_executor_config,
                        carry_reference_offset=latched_carry_offset,
                        rgbd_axis_references=latched_rgbd_axis_references,
                        checkpoint_callback=motion_checkpoint_handler,
                    )
                    motion_attempts.append(attempt_report)
                    if not bool(attempt_report.get("recovery_requested")):
                        break
                    if motion_report_yields_to_scheduler(attempt_report):
                        attempt_report["yielded_to_scheduler"] = True
                        attempt_report["yield_reason"] = (
                            "local_motion_invalidation_requires_fresh_"
                            "operation_selection"
                        )
                        attempt_report["recovery_requested"] = False
                        print(
                            f"[executor handoff] {phase}: local invalidation yielded "
                            "to the fresh operation scheduler",
                            flush=True,
                        )
                        break
                    if phase != "above_plate":
                        raise RuntimeError(
                            f"Motion recovery replan budget exhausted in {phase}: "
                            f"{attempt_report.get('recovery_request')}"
                        )
                    if transport_recovery_count >= args_cli.max_transport_recoveries:
                        raise RuntimeError(
                            "Transport recovery budget exhausted: "
                            f"{transport_recovery_count}/{args_cli.max_transport_recoveries}"
                        )
                    transport_recovery_count += 1
                    print(
                        f"[recovery] starting bounded transport recovery "
                        f"{transport_recovery_count}/{args_cli.max_transport_recoveries}",
                        flush=True,
                    )
                    (
                        obs,
                        terminal,
                        last_action,
                        latched_carry_offset,
                        latched_carry_quaternion,
                        recovery_report,
                    ) = _recover_transport_grasp(
                        env,
                        obs,
                        last_action,
                        initial_object_z=initial_object_z,
                        grasp_offset_object=grasp_offset_object,
                        object_to_grasp_quat=object_to_grasp_quat,
                        checkpoint_callback=motion_checkpoint_handler,
                    )
                    recovery_event = {
                        "index": transport_recovery_count,
                        "trigger": attempt_report.get("recovery_request"),
                        **recovery_report,
                    }
                    episode_trace["recoveries"].append(recovery_event)
                    _write_trace(trace_path, episode_trace)
                    if bool(recovery_report.get("goal_completed")):
                        placement_completed_during_recovery = True
                        print(
                            f"[recovery] completed task with outcome="
                            f"{recovery_report.get('outcome')}; skipping further "
                            "transport/centering",
                            flush=True,
                        )
                        break
                    print(
                        f"[recovery] completed with outcome="
                        f"{recovery_report.get('outcome')}; resuming above_plate",
                        flush=True,
                    )
                    resumed_state = _state(env, initial_object_z)
                    movable_object_xyz = torch.tensor(
                        resumed_state["movable_object_xyz"], dtype=torch.float32
                    )
                    target_receptacle_xyz = torch.tensor(
                        resumed_state["target_receptacle_xyz"], dtype=torch.float32
                    )
                    nominal = live_phase_target(
                        "above_plate",
                        movable_object_xyz,
                        target_receptacle_xyz,
                        latched_carry_offset,
                        eef_xyz=torch.tensor(
                            resumed_state["eef_gripper_base_xyz"],
                            dtype=torch.float32,
                        ),
                        approach_clearance=args_cli.approach_clearance,
                        lift_clearance=args_cli.lift_clearance,
                        plate_hover_height=args_cli.plate_hover_height,
                    )
                    nominal_quaternion = latched_carry_quaternion
                if placement_completed_during_recovery:
                    orientation_error = torch.linalg.vector_norm(
                        quaternion_error_axis_angle_wxyz(
                            nominal_quaternion, _eef_quaternion(env)
                        )
                    )
                    motion_report = {
                        "enabled": True,
                        "phase": phase,
                        "executor": "bounded_recovery_goal_completion",
                        "attempts": motion_attempts,
                        "recovery_count": transport_recovery_count,
                        "goal_completed_during_recovery": True,
                        "target_xyz": nominal.tolist(),
                        "target_quaternion_wxyz": nominal_quaternion.tolist(),
                        "target_error_after_m": float(
                            torch.linalg.vector_norm(nominal - _eef_position(env))
                        ),
                        "orientation_error_after_deg": float(
                            orientation_error * 180.0 / np.pi
                        ),
                        "converged": True,
                    }
                elif len(motion_attempts) == 1:
                    motion_report = motion_attempts[0]
                else:
                    motion_report = {
                        "enabled": True,
                        "phase": phase,
                        "executor": "bounded_recovery_wrapped_live_pose_ik",
                        "attempts": motion_attempts,
                        "recovery_count": len(motion_attempts) - 1,
                        "target_xyz": nominal.tolist(),
                        "target_quaternion_wxyz": nominal_quaternion.tolist(),
                        "target_error_after_m": motion_attempts[-1][
                            "target_error_after_m"
                        ],
                        "orientation_error_after_deg": motion_attempts[-1][
                            "orientation_error_after_deg"
                        ],
                        "converged": motion_attempts[-1]["converged"],
                    }
                motion_report["demonstration_orientation_seed_steps"] = seed_steps
                if not bool(motion_report["converged"]) and not bool(
                    motion_report.get("yielded_to_scheduler")
                ):
                    raise RuntimeError(
                        f"Adaptive IK did not reach the live {phase} target: "
                        f"error={motion_report['target_error_after_m']:.4f} m"
                    )
            else:
                obs, terminal, last_action = _run_joint_segment(
                    env, obs, joint_states, recorded_actions, start, end
                )
                motion_report = {
                    "enabled": False,
                    "executor": "fixed_demonstration_replay",
                    "target_source": target_source,
                    "demonstrated_steps": end - start,
                    "converged": not terminal,
                }
                if phase == "above_plate" and not terminal:
                    # The successful source episode terminated only eight steps
                    # into transport. Sim 6 needs a short settling hold for the
                    # same final demonstrated joint target to converge before
                    # ER 2 decides whether release is safe.
                    obs, terminal = _hold_joint_action(
                        env,
                        obs,
                        last_action,
                        args_cli.retry_steps,
                        gripper_closed=True,
                    )
            actuator_engaged_before_transition = bool(
                float(last_action[0, 7].detach().cpu()) > 0.5
            )
            if not args_cli.disable_adaptive_ik:
                (
                    obs,
                    terminal,
                    scheduler_decision,
                    scheduler_latency,
                    scheduler_digest,
                ) = operation_scheduler_handler(
                    obs,
                    last_action,
                    phase_label=phase,
                    observation_prefix=(
                        f"scheduler_stage_{stage_index:02d}_{phase}"
                    ),
                    motion_report=motion_report,
                )
                if scheduler_decision.get("decision") == "complete":
                    task_completed_by_scheduler = True
                    motion_report["scheduler_declared_task_complete"] = True
                elif scheduler_decision.get("operation_kind") not in {
                    "motion",
                    "actuation",
                }:
                    raise RuntimeError(
                        f"Scheduler returned unsupported operation during {phase}: "
                        f"{scheduler_decision}"
                    )
                # A motion lease that yielded on measured invalidation has not
                # completed its runtime operation.  If the fresh scheduler asks
                # for more motion, dispatch that request now instead of silently
                # advancing the legacy phase scaffold.  Each dispatched motion
                # is followed by another fresh observation and scheduler call;
                # phase advancement is admitted only once the scheduler selects
                # a different operation or declares physical completion.
                scheduler_motion_handoffs: list[dict[str, Any]] = []
                if (
                    scheduler_decision.get("operation_kind") == "motion"
                    and motion_report_yields_to_scheduler(motion_report)
                ):
                    previous_handoff_report = motion_report
                    maximum_handoffs = max(
                        1, int(args_cli.motion_checkpoint_replans)
                    )
                    for handoff_index in range(maximum_handoffs):
                        handoff_decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": "scheduler_requested_runtime_motion",
                                "phase": f"{phase}:scheduler_handoff",
                                "iteration": handoff_index + 1,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "scheduler_decision": scheduler_decision,
                                "previous_recovery_motion_outcome": (
                                    recovery_motion_handoff_from_report(
                                        previous_handoff_report
                                    )
                                ),
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if latched_carry_offset is not None
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if latched_rgbd_axis_references
                                        else {}
                                    ),
                                    "observed_clearance": None,
                                },
                            },
                        )
                        if handoff_decision.get("decision") != "execute":
                            raise RuntimeError(
                                "Scheduler-dispatched runtime motion was not "
                                f"admitted by the motion governor: {handoff_decision}"
                            )
                        handoff_target = torch.tensor(
                            handoff_decision["target_xyz_m"],
                            dtype=torch.float32,
                        )
                        handoff_quaternion = torch.tensor(
                            handoff_decision["target_quaternion_wxyz"],
                            dtype=torch.float32,
                        )
                        # This scheduler-issued target supersedes the stale
                        # phase seed for all downstream convergence and
                        # pre-grasp admission measurements.
                        nominal = handoff_target
                        nominal_quaternion = handoff_quaternion
                        selected_executor_config = dict(
                            handoff_decision.get("executor_config") or {}
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            handoff_report,
                        ) = _move_eef_to_target(
                            env,
                            obs,
                            last_action,
                            handoff_target,
                            handoff_quaternion,
                            f"{phase}:scheduler_handoff",
                            gripper_closed=bool(
                                float(last_action[0, 7].detach().cpu()) > 0.5
                            ),
                            initial_object_z=initial_object_z,
                            executor_config=selected_executor_config,
                            carry_reference_offset=latched_carry_offset,
                            rgbd_axis_references=latched_rgbd_axis_references,
                            checkpoint_callback=motion_checkpoint_handler,
                        )
                        if terminal:
                            raise RuntimeError(
                                "Environment terminated during scheduler-dispatched "
                                "runtime motion"
                            )
                        if not bool(handoff_report.get("converged")) and not (
                            motion_report_yields_to_scheduler(handoff_report)
                        ):
                            raise RuntimeError(
                                "Scheduler-dispatched runtime motion neither "
                                f"converged nor yielded safely: {handoff_report}"
                            )
                        scheduler_motion_handoffs.append(
                            {
                                "index": handoff_index + 1,
                                "motion_decision": handoff_decision,
                                "motion_report": handoff_report,
                            }
                        )
                        previous_handoff_report = handoff_report
                        (
                            obs,
                            terminal,
                            scheduler_decision,
                            handoff_scheduler_latency,
                            handoff_scheduler_digest,
                        ) = operation_scheduler_handler(
                            obs,
                            last_action,
                            phase_label=f"{phase}:scheduler_handoff",
                            observation_prefix=(
                                f"scheduler_handoff_{stage_index:02d}_{phase}_"
                                f"{handoff_index + 1}"
                            ),
                            motion_report=handoff_report,
                            trigger_event={
                                "type": "scheduler_motion_handoff_completed",
                                "phase_label": phase,
                                "handoff_index": handoff_index + 1,
                            },
                        )
                        scheduler_latency += handoff_scheduler_latency
                        scheduler_digest = handoff_scheduler_digest
                        if scheduler_decision.get("decision") == "complete":
                            task_completed_by_scheduler = True
                            motion_report[
                                "scheduler_declared_task_complete"
                            ] = True
                            break
                        if scheduler_decision.get("operation_kind") != "motion":
                            break
                    if (
                        not task_completed_by_scheduler
                        and scheduler_decision.get("operation_kind") == "motion"
                    ):
                        raise RuntimeError(
                            "Scheduler motion handoff budget exhausted without "
                            "selecting a different runtime operation: "
                            f"{len(scheduler_motion_handoffs)}/{maximum_handoffs}"
                        )
                    motion_report["scheduler_motion_handoffs"] = (
                        scheduler_motion_handoffs
                    )
            if (
                scheduler_decision is not None
                and scheduler_decision.get("operation_kind") == "actuation"
            ):
                # A model-issued actuator hold is a request for a different
                # operation from a fresh observation, not an actuator error.
                # Keep this phase boundary active until the scheduler either
                # supplies corrective motion followed by an admitted actuator
                # transition, or proves that the physical task is complete.
                stage_actuator_handoffs: list[dict[str, Any]] = []
                stage_operation = scheduler_decision
                stage_trigger_event: dict[str, Any] | None = None
                stage_operation_report = motion_report
                maximum_stage_operations = max(
                    3, 2 * int(args_cli.motion_checkpoint_replans) + 1
                )
                for operation_index in range(maximum_stage_operations):
                    operation_kind = stage_operation.get("operation_kind")
                    if operation_kind == "actuation":
                        (
                            obs,
                            terminal,
                            stage_actuator_decision,
                            stage_actuator_latency,
                            stage_actuator_digest,
                        ) = actuator_transition_handler(
                            obs,
                            last_action,
                            phase_label=phase,
                            observation_prefix=(
                                f"actuator_stage_{stage_index:02d}_{phase}_"
                                f"{operation_index + 1}"
                            ),
                            trigger_event=stage_trigger_event,
                            scheduler_dispatch=stage_operation,
                            yield_on_hold=True,
                        )
                        actuator_decision = stage_actuator_decision
                        actuator_latency += stage_actuator_latency
                        actuator_digest = stage_actuator_digest
                        if stage_actuator_decision.get("decision") == "execute":
                            break
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "scheduler_handoff"
                        ] = True
                        stage_trigger_event = {
                            "type": "actuator_governor_yielded_to_scheduler",
                            "triggered": True,
                            "phase_label": phase,
                            "actuator_governor_decision": (
                                stage_actuator_decision
                            ),
                        }
                        stage_operation_report = {
                            "converged": False,
                            "yielded_to_scheduler": True,
                            "recovery_request": stage_trigger_event,
                        }
                    elif operation_kind == "motion":
                        stage_motion_decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": (
                                    "scheduler_requested_motion_after_"
                                    "actuator_hold"
                                ),
                                "phase": f"{phase}:actuator_hold_motion",
                                "iteration": operation_index + 1,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "previous_motion_tool_outcome": (
                                    actuator_decision.get("actuator_tool")
                                    if isinstance(actuator_decision, dict)
                                    else None
                                ),
                                "scheduler_decision": stage_operation,
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if latched_carry_offset is not None
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if latched_rgbd_axis_references
                                        else {}
                                    ),
                                    "observed_clearance": None,
                                },
                            },
                        )
                        if stage_motion_decision.get("decision") != "execute":
                            stage_operation_report = {
                                "converged": False,
                                "yielded_to_scheduler": True,
                                "recovery_request": {
                                    "reason": (
                                        "motion_governor_withheld_"
                                        "actuator_hold_correction"
                                    ),
                                    "motion_decision": stage_motion_decision,
                                },
                            }
                            stage_trigger_event = {
                                "type": "motion_governor_yielded_to_scheduler",
                                "triggered": True,
                                "phase_label": phase,
                                "motion_governor_decision": (
                                    stage_motion_decision
                                ),
                            }
                        else:
                            nominal = torch.tensor(
                                stage_motion_decision["target_xyz_m"],
                                dtype=torch.float32,
                            )
                            nominal_quaternion = torch.tensor(
                                stage_motion_decision[
                                    "target_quaternion_wxyz"
                                ],
                                dtype=torch.float32,
                            )
                            (
                                obs,
                                terminal,
                                last_action,
                                stage_motion_report,
                            ) = _move_eef_to_target(
                                env,
                                obs,
                                last_action,
                                nominal,
                                nominal_quaternion,
                                f"{phase}:actuator_hold_motion",
                                gripper_closed=bool(
                                    float(
                                        last_action[0, 7].detach().cpu()
                                    )
                                    > 0.5
                                ),
                                initial_object_z=initial_object_z,
                                executor_config=dict(
                                    stage_motion_decision.get(
                                        "executor_config"
                                    )
                                    or {}
                                ),
                                carry_reference_offset=(
                                    latched_carry_offset
                                ),
                                rgbd_axis_references=(
                                    latched_rgbd_axis_references
                                ),
                                checkpoint_callback=(
                                    motion_checkpoint_handler
                                ),
                            )
                            if terminal:
                                raise RuntimeError(
                                    "Environment terminated during actuator-"
                                    "hold corrective motion"
                                )
                            stage_actuator_handoffs.append(
                                {
                                    "index": len(stage_actuator_handoffs) + 1,
                                    "scheduler_decision": stage_operation,
                                    "motion_decision": stage_motion_decision,
                                    "motion_report": stage_motion_report,
                                }
                            )
                            stage_operation_report = stage_motion_report
                            stage_trigger_event = {
                                "type": (
                                    "actuator_hold_scheduler_motion_completed"
                                ),
                                "triggered": True,
                                "phase_label": phase,
                                "motion_outcome": (
                                    recovery_motion_handoff_from_report(
                                        stage_motion_report
                                    )
                                ),
                            }
                    else:
                        raise RuntimeError(
                            "Actuator-hold scheduler returned unsupported "
                            f"operation: {stage_operation}"
                        )

                    (
                        obs,
                        terminal,
                        stage_operation,
                        stage_scheduler_latency,
                        stage_scheduler_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label=f"{phase}:actuator_hold_handoff",
                        observation_prefix=(
                            f"scheduler_actuator_hold_{stage_index:02d}_"
                            f"{phase}_{operation_index + 1}"
                        ),
                        motion_report=stage_operation_report,
                        trigger_event=stage_trigger_event,
                    )
                    scheduler_decision = stage_operation
                    scheduler_latency += stage_scheduler_latency
                    scheduler_digest = stage_scheduler_digest
                    if stage_operation.get("decision") == "complete":
                        task_completed_by_scheduler = True
                        motion_report["scheduler_declared_task_complete"] = True
                        break
                else:
                    raise RuntimeError(
                        "Actuator-hold scheduler handoff budget exhausted "
                        "without an admitted transition: "
                        f"{maximum_stage_operations} operations"
                    )
                motion_report["actuator_hold_scheduler_handoffs"] = (
                    stage_actuator_handoffs
                )
                if task_completed_by_scheduler:
                    break
                if (
                    actuator_decision is None
                    or actuator_decision.get("decision") != "execute"
                ):
                    raise RuntimeError(
                        "Actuator-hold handoff ended without an admitted "
                        f"actuator transition: {actuator_decision}"
                    )
                admit_pregrasp_transition(
                    actuator_decision,
                    current_target=nominal,
                )
                obs, terminal, last_action, actuator_execution = (
                    _execute_binary_actuator_tool(
                        env,
                        obs,
                        last_action,
                        actuator_decision,
                        initial_object_z=initial_object_z,
                    )
                )
                reconcile_carry_latch_after_actuation(
                    actuator_execution,
                    source=phase,
                )
                episode_trace["actuator_tool_protocol"]["calls"][-1][
                    "execution"
                ] = actuator_execution
                motion_report["actuator_execution"] = actuator_execution
                print(
                    f"[actuator executor] phase={phase} "
                    f"state={actuator_execution['requested_state']} "
                    f"settle_steps={actuator_execution['settle_steps']} "
                    f"touch_after="
                    f"{actuator_execution['state_after']['current_contact']['touch']}",
                    flush=True,
                )
                if terminal:
                    raise RuntimeError(
                        f"Environment terminated during {phase} actuator transition"
                    )
                post_feedback_decisions: list[dict[str, Any]] = []
                post_feedback_executions: list[dict[str, Any]] = []
                post_feedback_motion_handoffs: list[dict[str, Any]] = []
                current_actuator_execution = actuator_execution
                pending_scheduler_trigger_event: dict[str, Any] | None = None
                for feedback_index in range(3):
                    if pending_scheduler_trigger_event is None:
                        feedback_event = _actuator_feedback_event_from_execution(
                            current_actuator_execution,
                            actuator_feedback_policy,
                        )
                        current_actuator_execution["feedback_event"] = feedback_event
                        record_unsupported_grasp_attempt(
                            current_actuator_execution,
                            feedback_event,
                            source=f"{phase}:post_actuation",
                        )
                        _write_trace(trace_path, episode_trace)
                        print(
                            f"[post-actuation event] phase={phase} "
                            f"triggered={feedback_event['triggered']} "
                            f"position_delta="
                            f"{feedback_event['actuator_position_change']:.3f} "
                            f"force_delta="
                            f"{feedback_event['tactile_force_change_n']:.3f}N "
                            f"touch_changed={feedback_event['touch_changed']} "
                            f"outcome_invalidated="
                            f"{feedback_event['actuator_outcome_invalidated']}",
                            flush=True,
                        )
                        if not feedback_event["triggered"]:
                            break
                        feedback_trigger_event = {
                            "type": "actuator_physical_outcome_observed",
                            **feedback_event,
                        }
                    else:
                        feedback_trigger_event = pending_scheduler_trigger_event
                        pending_scheduler_trigger_event = None
                    (
                        obs,
                        terminal,
                        post_feedback_decision,
                        post_feedback_latency,
                        post_feedback_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label=f"{phase}:post_actuation",
                        observation_prefix=(
                            f"scheduler_post_actuator_{stage_index:02d}_{phase}_"
                            f"{feedback_index + 1}"
                        ),
                        motion_report=motion_report,
                        trigger_event=feedback_trigger_event,
                    )
                    post_feedback_decisions.append(post_feedback_decision)
                    scheduler_latency += post_feedback_latency
                    scheduler_digest = post_feedback_digest
                    if post_feedback_decision.get("decision") == "complete":
                        task_completed_by_scheduler = True
                        motion_report["scheduler_declared_task_complete"] = True
                        break
                    if post_feedback_decision.get("operation_kind") == "motion":
                        post_feedback_motion_decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": (
                                    "scheduler_requested_runtime_motion_after_"
                                    "actuation"
                                ),
                                "phase": f"{phase}:post_actuation_motion",
                                "iteration": feedback_index + 1,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "scheduler_decision": post_feedback_decision,
                                "actuator_feedback_event": (
                                    feedback_trigger_event
                                ),
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": None,
                                    "tracked_orientation": {},
                                    "observed_clearance": None,
                                },
                            },
                        )
                        if post_feedback_motion_decision.get("decision") != "execute":
                            raise RuntimeError(
                                "Scheduler-dispatched post-actuation motion was "
                                "not admitted by the motion governor: "
                                f"{post_feedback_motion_decision}"
                            )
                        (
                            obs,
                            terminal,
                            last_action,
                            post_feedback_motion_report,
                        ) = _move_eef_to_target(
                            env,
                            obs,
                            last_action,
                            torch.tensor(
                                post_feedback_motion_decision["target_xyz_m"],
                                dtype=torch.float32,
                            ),
                            torch.tensor(
                                post_feedback_motion_decision[
                                    "target_quaternion_wxyz"
                                ],
                                dtype=torch.float32,
                            ),
                            f"{phase}:post_actuation_motion",
                            gripper_closed=bool(
                                float(last_action[0, 7].detach().cpu()) > 0.5
                            ),
                            initial_object_z=initial_object_z,
                            executor_config=dict(
                                post_feedback_motion_decision.get(
                                    "executor_config"
                                )
                                or {}
                            ),
                            carry_reference_offset=None,
                            rgbd_axis_references={},
                            checkpoint_callback=motion_checkpoint_handler,
                        )
                        if terminal:
                            raise RuntimeError(
                                "Environment terminated during scheduler-"
                                "dispatched post-actuation motion"
                            )
                        post_feedback_motion_handoffs.append(
                            {
                                "scheduler_decision": post_feedback_decision,
                                "motion_decision": post_feedback_motion_decision,
                                "motion_report": post_feedback_motion_report,
                            }
                        )
                        pending_scheduler_trigger_event = {
                            "type": "post_actuation_scheduler_motion_completed",
                            "triggered": True,
                            "phase_label": phase,
                            "motion_outcome": recovery_motion_handoff_from_report(
                                post_feedback_motion_report
                            ),
                        }
                        continue
                    if post_feedback_decision.get("operation_kind") != "actuation":
                        raise RuntimeError(
                            "Post-actuation scheduler returned unsupported "
                            f"operation: {post_feedback_decision}"
                        )
                    (
                        obs,
                        terminal,
                        repeated_actuator_decision,
                        repeated_actuator_latency,
                        actuator_digest,
                    ) = actuator_transition_handler(
                        obs,
                        last_action,
                        phase_label=f"{phase}:post_actuation",
                        observation_prefix=(
                            f"actuator_post_feedback_{stage_index:02d}_{phase}_"
                            f"{feedback_index + 1}"
                        ),
                        trigger_event=feedback_trigger_event,
                        scheduler_dispatch=post_feedback_decision,
                        yield_on_hold=True,
                    )
                    actuator_latency += repeated_actuator_latency
                    if repeated_actuator_decision.get("decision") != "execute":
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "scheduler_handoff"
                        ] = True
                        pending_scheduler_trigger_event = {
                            "type": "actuator_governor_yielded_to_scheduler",
                            "triggered": True,
                            "phase_label": phase,
                            "actuator_governor_decision": (
                                repeated_actuator_decision
                            ),
                        }
                        continue
                    (
                        obs,
                        terminal,
                        last_action,
                        repeated_execution,
                    ) = _execute_binary_actuator_tool(
                        env,
                        obs,
                        last_action,
                        repeated_actuator_decision,
                        initial_object_z=initial_object_z,
                    )
                    reconcile_carry_latch_after_actuation(
                        repeated_execution,
                        source=f"{phase}:post_actuation",
                    )
                    episode_trace["actuator_tool_protocol"]["calls"][-1][
                        "execution"
                    ] = repeated_execution
                    post_feedback_executions.append(repeated_execution)
                    current_actuator_execution = repeated_execution
                    if terminal:
                        raise RuntimeError(
                            "Environment terminated during post-feedback actuation"
                        )
                else:
                    motion_report["post_actuation_feedback_budget_yield"] = {
                        "reason": (
                            "bounded_post_actuation_operations_completed"
                        ),
                        "operation_count": len(post_feedback_decisions),
                        "next_step": (
                            "return_current_observation_to_runtime_phase_loop"
                        ),
                        "task_success_assumed": False,
                    }
                    print(
                        "[post-actuation handoff] bounded operation budget "
                        "completed; returning current observation to the fresh "
                        "runtime phase loop",
                        flush=True,
                    )
                actuator_execution["post_feedback_scheduler_decisions"] = (
                    post_feedback_decisions
                )
                actuator_execution["post_feedback_executions"] = (
                    post_feedback_executions
                )
                actuator_execution["post_feedback_motion_handoffs"] = (
                    post_feedback_motion_handoffs
                )
                motion_report["post_actuation_scheduler_decisions"] = (
                    post_feedback_decisions
                )
                _write_trace(trace_path, episode_trace)
                if (
                    not actuator_engaged_before_transition
                    and bool(float(last_action[0, 7].detach().cpu()) > 0.5)
                ):
                    motion_report["grasp_attempt"] = capture_grasp_attempt(
                        f"stage:{phase}"
                    )
                    # Preserve the fresh measured carry transform after the
                    # admitted actuator command, not before contact.
                    latched_carry_offset = (
                        _eef_position(env) - _movable_object_position(env)
                    )
                    latched_carry_quaternion = _eef_quaternion(env)
                    motion_report["latched_carry_offset_m"] = (
                        latched_carry_offset.tolist()
                    )
                    motion_report["latched_carry_quaternion_wxyz"] = (
                        latched_carry_quaternion.tolist()
                    )
                    latched_rgbd_axis_references = {}
                    rgbd_reference_observations: dict[str, Any] = {}
                    for object_id in trackable_object_ids:
                        try:
                            rgbd_reference = _rgbd_object_axis_observation(
                                env,
                                prim_label_fragment=f"/scene/{object_id}",
                            )
                            latched_rgbd_axis_references[object_id] = np.asarray(
                                rgbd_reference["major_axis_camera"],
                                dtype=np.float64,
                            )
                            rgbd_reference_observations[object_id] = rgbd_reference
                        except ValueError as exc:
                            rgbd_reference_observations[object_id] = {
                                "available": False,
                                "error": str(exc),
                            }
                    motion_report["latched_rgbd_axis_references"] = (
                        rgbd_reference_observations
                    )
                    print(
                        "[rgbd-tracker] latched observable object axes="
                        f"{sorted(latched_rgbd_axis_references)}",
                        flush=True,
                    )
            eef = _eef_position(env)
            eef_trace.append(eef.tolist())
            pos_error = float(torch.linalg.norm(eef - nominal))
            object_now = _movable_object_position(env)
            print(
                f"[feedback] {phase}: eef={eef.tolist()} error={pos_error:.4f}m "
                f"object={object_now.tolist()} terminal={terminal}",
                flush=True,
            )
            phase_contact_summary = _active_contact_summary()
            if phase in {"grasp", "lift", "release"}:
                print(
                    f"[contact] after={phase} "
                    f"coverage={phase_contact_summary['coverage']:.3f} "
                    f"touch_samples={phase_contact_summary['touch_samples']} "
                    f"peak_net_force={phase_contact_summary['peak_net_force_n']:.3f}N",
                    flush=True,
                )
            episode_trace["stages"].append({
                "phase": phase,
                "frame": frame_path.name,
                "initial_coach_decision": initial_decision,
                "coach_decision": decision,
                "retry_performed": retry_performed,
                "coach_latency_s": latency,
                "state_before": current,
                "nominal_target_xyz": nominal.tolist(),
                "nominal_target_quaternion_wxyz": nominal_quaternion.tolist(),
                "target_source": target_source,
                "orientation_target_source": orientation_target_source,
                "demonstrated_steps": end - start,
                "motion_report": motion_report,
                "recorded_actuator_hint_used": bool(
                    args_cli.disable_adaptive_ik
                ),
                "operation_scheduler_decision": scheduler_decision,
                "operation_scheduler_latency_s": scheduler_latency,
                "operation_scheduler_image_digest": scheduler_digest,
                "actuator_state_at_stage_start": (
                    "engaged"
                    if actuator_engaged_at_stage_start
                    else "disengaged"
                ),
                "actuator_tool_decision": actuator_decision,
                "actuator_tool_latency_s": actuator_latency,
                "actuator_tool_image_digest": actuator_digest,
                "actuator_execution": actuator_execution,
                "eef_after_xyz": eef.tolist(),
                "eef_target_error_m": pos_error,
                "object_after_xyz": object_now.tolist(),
                "terminal": terminal,
                "contact_telemetry_after": phase_contact_summary,
            })
            _write_trace(trace_path, episode_trace)

            if phase == "above_plate" and not terminal:
                if placement_completed_during_recovery:
                    completed_state = _state(env, initial_object_z)
                    centering_report = {
                        "enabled": False,
                        "converged": True,
                        "reason": "goal_completed_during_transport_recovery",
                        "object_target_contact_proxy": completed_state[
                            "object_target_contact_proxy"
                        ],
                        "xy_error_after_m": completed_state[
                            "object_target_xy_error_m"
                        ],
                        "height_above_plate_after_m": completed_state[
                            "object_height_above_target_m"
                        ],
                        "iterations": [],
                    }
                    tests["centering"] = True
                    _test_line(
                        7,
                        "bounded residual plate centering",
                        True,
                        "goal completed during supervised recovery set-down",
                    )
                elif args_cli.disable_residual_centering:
                    centering_report = {
                        "enabled": False,
                        "converged": None,
                        "reason": "baseline mode",
                    }
                    tests["centering"] = True
                    _test_line(7, "residual plate centering", True, "disabled (baseline mode)")
                else:
                    centering_report = {
                        "enabled": True,
                        "converged": None,
                        "reason": (
                            "pending_fresh_observation_bound_place_operation"
                        ),
                        "legacy_local_xy_z_controller_used": False,
                    }
                episode_trace["residual_centering"] = centering_report
                _write_trace(trace_path, episode_trace)

            if phase == "place" and not terminal:
                placement_state = _state(env, initial_object_z)
                centered = bool(
                    placement_state["object_target_contact_proxy"]
                    or (
                        placement_state["object_target_xy_error_m"]
                        <= args_cli.center_tolerance
                        and abs(
                            placement_state["object_height_above_target_m"]
                            - args_cli.release_height
                        )
                        <= args_cli.release_height_tolerance
                    )
                )
                placement_outcome_recoveries: list[dict[str, Any]] = []
                for recovery_index in range(args_cli.max_transport_recoveries):
                    if centered or terminal:
                        break
                    trigger_event = {
                        "type": "measured_stage_outcome_not_met",
                        "predicate_id": (
                            "object.target_contact_or_release_envelope"
                        ),
                        "observed_values": {
                            "target_contact": placement_state[
                                "object_target_contact_proxy"
                            ],
                            "target_xy_error_m": placement_state[
                                "object_target_xy_error_m"
                            ],
                            "height_above_target_m": placement_state[
                                "object_height_above_target_m"
                            ],
                        },
                        "admission_values": {
                            "maximum_target_xy_error_m": (
                                args_cli.center_tolerance
                            ),
                            "target_release_height_m": args_cli.release_height,
                            "release_height_tolerance_m": (
                                args_cli.release_height_tolerance
                            ),
                        },
                        "instruction": args_cli.instruction,
                    }
                    print(
                        "[outcome recovery] measured placement is outside the "
                        f"release envelope (xy="
                        f"{placement_state['object_target_xy_error_m']:.3f}m, "
                        f"height="
                        f"{placement_state['object_height_above_target_m']:.3f}m); "
                        f"requesting fresh operation {recovery_index + 1}/"
                        f"{args_cli.max_transport_recoveries}",
                        flush=True,
                    )
                    (
                        obs,
                        terminal,
                        recovery_schedule,
                        recovery_scheduler_latency,
                        recovery_scheduler_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label="place:measured_outcome_not_met",
                        observation_prefix=(
                            f"scheduler_outcome_{stage_index:02d}_place_"
                            f"{recovery_index + 1}"
                        ),
                        motion_report={
                            "converged": False,
                            "yielded_to_scheduler": True,
                            "recovery_request": trigger_event,
                        },
                        trigger_event=trigger_event,
                    )
                    scheduler_latency += recovery_scheduler_latency
                    scheduler_digest = recovery_scheduler_digest
                    recovery_event: dict[str, Any] = {
                        "kind": "measured_stage_outcome_recovery",
                        "index": recovery_index + 1,
                        "phase": phase,
                        "trigger": trigger_event,
                        "state_before": placement_state,
                        "scheduler_decision": recovery_schedule,
                    }
                    if recovery_schedule.get("decision") == "complete":
                        recovery_event["completion_admitted"] = False
                        recovery_event["completion_rejection_reason"] = (
                            "measured outcome predicate remains false"
                        )
                    elif recovery_schedule.get("operation_kind") == "actuation":
                        (
                            obs,
                            terminal,
                            recovery_actuator_decision,
                            recovery_actuator_latency,
                            recovery_actuator_digest,
                        ) = actuator_transition_handler(
                            obs,
                            last_action,
                            phase_label="place:measured_outcome_not_met",
                            observation_prefix=(
                                f"actuator_outcome_{stage_index:02d}_place_"
                                f"{recovery_index + 1}"
                            ),
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            recovery_actuator_execution,
                        ) = _execute_binary_actuator_tool(
                            env,
                            obs,
                            last_action,
                            recovery_actuator_decision,
                            initial_object_z=initial_object_z,
                        )
                        reconcile_carry_latch_after_actuation(
                            recovery_actuator_execution,
                            source="place:measured_outcome_not_met",
                        )
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "execution"
                        ] = recovery_actuator_execution
                        actuator_latency += recovery_actuator_latency
                        actuator_digest = recovery_actuator_digest
                        actuator_decision = recovery_actuator_decision
                        actuator_execution = recovery_actuator_execution
                        recovery_event["actuator_decision"] = (
                            recovery_actuator_decision
                        )
                        recovery_event["actuator_execution"] = (
                            recovery_actuator_execution
                        )
                        actuator_state_after = recovery_actuator_execution[
                            "state_after"
                        ]
                        if (
                            recovery_actuator_execution["engaged_after"]
                            and not recovery_actuator_execution["engaged_before"]
                            and actuator_state_after["current_contact"].get(
                                "touch"
                            )
                        ):
                            latched_carry_offset = (
                                _eef_position(env)
                                - _movable_object_position(env)
                            )
                            latched_carry_quaternion = _eef_quaternion(env)
                            latched_rgbd_axis_references = {}
                            for object_id in trackable_object_ids:
                                try:
                                    axis_observation = (
                                        _rgbd_object_axis_observation(
                                            env,
                                            prim_label_fragment=(
                                                f"/scene/{object_id}"
                                            ),
                                        )
                                    )
                                    latched_rgbd_axis_references[object_id] = (
                                        np.asarray(
                                            axis_observation[
                                                "major_axis_camera"
                                            ],
                                            dtype=np.float64,
                                        )
                                    )
                                except ValueError:
                                    continue
                            recovery_event["carry_latched_after_actuation"] = {
                                "eef_minus_object_m": (
                                    latched_carry_offset.tolist()
                                ),
                                "tracked_rgbd_objects": sorted(
                                    latched_rgbd_axis_references
                                ),
                            }
                    elif recovery_schedule.get("operation_kind") == "motion":
                        recovery_contact = placement_state.get(
                            "current_contact", {}
                        )
                        carry_observed = bool(
                            float(last_action[0, 7].detach().cpu()) > 0.5
                            and isinstance(recovery_contact, dict)
                            and recovery_contact.get("touch")
                            and latched_carry_offset is not None
                        )
                        recovery_motion_decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": "measured_stage_outcome_not_met",
                                "phase": "place:outcome_recovery",
                                "iteration": recovery_index + 1,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "scheduler_decision": recovery_schedule,
                                "measured_outcome": trigger_event,
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if carry_observed
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if carry_observed
                                        else {}
                                    ),
                                    "observed_clearance": (
                                        "sim6.privileged_object_to_support_height_adapter"
                                        if carry_observed
                                        else None
                                    ),
                                },
                            },
                        )
                        recovery_event["motion_decision"] = (
                            recovery_motion_decision
                        )
                        if recovery_motion_decision.get("decision") != "execute":
                            raise RuntimeError(
                                "Model did not admit a bounded recovery motion "
                                f"after measured placement failure: "
                                f"{recovery_motion_decision}"
                            )
                        recovery_target = torch.tensor(
                            recovery_motion_decision["target_xyz_m"],
                            dtype=torch.float32,
                        )
                        recovery_target_quaternion = torch.tensor(
                            recovery_motion_decision[
                                "target_quaternion_wxyz"
                            ],
                            dtype=torch.float32,
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            recovery_motion_report,
                        ) = _move_eef_to_target(
                            env,
                            obs,
                            last_action,
                            recovery_target,
                            recovery_target_quaternion,
                            "place:outcome_recovery",
                            gripper_closed=bool(
                                float(last_action[0, 7].detach().cpu()) > 0.5
                            ),
                            initial_object_z=initial_object_z,
                            executor_config=dict(
                                recovery_motion_decision.get(
                                    "executor_config"
                                )
                                or {}
                            ),
                            carry_reference_offset=(
                                latched_carry_offset if carry_observed else None
                            ),
                            rgbd_axis_references=(
                                latched_rgbd_axis_references
                                if carry_observed
                                else {}
                            ),
                            checkpoint_callback=motion_checkpoint_handler,
                        )
                        recovery_event["motion_report"] = recovery_motion_report
                    else:
                        raise RuntimeError(
                            "Placement outcome scheduler returned unsupported "
                            f"operation: {recovery_schedule}"
                        )
                    placement_state = _state(env, initial_object_z)
                    recovery_event["state_after"] = placement_state
                    placement_outcome_recoveries.append(recovery_event)
                    episode_trace["recoveries"].append(recovery_event)
                    centered = bool(
                        placement_state["object_target_contact_proxy"]
                        or (
                            placement_state["object_target_xy_error_m"]
                            <= args_cli.center_tolerance
                            and abs(
                                placement_state[
                                    "object_height_above_target_m"
                                ]
                                - args_cli.release_height
                            )
                            <= args_cli.release_height_tolerance
                        )
                    )
                    _write_trace(trace_path, episode_trace)
                centering_report = {
                    "enabled": True,
                    "controller": "observation_bound_model_motion_tool",
                    "legacy_local_xy_z_controller_used": False,
                    "converged": centered,
                    "object_target_contact_proxy": placement_state[
                        "object_target_contact_proxy"
                    ],
                    "xy_error_after_m": placement_state[
                        "object_target_xy_error_m"
                    ],
                    "height_above_plate_after_m": placement_state[
                        "object_height_above_target_m"
                    ],
                    "motion_report": motion_report,
                    "measured_outcome_recoveries": (
                        placement_outcome_recoveries
                    ),
                }
                episode_trace["stages"][-1]["measured_outcome_recoveries"] = (
                    placement_outcome_recoveries
                )
                episode_trace["stages"][-1]["eef_after_xyz"] = (
                    _eef_position(env).tolist()
                )
                episode_trace["stages"][-1]["object_after_xyz"] = (
                    _movable_object_position(env).tolist()
                )
                episode_trace["residual_centering"] = centering_report
                tests["centering"] = centered
                _test_line(
                    7,
                    "model-governed plate placement",
                    centered,
                    f"xy_error={placement_state['object_target_xy_error_m']:.3f}m "
                    f"height={placement_state['object_height_above_target_m']:.3f}m "
                    f"contact={placement_state['object_target_contact_proxy']}",
                )
                _write_trace(trace_path, episode_trace)
                if not centered:
                    raise RuntimeError(
                        "Measured placement recovery budget exhausted without "
                        "satisfying the runtime release predicate"
                    )

            if phase == "lift":
                lift_outcome_recoveries: list[dict[str, Any]] = []
                previous_lift_recovery_motion_outcome = (
                    recovery_motion_handoff_from_report(motion_report)
                )
                lifted = (
                    float(object_now[2]) - initial_object_z
                    >= args_cli.minimum_transport_lift
                )
                for recovery_index in range(
                    args_cli.max_lift_recovery_operations
                ):
                    if lifted or terminal:
                        break
                    recovery_state_before = _state(env, initial_object_z)
                    observed_lift_m = float(
                        recovery_state_before["object_lift_m"]
                    )
                    if (
                        latest_grasp_attempt is not None
                        and not any(
                            item["attempt_id"]
                            == latest_grasp_attempt["attempt_id"]
                            for item in failed_grasp_attempts
                        )
                    ):
                        failed_grasp_attempts.append(
                            {
                                **latest_grasp_attempt,
                                "outcome": "object_did_not_follow_lift",
                                "observed_object_lift_m": observed_lift_m,
                                "fresh_failure_state": {
                                    "gripper_closed_fraction": (
                                        recovery_state_before[
                                            "gripper_closed_fraction"
                                        ]
                                    ),
                                    "touch": recovery_state_before[
                                        "current_contact"
                                    ].get("touch"),
                                    "fingertip_object_distance_m": (
                                        recovery_state_before[
                                            "fingertip_object_distance_m"
                                        ]
                                    ),
                                },
                            }
                        )
                    if (
                        len(failed_grasp_attempts)
                        >= args_cli.max_failed_grasp_attempts
                    ):
                        print(
                            "[outcome recovery] failed-grasp budget exhausted "
                            f"after {len(failed_grasp_attempts)} physically "
                            "tested poses",
                            flush=True,
                        )
                        break
                    actuator_command_engaged = bool(
                        float(last_action[0, 7].detach().cpu()) > 0.5
                    )
                    failed_grasp_pose_comparisons = (
                        compare_grasp_pose_to_failed_attempts(
                            failed_attempts=failed_grasp_attempts,
                            current_eef_xyz_m=recovery_state_before[
                                "eef_gripper_base_xyz"
                            ],
                            current_object_xyz_m=recovery_state_before[
                                "movable_object_xyz"
                            ],
                            current_eef_quaternion_wxyz=recovery_state_before[
                                "eef_gripper_base_quaternion_wxyz"
                            ],
                        )
                    )
                    trigger_event = {
                        "type": "measured_stage_outcome_not_met",
                        "predicate_id": "object.lift_above_minimum",
                        "observed_value_m": observed_lift_m,
                        "minimum_value_m": args_cli.minimum_transport_lift,
                        "instruction": args_cli.instruction,
                        "failed_grasp_attempts": list(failed_grasp_attempts),
                        "failed_grasp_pose_comparisons": (
                            failed_grasp_pose_comparisons
                        ),
                        "actuator_outcome_invalidated": bool(
                            failed_grasp_attempts
                            and actuator_command_engaged
                        ),
                        "prior_failed_actuator_outcome_observed": bool(
                            failed_grasp_attempts
                        ),
                        "previous_recovery_motion_outcome": (
                            previous_lift_recovery_motion_outcome
                        ),
                    }
                    print(
                        "[outcome recovery] measured object lift "
                        f"{observed_lift_m:.3f}m is below "
                        f"{args_cli.minimum_transport_lift:.3f}m; requesting "
                        f"fresh operation {recovery_index + 1}/"
                        f"{args_cli.max_lift_recovery_operations} "
                        f"(failed grasps={len(failed_grasp_attempts)}/"
                        f"{args_cli.max_failed_grasp_attempts})",
                        flush=True,
                    )
                    (
                        obs,
                        terminal,
                        recovery_schedule,
                        recovery_scheduler_latency,
                        recovery_scheduler_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label="lift:measured_outcome_not_met",
                        observation_prefix=(
                            f"scheduler_outcome_{stage_index:02d}_lift_"
                            f"{recovery_index + 1}"
                        ),
                        motion_report={
                            "converged": False,
                            "yielded_to_scheduler": True,
                            "recovery_request": trigger_event,
                        },
                        trigger_event=trigger_event,
                    )
                    scheduler_latency += recovery_scheduler_latency
                    scheduler_digest = recovery_scheduler_digest
                    recovery_event: dict[str, Any] = {
                        "kind": "measured_stage_outcome_recovery",
                        "index": recovery_index + 1,
                        "phase": phase,
                        "trigger": trigger_event,
                        "state_before": recovery_state_before,
                        "scheduler_decision": recovery_schedule,
                    }
                    if recovery_schedule.get("decision") == "complete":
                        recovery_event["completion_admitted"] = False
                        recovery_event["completion_rejection_reason"] = (
                            "measured outcome predicate remains false"
                        )
                    elif recovery_schedule.get("operation_kind") == "actuation":
                        (
                            obs,
                            terminal,
                            recovery_actuator_decision,
                            recovery_actuator_latency,
                            recovery_actuator_digest,
                        ) = actuator_transition_handler(
                            obs,
                            last_action,
                            phase_label="lift:measured_outcome_not_met",
                            observation_prefix=(
                                f"actuator_outcome_{stage_index:02d}_lift_"
                                f"{recovery_index + 1}"
                            ),
                            trigger_event=trigger_event,
                            scheduler_dispatch=recovery_schedule,
                            yield_on_hold=True,
                        )
                        actuator_latency += recovery_actuator_latency
                        actuator_digest = recovery_actuator_digest
                        actuator_decision = recovery_actuator_decision
                        recovery_event["actuator_decision"] = (
                            recovery_actuator_decision
                        )
                        if recovery_actuator_decision.get("decision") != "execute":
                            recovery_event["yielded_to_scheduler"] = True
                            episode_trace["actuator_tool_protocol"]["calls"][-1][
                                "scheduler_handoff"
                            ] = True
                        else:
                            (
                                obs,
                                terminal,
                                last_action,
                                recovery_actuator_execution,
                            ) = _execute_binary_actuator_tool(
                                env,
                                obs,
                                last_action,
                                recovery_actuator_decision,
                                initial_object_z=initial_object_z,
                            )
                            reconcile_carry_latch_after_actuation(
                                recovery_actuator_execution,
                                source="lift:measured_outcome_not_met",
                            )
                            episode_trace["actuator_tool_protocol"]["calls"][-1][
                                "execution"
                            ] = recovery_actuator_execution
                            actuator_execution = recovery_actuator_execution
                            recovery_event["actuator_execution"] = (
                                recovery_actuator_execution
                            )
                            if (
                                recovery_actuator_execution.get(
                                    "requested_state"
                                )
                                == "engage"
                            ):
                                recovery_event["grasp_attempt"] = (
                                    capture_grasp_attempt(
                                        "lift:measured_outcome_recovery"
                                    )
                                )
                                latched_carry_offset = (
                                    _eef_position(env)
                                    - _movable_object_position(env)
                                )
                                latched_carry_quaternion = _eef_quaternion(env)
                                latched_rgbd_axis_references = {}
                                for object_id in trackable_object_ids:
                                    try:
                                        axis_observation = (
                                            _rgbd_object_axis_observation(
                                                env,
                                                prim_label_fragment=(
                                                    f"/scene/{object_id}"
                                                ),
                                            )
                                        )
                                        latched_rgbd_axis_references[object_id] = (
                                            np.asarray(
                                                axis_observation[
                                                    "major_axis_camera"
                                                ],
                                                dtype=np.float64,
                                            )
                                        )
                                    except ValueError:
                                        continue
                                recovery_event["carry_latched_after_actuation"] = {
                                    "eef_minus_object_m": (
                                        latched_carry_offset.tolist()
                                    ),
                                    "eef_quaternion_wxyz": (
                                        latched_carry_quaternion.tolist()
                                    ),
                                    "tracked_rgbd_objects": sorted(
                                        latched_rgbd_axis_references
                                    ),
                                }
                    elif recovery_schedule.get("operation_kind") == "motion":
                        recovery_contact = recovery_state_before.get(
                            "current_contact", {}
                        )
                        carry_observed = bool(
                            float(last_action[0, 7].detach().cpu()) > 0.5
                            and isinstance(recovery_contact, dict)
                            and recovery_contact.get("touch")
                            and latched_carry_offset is not None
                        )
                        recovery_motion_decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": "measured_stage_outcome_not_met",
                                "phase": "lift:outcome_recovery",
                                "iteration": recovery_index + 1,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "scheduler_decision": recovery_schedule,
                                "measured_outcome": trigger_event,
                                "previous_recovery_motion_outcome": (
                                    previous_lift_recovery_motion_outcome
                                ),
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if carry_observed
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if carry_observed
                                        else {}
                                    ),
                                    "observed_clearance": None,
                                },
                            },
                        )
                        recovery_event["motion_decision"] = (
                            recovery_motion_decision
                        )
                        if recovery_motion_decision.get("decision") != "execute":
                            raise RuntimeError(
                                "Model did not admit a bounded recovery motion "
                                f"after measured lift failure: "
                                f"{recovery_motion_decision}"
                            )
                        recovery_target = torch.tensor(
                            recovery_motion_decision["target_xyz_m"],
                            dtype=torch.float32,
                        )
                        recovery_target_quaternion = torch.tensor(
                            recovery_motion_decision[
                                "target_quaternion_wxyz"
                            ],
                            dtype=torch.float32,
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            recovery_motion_report,
                        ) = _move_eef_to_target(
                            env,
                            obs,
                            last_action,
                            recovery_target,
                            recovery_target_quaternion,
                            "lift:outcome_recovery",
                            gripper_closed=bool(
                                float(last_action[0, 7].detach().cpu()) > 0.5
                            ),
                            initial_object_z=initial_object_z,
                            executor_config=dict(
                                recovery_motion_decision.get(
                                    "executor_config"
                                )
                                or {}
                            ),
                            carry_reference_offset=(
                                latched_carry_offset if carry_observed else None
                            ),
                            rgbd_axis_references=(
                                latched_rgbd_axis_references
                                if carry_observed
                                else {}
                            ),
                            checkpoint_callback=motion_checkpoint_handler,
                        )
                        recovery_event["motion_report"] = recovery_motion_report
                        previous_lift_recovery_motion_outcome = (
                            recovery_motion_handoff_from_report(
                                recovery_motion_report
                            )
                        )
                    else:
                        raise RuntimeError(
                            "Outcome recovery scheduler returned unsupported "
                            f"operation: {recovery_schedule}"
                        )
                    recovery_state_after = _state(env, initial_object_z)
                    recovery_event["state_after"] = recovery_state_after
                    lift_outcome_recoveries.append(recovery_event)
                    episode_trace["recoveries"].append(recovery_event)
                    lifted = bool(
                        recovery_state_after["object_lift_m"]
                        >= args_cli.minimum_transport_lift
                    )
                    _write_trace(trace_path, episode_trace)

                object_now = _movable_object_position(env)
                eef = _eef_position(env)
                tests["lift"] = lifted
                episode_trace["measured_lift_outcome_recovery"].update(
                    {
                        "operation_count": len(lift_outcome_recoveries),
                        "failed_grasp_attempt_count": len(
                            failed_grasp_attempts
                        ),
                        "failed_grasp_attempts": failed_grasp_attempts,
                        "outcome_satisfied": lifted,
                    }
                )
                _test_line(
                    6,
                    "physical object lift",
                    lifted,
                    f"delta_z={float(object_now[2]) - initial_object_z:.3f}m "
                    f"operations={len(lift_outcome_recoveries)} "
                    f"failed_grasps={len(failed_grasp_attempts)}",
                )
                episode_trace["stages"][-1]["measured_outcome_recoveries"] = (
                    lift_outcome_recoveries
                )
                episode_trace["stages"][-1]["eef_after_xyz"] = eef.tolist()
                episode_trace["stages"][-1]["object_after_xyz"] = (
                    object_now.tolist()
                )
                if not lifted:
                    _write_trace(trace_path, episode_trace)
                    raise RuntimeError(
                        "Measured lift recovery budget exhausted without "
                        "satisfying the runtime outcome predicate: "
                        f"operations={len(lift_outcome_recoveries)}/"
                        f"{args_cli.max_lift_recovery_operations}, "
                        f"failed_grasps={len(failed_grasp_attempts)}/"
                        f"{args_cli.max_failed_grasp_attempts}"
                    )
                latched_carry_offset = _eef_position(env) - object_now
                latched_carry_quaternion = _eef_quaternion(env)
                episode_trace["post_lift_carry_latch"] = {
                    "eef_minus_object_m": latched_carry_offset.tolist(),
                    "eef_quaternion_wxyz": latched_carry_quaternion.tolist(),
                }
                _write_trace(trace_path, episode_trace)
            next_runtime_label = (
                stages[stage_index][0]
                if stage_index < len(stages)
                else None
            )
            if (
                not args_cli.disable_adaptive_ik
                and not task_completed_by_scheduler
                and next_runtime_label == "lift"
                and not terminal
            ):
                (
                    obs,
                    terminal,
                    last_action,
                    transition_report,
                ) = resolve_runtime_transition(
                    obs,
                    last_action,
                    required_capability="supported_loaded_interaction",
                    runtime_label=f"{phase}->{next_runtime_label}",
                    observation_prefix=(
                        f"transition_{stage_index:02d}_{phase}_to_"
                        f"{next_runtime_label}"
                    ),
                )
                episode_trace["stages"][-1][
                    "next_runtime_transition"
                ] = transition_report
                _write_trace(trace_path, episode_trace)
            if task_completed_by_scheduler:
                print(
                    f"[scheduler] physical task declared complete after {phase}; "
                    "skipping remaining runtime-proposed stages",
                    flush=True,
                )
                break
            if terminal:
                break

        if not args_cli.disable_adaptive_ik and not terminal:
            (
                obs,
                terminal,
                last_action,
                release_transition_report,
            ) = resolve_runtime_transition(
                obs,
                last_action,
                required_capability="released_interaction",
                runtime_label="task_motion->retreat",
                observation_prefix="transition_release_to_retreat",
            )
            episode_trace["release_transition_admission"] = (
                release_transition_report
            )
            _write_trace(trace_path, episode_trace)

        if args_cli.disable_release_retreat:
            retreat_report = {
                "enabled": False,
                "converged": None,
                "reason": "disabled by command line",
            }
            tests["detachment"] = True
            _test_line(8, "open-gripper retreat and detachment", True, "disabled")
        else:
            obs, terminal, last_action, retreat_report = _retreat_after_release(
                env,
                obs,
                last_action,
            )
            detached = bool(retreat_report["converged"])
            tests["detachment"] = detached
            episode_trace["release_retreat"] = retreat_report
            _write_trace(trace_path, episode_trace)
            _test_line(
                8,
                "open-gripper retreat and detachment",
                detached,
                f"eef_up={retreat_report['eef_retreat_z_m']:.3f}m "
                f"object_motion={retreat_report['object_motion_during_retreat_m']:.3f}m "
                f"plate_xy={retreat_report['object_target_xy_error_after_m']:.3f}m "
                f"separation={retreat_report['eef_object_separation_before_m']:.3f}→"
                f"{retreat_report['eef_object_separation_after_m']:.3f}m",
            )
            if not detached:
                raise RuntimeError("Open-gripper retreat did not prove clean object detachment")
        episode_trace["release_retreat"] = retreat_report
        _write_trace(trace_path, episode_trace)

        object_final = _movable_object_position(env)
        plate_final = _target_receptacle_position(env)
        final_frame = _single_exterior_frame(obs)
        cv2.imwrite(
            str(args_cli.artifact_dir / "99_final.jpg"),
            cv2.cvtColor(final_frame, cv2.COLOR_RGB2BGR),
        )
        xy_error = float(torch.linalg.norm(object_final[:2] - plate_final[:2]))
        height_above_plate = float(object_final[2] - plate_final[2])
        contact_summary = _active_contact_summary()
        contact_passed = (
            True if args_cli.disable_contact_telemetry else bool(contact_summary["passed"])
        )
        tests["contact_telemetry"] = contact_passed
        _test_line(
            9,
            "real gripper contact telemetry",
            contact_passed,
            "disabled" if args_cli.disable_contact_telemetry else (
                f"coverage={contact_summary['coverage']:.3f} "
                f"touch_samples={contact_summary['touch_samples']} "
                f"peak_net_force={contact_summary['peak_net_force_n']:.3f}N"
            ),
        )
        success = xy_error <= 0.12 and 0.0 <= height_above_plate <= 0.20
        tests["success"] = success
        _test_line(
            10,
            "object-on-target geometric outcome",
            success,
            f"xy_error={xy_error:.3f}m height_above_plate={height_above_plate:.3f}m",
        )
        episode_trace["final"] = {
            "frame": "99_final.jpg",
            "movable_object_xyz": object_final.tolist(),
            "target_receptacle_xyz": plate_final.tolist(),
            "object_target_xy_error_m": xy_error,
            "object_height_above_target_m": height_above_plate,
            "tests": tests,
            "contact_telemetry": contact_summary,
            "all_tests_passed": all(tests.values()),
            "coach_model_calls": model_calls,
        }
        all_tests_passed = all(tests.values())
        episode_trace["status"] = (
            "complete" if all_tests_passed else "failed_not_admitted"
        )
        if episode_recorder is not None and not all_tests_passed:
            episode_trace["training_capture"] = {
                "status": "rejected",
                "reason": "not_all_supervision_and_physical_tests_passed",
                "samples_discarded": episode_recorder.sample_count,
            }
        _write_trace(trace_path, episode_trace)

        if episode_recorder is not None and all_tests_passed:
            ACTIVE_EPISODE_RECORDER = None
            episode_recorder.metadata["outcome"] = {
                "coach_model_calls": model_calls,
                "transport_recovery_count": len(episode_trace["recoveries"]),
                "transport_recovery_outcomes": [
                    recovery.get("outcome")
                    for recovery in episode_trace["recoveries"]
                ],
                "all_supervision_and_physical_tests_passed": True,
            }
            training_row = episode_recorder.publish_success(trace_path=trace_path)
            episode_trace["training_capture"] = {
                "status": "published_success",
                **training_row,
            }
            _write_trace(trace_path, episode_trace)
            print(
                f"Training episode: {training_row['hdf5']} + {training_row['video']} "
                f"({training_row['samples']} samples)",
                flush=True,
            )

        print("=" * 78)
        print(
            f"FINAL: {'PASS' if all(tests.values()) else 'FAIL'} | "
            f"model_calls={model_calls} physics_steps_are_local=True tests={tests}"
        )
        print(f"Frames: {args_cli.artifact_dir}")
        print(f"Trace:  {trace_path}")
        print("=" * 78, flush=True)

        if args_cli.linger_steps > 0:
            print(
                f"[viewer] Holding the final pose for {args_cli.linger_steps} steps...",
                flush=True,
            )
            obs, _ = _hold_joint_action(
                env, obs, last_action, args_cli.linger_steps, gripper_closed=False
            )
        return 0 if all_tests_passed else 2
    except BaseException as error:
        episode_trace["status"] = "failed_not_admitted"
        episode_trace["failure"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        if episode_recorder is not None:
            episode_trace["training_capture"] = {
                "status": "rejected",
                "reason": "exception_before_all_success_gates",
                "samples_discarded": episode_recorder.sample_count,
            }
        episode_trace["contact_telemetry_at_failure"] = _active_contact_summary()
        _write_trace(trace_path, episode_trace)
        raise
    finally:
        ACTIVE_EPISODE_RECORDER = None
        ACTIVE_SENSOR_MONITOR = None
        ACTIVE_SENSOR_SAMPLE_INDEX = 0
        if ros2_sensor_ingress is not None:
            episode_trace["ros2_sensor_ingress"] = {
                "enabled": True,
                "status": (
                    "subscribed"
                    if ros2_sensor_ingress.available
                    else "unavailable_using_simulator_fallback"
                ),
                **ros2_sensor_ingress.status(),
            }
            _write_trace(trace_path, episode_trace)
            ros2_sensor_ingress.stop()
        ACTIVE_ROS2_SENSOR_INGRESS = None
        if episode_recorder is not None:
            episode_recorder.discard()
        end_episode(env)
        env.close()


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except Exception as error:
        print(f"[ER2 TEST SUITE] ERROR: {error}", flush=True)
        traceback.print_exc()
    finally:
        simulation_app.close()
    sys.exit(exit_code)
