"""
task_spec_schema.py
--------------------
Pydantic models for the Task Spec Protocol (docs/02_task_spec_protocol.md).

This schema is the contract between:
  - Pi0.5 Planner (producer)  →  Isaac Assist /manipulation/plan
  - Continuity Manager (consumer)  ←  POST /manipulation/tasks
  - RL Policy Bank (consumer of phase_context extracted by CM)
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HandRole(str, Enum):
    LEAD  = "LEAD"
    ASSIST = "ASSIST"
    IDLE  = "IDLE"


class PredicateType(str, Enum):
    AND              = "AND"
    OR               = "OR"
    GRIPPER_CLOSED   = "gripper_closed"
    GRIPPER_OPEN     = "gripper_open"
    OBJECT_ATTACHED  = "object_attached"
    OBJECT_RESTING_ON = "object_resting_on"
    LIFT_CLEARANCE   = "lift_clearance"
    POSE_REACHED     = "pose_reached"
    FORCE_EXCEEDED   = "force_exceeded"
    DURATION_EXCEEDED = "duration_exceeded"
    OBJECT_LOST      = "object_lost"


class TargetType(str, Enum):
    OBJECT_GRASP    = "object_grasp"
    WORLD_POSE      = "world_pose"
    PLACE_ON_OBJECT = "place_on_object"
    PUSH_VECTOR     = "push_vector"
    PRESS_POINT     = "press_point"


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class Pose(BaseModel):
    """SE(3) pose: 3D position + unit quaternion [x,y,z,w]."""
    position:   List[float] = Field(..., min_length=3, max_length=3)
    quaternion: List[float] = Field(..., min_length=4, max_length=4)


class SceneObject(BaseModel):
    object_id:  str
    cls:        str = Field(alias="class", default="unknown")
    pose:       Pose
    bbox_3d:    List[float] = Field(default_factory=lambda: [0.1, 0.1, 0.1])
    confidence: float = 1.0

    model_config = {"populate_by_name": True}


class SceneSnapshot(BaseModel):
    timestamp:  float
    frame:      str = "base_link"
    objects:    List[SceneObject] = []


# ---------------------------------------------------------------------------
# Predicates (recursive for AND/OR)
# ---------------------------------------------------------------------------

class _BasePredicate(BaseModel):
    type: str


class CompoundPredicate(_BasePredicate):
    """AND / OR of sub-clauses."""
    type:    Literal["AND", "OR"]
    clauses: List["Predicate"]


class GripperClosedPredicate(_BasePredicate):
    type:         Literal["gripper_closed"]
    arm:          Literal["left", "right"]
    min_width_m:  float = 0.0
    max_width_m:  float = 0.12


class GripperOpenPredicate(_BasePredicate):
    type:        Literal["gripper_open"]
    arm:         Literal["left", "right"]
    min_width_m: float = 0.05


class ObjectAttachedPredicate(_BasePredicate):
    type:      Literal["object_attached"]
    object_id: str
    arm:       Literal["left", "right"]


class ObjectRestingOnPredicate(_BasePredicate):
    type:       Literal["object_resting_on"]
    object_id:  str
    support_id: str


class LiftClearancePredicate(_BasePredicate):
    type:             Literal["lift_clearance"]
    object_id:        str
    min_clearance_m:  float = 0.05


class PoseReachedPredicate(_BasePredicate):
    type:           Literal["pose_reached"]
    tolerance_m:    float = 0.02
    tolerance_rad:  float = 0.05


class ForceExceededPredicate(_BasePredicate):
    type:        Literal["force_exceeded"]
    threshold_n: float


class DurationExceededPredicate(_BasePredicate):
    type:        Literal["duration_exceeded"]
    threshold_s: float


class ObjectLostPredicate(_BasePredicate):
    type:           Literal["object_lost"]
    object_id:      str
    missing_frames: int = 15


Predicate = Union[
    CompoundPredicate,
    GripperClosedPredicate,
    GripperOpenPredicate,
    ObjectAttachedPredicate,
    ObjectRestingOnPredicate,
    LiftClearancePredicate,
    PoseReachedPredicate,
    ForceExceededPredicate,
    DurationExceededPredicate,
    ObjectLostPredicate,
]

# Rebuild forward refs after all types are defined
CompoundPredicate.model_rebuild()


# ---------------------------------------------------------------------------
# Semantic targets
# ---------------------------------------------------------------------------

class ObjectGraspTarget(BaseModel):
    type:              Literal["object_grasp"]
    object_id:         str
    approach_axis:     List[float] = Field(default=[0.0, 0.0, -1.0], min_length=3, max_length=3)
    approach_offset_m: float = 0.08


class WorldPoseTarget(BaseModel):
    type: Literal["world_pose"]
    pose: Pose


class PlaceOnObjectTarget(BaseModel):
    type:             Literal["place_on_object"]
    carry_object_id:  str
    support_object_id: str
    place_offset:     List[float] = Field(default=[0.0, 0.0, 0.0], min_length=3, max_length=3)


class PushVectorTarget(BaseModel):
    type:       Literal["push_vector"]
    object_id:  str
    direction:  List[float] = Field(..., min_length=3, max_length=3)
    distance_m: float = 0.1


class PressPointTarget(BaseModel):
    type:       Literal["press_point"]
    world_pose: Pose
    force_n:    float = 5.0


SemanticTarget = Union[
    ObjectGraspTarget,
    WorldPoseTarget,
    PlaceOnObjectTarget,
    PushVectorTarget,
    PressPointTarget,
]


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------

class PhaseConstraints(BaseModel):
    max_force_n:          float = 20.0
    max_duration_s:       float = 15.0
    approach_speed_max_mps: float = 0.20
    carry_object_id:      Optional[str] = None


class Phase(BaseModel):
    phase_index:      int
    skill_name:       str
    hand_assignment:  Dict[Literal["left", "right"], HandRole]
    semantic_target:  SemanticTarget
    constraints:      PhaseConstraints = Field(default_factory=PhaseConstraints)
    success_predicate: Predicate
    failure_predicate: Predicate

    @model_validator(mode="after")
    def _one_lead_per_phase(self) -> "Phase":
        roles = list(self.hand_assignment.values())
        lead_count = roles.count(HandRole.LEAD)
        if lead_count != 1:
            raise ValueError(
                f"Phase {self.phase_index}: exactly one arm must be LEAD, got {lead_count}"
            )
        return self


# ---------------------------------------------------------------------------
# Global constraints + replanning hints
# ---------------------------------------------------------------------------

class WorkspaceBounds(BaseModel):
    x: List[float] = Field(default=[0.20, 0.80], min_length=2, max_length=2)
    y: List[float] = Field(default=[-0.50, 0.50], min_length=2, max_length=2)
    z: List[float] = Field(default=[0.40, 1.20], min_length=2, max_length=2)


class GlobalConstraints(BaseModel):
    workspace_bounds:    WorkspaceBounds = Field(default_factory=WorkspaceBounds)
    max_total_duration_s: float = 60.0


class ReplanningHints(BaseModel):
    if_object_lost:      str = "rescan_workspace"
    if_grasp_fails_3x:   str = "try_alternative_grasp_axis"


# ---------------------------------------------------------------------------
# Root Task Spec
# ---------------------------------------------------------------------------

class TaskSpec(BaseModel):
    spec_version:      str = "1.0"
    task_id:           str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal_text:         str
    embodiment_id:     str = "tenthings_v1_open_arm_bimanual"
    scene_snapshot:    SceneSnapshot
    phases:            List[Phase]
    global_constraints: GlobalConstraints = Field(default_factory=GlobalConstraints)
    replanning_hints:  ReplanningHints = Field(default_factory=ReplanningHints)

    @model_validator(mode="after")
    def _contiguous_phases(self) -> "TaskSpec":
        if not self.phases:
            raise ValueError("Task Spec must have at least one phase")
        indices = [p.phase_index for p in self.phases]
        expected = list(range(len(indices)))
        if sorted(indices) != expected:
            raise ValueError(
                f"Phase indices must be contiguous starting at 0, got {indices}"
            )
        return self

    @model_validator(mode="after")
    def _known_object_ids(self) -> "TaskSpec":
        known = {o.object_id for o in self.scene_snapshot.objects}
        for phase in self.phases:
            t = phase.semantic_target
            refs: List[str] = []
            if hasattr(t, "object_id"):
                refs.append(t.object_id)           # type: ignore[union-attr]
            if hasattr(t, "carry_object_id"):
                refs.append(t.carry_object_id)     # type: ignore[union-attr]
            if hasattr(t, "support_object_id"):
                refs.append(t.support_object_id)   # type: ignore[union-attr]
            for ref in refs:
                if ref not in known:
                    raise ValueError(
                        f"Phase {phase.phase_index} references object '{ref}' "
                        f"not in scene_snapshot ({sorted(known)})"
                    )
        return self


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

class PlanRequest(BaseModel):
    goal_text:      str
    scene_snapshot: Optional[SceneSnapshot] = None   # built from viewport if omitted
    embodiment_id:  str = "tenthings_v1_open_arm_bimanual"
    prior_attempt:  Optional[str] = None


class ReplanRequest(BaseModel):
    task_id:        str
    current_phase:  int
    failure_reason: str
    scene_snapshot: Optional[SceneSnapshot] = None


class PhaseTelemetry(BaseModel):
    """Single phase outcome record emitted by the Continuity Manager."""
    task_id:         str
    goal_text:       str
    phase_index:     int
    skill_name:      str
    outcome:         Literal["success", "fail", "escalate"]
    failure_reason:  Optional[str] = None
    duration_s:      float = 0.0
    ft_peak_n:       float = 0.0
    clamp_count:     int = 0
    predicate_trace: List[str] = []
