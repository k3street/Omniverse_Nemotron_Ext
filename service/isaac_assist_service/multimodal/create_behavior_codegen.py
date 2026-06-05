"""Phase 70b SPEC/CODE-GEN layer — create_behavior rewrite for Isaac Sim 5.x.

Emits valid Isaac Sim 5.x Cortex behavior code from a typed BehaviorConfig.
Live runtime execution stays scaffold; this module is the code-generation
kernel that the tool handler will call.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 70b.
"""
from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "70b"
PHASE_TITLE = "create_behavior rewrite for Isaac Sim 5.x"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 70b",
        "files": [
            "service/isaac_assist_service/multimodal/create_behavior_codegen.py",
            "tests/test_phase_70b_create_behavior_codegen.py",
        ],
    }


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

BehaviorPattern = Literal[
    "pick_place",
    "navigate_to",
    "scan_grid",
    "press_button",
    "follow_path",
    "guard_zone",
    "synchronize_with",
]

ALL_PATTERNS: List[BehaviorPattern] = [
    "pick_place",
    "navigate_to",
    "scan_grid",
    "press_button",
    "follow_path",
    "guard_zone",
    "synchronize_with",
]


def expected_patterns() -> List[BehaviorPattern]:
    """Return the canonical list of all supported behavior patterns."""
    return list(ALL_PATTERNS)


# ---------------------------------------------------------------------------
# Required parameters per pattern
# ---------------------------------------------------------------------------

PATTERN_REQUIRED_PARAMS: Dict[str, List[str]] = {
    "pick_place": ["pick_pose", "place_pose"],
    "navigate_to": ["target_xy"],
    "scan_grid": ["grid_origin", "grid_size", "grid_step"],
    "press_button": ["button_path"],
    "follow_path": ["waypoints"],
    "guard_zone": ["zone_bbox"],
    "synchronize_with": ["partner_robot_path"],
}


# ---------------------------------------------------------------------------
# Behavior templates (Isaac Sim 5.x Cortex API)
# ---------------------------------------------------------------------------

_PICK_PLACE_TEMPLATE = '''\
"""Auto-generated pick-and-place behavior for Isaac Sim 5.x."""
import numpy as np
import isaacsim.cortex.framework
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.df import DfNetwork, DfState
from isaacsim.cortex.framework.behaviors.franka import simple_state_machine


PICK_POSE = {pick_pose}
PLACE_POSE = {place_pose}
ROBOT_PRIM_PATH = "{robot_prim_path}"
END_EFFECTOR_PATH = "{end_effector_path}"


class {behavior_name}(DfState):
    """Pick-and-place behavior state machine."""

    def enter(self):
        self._phase = "approach_pick"
        self._robot = CortexWorld.instance().get_robot(ROBOT_PRIM_PATH)

    def step(self):
        if self._phase == "approach_pick":
            self._robot.arm.send_end_effector(target_pose=PICK_POSE)
            if self._robot.arm.is_converged():
                self._phase = "close_gripper"
        elif self._phase == "close_gripper":
            self._robot.gripper.close()
            self._phase = "approach_place"
        elif self._phase == "approach_place":
            self._robot.arm.send_end_effector(target_pose=PLACE_POSE)
            if self._robot.arm.is_converged():
                self._phase = "open_gripper"
        elif self._phase == "open_gripper":
            self._robot.gripper.open()
            return None  # done
        return self


def make_behavior_network() -> DfNetwork:
    return DfNetwork(initial_state={behavior_name}())
'''

_NAVIGATE_TO_TEMPLATE = '''\
"""Auto-generated navigate-to behavior for Isaac Sim 5.x."""
import numpy as np
import isaacsim.cortex.framework
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.df import DfNetwork, DfState


TARGET_XY = {target_xy}
ROBOT_PRIM_PATH = "{robot_prim_path}"


class {behavior_name}(DfState):
    """Navigate-to behavior state machine."""

    def enter(self):
        self._robot = CortexWorld.instance().get_robot(ROBOT_PRIM_PATH)
        self._target = np.array(TARGET_XY)

    def step(self):
        pos = self._robot.get_world_pose()[0][:2]
        dist = np.linalg.norm(pos - self._target)
        if dist < 0.05:
            return None  # reached goal
        direction = (self._target - pos) / (dist + 1e-9)
        self._robot.set_linear_velocity(direction * 0.3)
        return self


def make_behavior_network() -> DfNetwork:
    return DfNetwork(initial_state={behavior_name}())
'''

_SCAN_GRID_TEMPLATE = '''\
"""Auto-generated scan-grid behavior for Isaac Sim 5.x."""
import numpy as np
import isaacsim.cortex.framework
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.df import DfNetwork, DfState


GRID_ORIGIN = {grid_origin}
GRID_SIZE = {grid_size}
GRID_STEP = {grid_step}
ROBOT_PRIM_PATH = "{robot_prim_path}"
END_EFFECTOR_PATH = "{end_effector_path}"


class {behavior_name}(DfState):
    """Raster grid-scan behavior state machine."""

    def enter(self):
        self._robot = CortexWorld.instance().get_robot(ROBOT_PRIM_PATH)
        origin = np.array(GRID_ORIGIN)
        size = np.array(GRID_SIZE)
        step = float(GRID_STEP)
        xs = np.arange(origin[0], origin[0] + size[0], step)
        ys = np.arange(origin[1], origin[1] + size[1], step)
        self._waypoints = [np.array([x, y, origin[2]]) for i, y in enumerate(ys)
                           for x in (xs if i % 2 == 0 else reversed(xs))]
        self._idx = 0

    def step(self):
        if self._idx >= len(self._waypoints):
            return None  # scan complete
        target = self._waypoints[self._idx]
        self._robot.arm.send_end_effector(target_pose=target)
        if self._robot.arm.is_converged():
            self._idx += 1
        return self


def make_behavior_network() -> DfNetwork:
    return DfNetwork(initial_state={behavior_name}())
'''

_PRESS_BUTTON_TEMPLATE = '''\
"""Auto-generated press-button behavior for Isaac Sim 5.x."""
import numpy as np
import isaacsim.cortex.framework
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.df import DfNetwork, DfState


BUTTON_PATH = "{button_path}"
ROBOT_PRIM_PATH = "{robot_prim_path}"
END_EFFECTOR_PATH = "{end_effector_path}"


class {behavior_name}(DfState):
    """Press-button behavior state machine."""

    def enter(self):
        self._robot = CortexWorld.instance().get_robot(ROBOT_PRIM_PATH)
        self._phase = "approach"
        # Resolve button pose from USD stage
        from pxr import UsdGeom
        import omni.usd
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(BUTTON_PATH)
        xform = UsdGeom.Xformable(prim)
        self._button_pose = xform.ComputeLocalToWorldTransform(0)

    def step(self):
        if self._phase == "approach":
            pre_pose = np.array(self._button_pose.ExtractTranslation()) + np.array([0, 0, 0.05])
            self._robot.arm.send_end_effector(target_pose=pre_pose)
            if self._robot.arm.is_converged():
                self._phase = "press"
        elif self._phase == "press":
            press_pose = np.array(self._button_pose.ExtractTranslation())
            self._robot.arm.send_end_effector(target_pose=press_pose)
            if self._robot.arm.is_converged():
                self._phase = "retract"
        elif self._phase == "retract":
            retract_pose = np.array(self._button_pose.ExtractTranslation()) + np.array([0, 0, 0.1])
            self._robot.arm.send_end_effector(target_pose=retract_pose)
            if self._robot.arm.is_converged():
                return None  # done
        return self


def make_behavior_network() -> DfNetwork:
    return DfNetwork(initial_state={behavior_name}())
'''

_FOLLOW_PATH_TEMPLATE = '''\
"""Auto-generated follow-path behavior for Isaac Sim 5.x."""
import numpy as np
import isaacsim.cortex.framework
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.df import DfNetwork, DfState


WAYPOINTS = {waypoints}
ROBOT_PRIM_PATH = "{robot_prim_path}"
END_EFFECTOR_PATH = "{end_effector_path}"


class {behavior_name}(DfState):
    """Follow-path behavior state machine."""

    def enter(self):
        self._robot = CortexWorld.instance().get_robot(ROBOT_PRIM_PATH)
        self._waypoints = [np.array(wp) for wp in WAYPOINTS]
        self._idx = 0

    def step(self):
        if self._idx >= len(self._waypoints):
            return None  # path complete
        target = self._waypoints[self._idx]
        self._robot.arm.send_end_effector(target_pose=target)
        if self._robot.arm.is_converged():
            self._idx += 1
        return self


def make_behavior_network() -> DfNetwork:
    return DfNetwork(initial_state={behavior_name}())
'''

_GUARD_ZONE_TEMPLATE = '''\
"""Auto-generated guard-zone behavior for Isaac Sim 5.x."""
import numpy as np
import isaacsim.cortex.framework
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.df import DfNetwork, DfState


ZONE_BBOX = {zone_bbox}  # [[x_min, y_min, z_min], [x_max, y_max, z_max]]
ROBOT_PRIM_PATH = "{robot_prim_path}"


class {behavior_name}(DfState):
    """Guard-zone behavior — alerts when objects enter the zone."""

    def enter(self):
        self._robot = CortexWorld.instance().get_robot(ROBOT_PRIM_PATH)
        self._bbox_min = np.array(ZONE_BBOX[0])
        self._bbox_max = np.array(ZONE_BBOX[1])
        self._intruder_detected = False

    def _in_zone(self, pos: np.ndarray) -> bool:
        return np.all(pos >= self._bbox_min) and np.all(pos <= self._bbox_max)

    def step(self):
        # Check robot end-effector position as proxy for intrusion detection
        ee_pos = self._robot.arm.get_end_effector_pose()[0]
        if self._in_zone(np.array(ee_pos)):
            self._intruder_detected = True
            self._robot.stop()
        return self  # guard runs indefinitely


def make_behavior_network() -> DfNetwork:
    return DfNetwork(initial_state={behavior_name}())
'''

_SYNCHRONIZE_WITH_TEMPLATE = '''\
"""Auto-generated synchronize-with behavior for Isaac Sim 5.x."""
import numpy as np
import isaacsim.cortex.framework
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.df import DfNetwork, DfState


PARTNER_ROBOT_PATH = "{partner_robot_path}"
ROBOT_PRIM_PATH = "{robot_prim_path}"


class {behavior_name}(DfState):
    """Synchronize-with behavior — coordinates between two robots."""

    def enter(self):
        world = CortexWorld.instance()
        self._robot = world.get_robot(ROBOT_PRIM_PATH)
        self._partner = world.get_robot(PARTNER_ROBOT_PATH)
        self._handshake_done = False

    def step(self):
        if not self._handshake_done:
            # Wait for partner to reach its ready pose before proceeding
            if self._partner.arm.is_converged():
                self._handshake_done = True
        else:
            # Both robots synchronized — proceed with coordinated task
            return None
        return self


def make_behavior_network() -> DfNetwork:
    return DfNetwork(initial_state={behavior_name}())
'''

BEHAVIOR_TEMPLATES: Dict[str, str] = {
    "pick_place": _PICK_PLACE_TEMPLATE,
    "navigate_to": _NAVIGATE_TO_TEMPLATE,
    "scan_grid": _SCAN_GRID_TEMPLATE,
    "press_button": _PRESS_BUTTON_TEMPLATE,
    "follow_path": _FOLLOW_PATH_TEMPLATE,
    "guard_zone": _GUARD_ZONE_TEMPLATE,
    "synchronize_with": _SYNCHRONIZE_WITH_TEMPLATE,
}


# ---------------------------------------------------------------------------
# BehaviorConfig dataclass
# ---------------------------------------------------------------------------

@dataclass
class BehaviorConfig:
    """Typed configuration for behavior code generation.

    Attributes:
        name: Python class/function name for the generated behavior.
        pattern: One of the supported BehaviorPattern literals.
        robot_prim_path: USD prim path to the robot articulation root.
        end_effector_path: USD prim path to the end-effector (optional for
            navigation/guard patterns).
        params: Pattern-specific parameters (see PATTERN_REQUIRED_PARAMS).
    """

    name: str
    pattern: str
    robot_prim_path: str
    end_effector_path: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Code generator
# ---------------------------------------------------------------------------

class CreateBehaviorCodeGenerator:
    """Generate Isaac Sim 5.x Cortex behavior Python source from a BehaviorConfig."""

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------

    def validate_config(self, cfg: BehaviorConfig) -> List[str]:
        """Validate a BehaviorConfig, returning a list of issue strings.

        Returns an empty list when the config is valid.
        """
        issues: List[str] = []

        # name: non-empty + valid Python identifier
        if not cfg.name:
            issues.append("name must be non-empty")
        elif not cfg.name.isidentifier() or keyword.iskeyword(cfg.name):
            issues.append(
                f"name '{cfg.name}' is not a valid Python identifier"
            )

        # pattern: must be in known set
        if cfg.pattern not in ALL_PATTERNS:
            issues.append(
                f"pattern '{cfg.pattern}' is unknown; valid patterns are: {ALL_PATTERNS}"
            )
        else:
            # pattern-specific required params
            required = PATTERN_REQUIRED_PARAMS.get(cfg.pattern, [])
            for key in required:
                if key not in cfg.params:
                    issues.append(
                        f"pattern '{cfg.pattern}' requires param '{key}'"
                    )

        # robot_prim_path must start with /
        if not cfg.robot_prim_path.startswith("/"):
            issues.append(
                f"robot_prim_path '{cfg.robot_prim_path}' must start with '/'"
            )

        return issues

    # -----------------------------------------------------------------------
    # Code generation
    # -----------------------------------------------------------------------

    def generate(self, cfg: BehaviorConfig) -> str:
        """Select the template for cfg.pattern, fill placeholders, return source."""
        template = BEHAVIOR_TEMPLATES[cfg.pattern]

        # Build placeholder substitutions
        subs: Dict[str, str] = {
            "behavior_name": cfg.name,
            "robot_prim_path": cfg.robot_prim_path,
            "end_effector_path": cfg.end_effector_path,
        }
        # Pattern-specific params — inject as Python literals
        for key, value in cfg.params.items():
            if isinstance(value, str):
                subs[key] = repr(value)
            else:
                subs[key] = repr(value)

        # Fill only placeholders that are present in the template
        result = template
        for placeholder, substitution in subs.items():
            result = result.replace("{" + placeholder + "}", substitution)

        return result

    # -----------------------------------------------------------------------
    # Post-generation validation
    # -----------------------------------------------------------------------

    def validate_generated(self, code: str) -> List[str]:
        """Validate generated code for correctness, returning a list of issues.

        Returns an empty list when the code passes all checks.
        """
        issues: List[str] = []

        # Must NOT use deprecated pre-5.x MotionCommander instantiation
        if "MotionCommander('" in code or 'MotionCommander("' in code:
            issues.append(
                "Code uses deprecated MotionCommander('/path') pattern "
                "(pre-5.x API); use CortexWorld / DfNetwork instead"
            )

        # Must NOT use deprecated CortexRobot motion_commander kwarg
        if "CortexRobot(" in code and "motion_commander=" in code:
            issues.append(
                "Code uses deprecated CortexRobot(..., motion_commander=...) pattern "
                "(pre-5.x API); use CortexWorld.get_robot() instead"
            )

        # Must NOT use deprecated pre-5.x Cortex namespace
        if "omni.isaac.cortex" in code:
            issues.append(
                "uses deprecated pre-5.x Cortex namespace; "
                "expected `isaacsim.cortex.framework`"
            )

        # Must import isaacsim.cortex.framework
        if "isaacsim.cortex.framework" not in code:
            issues.append(
                "Code does not import isaacsim.cortex.framework; "
                "add 'import isaacsim.cortex.framework' or equivalent"
            )

        # Must define a class or function
        has_class = bool(re.search(r"^\s*class\s+\w+", code, re.MULTILINE))
        has_function = bool(re.search(r"^\s*def\s+\w+", code, re.MULTILINE))
        if not has_class and not has_function:
            issues.append(
                "Generated code defines neither a class nor a function"
            )

        return issues
