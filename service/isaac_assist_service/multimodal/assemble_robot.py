"""Phase 70 — assemble_robot implementation.

Composes a multi-body robot from typed parts (base, arm-link chain,
gripper, sensors). The actual USD authoring is opus-runtime, but this
module lands the assembly algorithm: it validates parts, computes
joint chain topology, generates the prim hierarchy + reference list +
joint specs that the Kit RPC layer would author.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 70.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

PHASE_ID = 70
PHASE_TITLE = "assemble_robot implementation"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 70",
    }


Vec3 = Tuple[float, float, float]
PartCategory = Literal["base", "arm_link", "gripper", "sensor", "end_effector_extension"]
JointType = Literal["fixed", "revolute", "prismatic", "spherical"]


@dataclass
class RobotPart:
    """One assembly part with a category, asset reference, and attach point."""
    name: str
    category: PartCategory
    asset_ref: str
    parent_attach_point: str
    self_attach_point: str = "base"
    joint_type: JointType = "fixed"
    joint_axis: Optional[Literal["X", "Y", "Z"]] = None
    joint_lower_limit: Optional[float] = None
    joint_upper_limit: Optional[float] = None
    translate: Vec3 = (0.0, 0.0, 0.0)
    rotate_xyz_deg: Vec3 = (0.0, 0.0, 0.0)
    scale: Vec3 = (1.0, 1.0, 1.0)
    mass_kg: Optional[float] = None
    semantic_tags: List[str] = field(default_factory=list)


@dataclass
class AssemblySpec:
    """Top-level assembly: a base part + ordered child parts."""
    robot_name: str
    base_part: RobotPart
    children: List[RobotPart] = field(default_factory=list)
    robot_prim_path: str = "/World/Robot"


@dataclass
class JointSpec:
    """One joint between two parts."""
    name: str
    joint_type: JointType
    parent_prim: str
    child_prim: str
    parent_attach_point: str
    child_attach_point: str
    axis: Optional[str] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    translate: Vec3 = (0.0, 0.0, 0.0)
    rotate_xyz_deg: Vec3 = (0.0, 0.0, 0.0)


@dataclass
class AssemblyResult:
    """Output of assemble(): prim hierarchy, joints, validation issues."""
    robot_prim_path: str
    prim_paths: List[str]
    references: List[Tuple[str, str]]
    joints: List[JointSpec]
    articulation_root: str
    end_effector_path: Optional[str]
    semantic_labels: Dict[str, List[str]]
    issues: List[str]

    @property
    def success(self) -> bool:
        """Return True when there are no non-warning issues in the assembly result."""
        return not any(
            issue for issue in self.issues if not issue.startswith("WARN:")
        )


KNOWN_ATTACH_POINTS: Dict[PartCategory, set] = {
    "base": {"world", "base_link", "ground"},
    "arm_link": {"flange", "tool0", "tcp", "link_end", "base"},
    "gripper": {"base", "tcp"},
    "sensor": {"mount", "base"},
    "end_effector_extension": {"base", "tcp"},
}

CHILD_ORDER_RULES: List[Tuple[PartCategory, PartCategory]] = [
    ("base", "arm_link"),
    ("arm_link", "arm_link"),
    ("arm_link", "gripper"),
    ("arm_link", "sensor"),
    ("arm_link", "end_effector_extension"),
    ("gripper", "sensor"),
    ("end_effector_extension", "gripper"),
]


def validate_part(part: RobotPart) -> List[str]:
    """Return a list of validation issues for a single part."""
    issues: List[str] = []
    if not part.name:
        issues.append("part.name is empty")
    if not part.asset_ref:
        issues.append(f"part {part.name}: asset_ref is empty")
    if part.joint_type in ("revolute", "prismatic") and part.joint_axis is None:
        issues.append(
            f"part {part.name}: joint_type={part.joint_type} requires joint_axis"
        )
    if part.joint_axis is not None and part.joint_axis not in ("X", "Y", "Z"):
        issues.append(
            f"part {part.name}: joint_axis must be X/Y/Z, got {part.joint_axis}"
        )
    if (
        part.joint_lower_limit is not None
        and part.joint_upper_limit is not None
        and part.joint_lower_limit >= part.joint_upper_limit
    ):
        issues.append(
            f"part {part.name}: joint limits inverted (lower {part.joint_lower_limit} >= upper {part.joint_upper_limit})"
        )
    if part.mass_kg is not None and part.mass_kg <= 0:
        issues.append(f"part {part.name}: mass_kg must be positive")
    return issues


def validate_attachment(parent_category: PartCategory, child: RobotPart) -> List[str]:
    """Check that a parent-to-child attachment is legal."""
    issues: List[str] = []
    if (parent_category, child.category) not in CHILD_ORDER_RULES:
        issues.append(
            f"WARN: unusual attachment {parent_category} -> {child.category} for part {child.name}"
        )
    known_parent_points = KNOWN_ATTACH_POINTS.get(parent_category, set())
    if known_parent_points and child.parent_attach_point not in known_parent_points:
        issues.append(
            f"WARN: parent_attach_point '{child.parent_attach_point}' not in known set "
            f"for {parent_category}: {sorted(known_parent_points)}"
        )
    known_self_points = KNOWN_ATTACH_POINTS.get(child.category, set())
    if known_self_points and child.self_attach_point not in known_self_points:
        issues.append(
            f"WARN: self_attach_point '{child.self_attach_point}' not in known set "
            f"for {child.category}: {sorted(known_self_points)}"
        )
    return issues


def assemble(spec: AssemblySpec) -> AssemblyResult:
    """Compose the spec into a prim hierarchy + joint list."""
    issues: List[str] = []
    issues.extend(validate_part(spec.base_part))

    if spec.base_part.category != "base":
        issues.append(
            f"base_part category must be 'base', got '{spec.base_part.category}'"
        )

    if not spec.robot_name:
        issues.append("robot_name is empty")
    if not spec.robot_prim_path.startswith("/"):
        issues.append(f"robot_prim_path must start with /, got {spec.robot_prim_path}")

    seen_names = {spec.base_part.name}
    for child in spec.children:
        issues.extend(validate_part(child))
        if child.name in seen_names:
            issues.append(f"duplicate part name: {child.name}")
        seen_names.add(child.name)

    base_path = f"{spec.robot_prim_path}/{_safe_prim_segment(spec.base_part.name)}"
    prim_paths: List[str] = [base_path]
    references: List[Tuple[str, str]] = [(base_path, spec.base_part.asset_ref)]
    joints: List[JointSpec] = []
    semantic_labels: Dict[str, List[str]] = {}
    if spec.base_part.semantic_tags:
        semantic_labels[base_path] = list(spec.base_part.semantic_tags)

    joints.append(
        JointSpec(
            name="world_to_base",
            joint_type="fixed",
            parent_prim="/World",
            child_prim=base_path,
            parent_attach_point="world",
            child_attach_point=spec.base_part.self_attach_point or "base",
            translate=spec.base_part.translate,
            rotate_xyz_deg=spec.base_part.rotate_xyz_deg,
        )
    )

    chain_tip_path = base_path
    chain_tip_category: PartCategory = spec.base_part.category
    end_effector_path: Optional[str] = None
    last_arm_link_path = base_path
    last_arm_link_category: PartCategory = spec.base_part.category

    for child in spec.children:
        if child.category == "arm_link":
            parent_path = chain_tip_path
            parent_category = chain_tip_category
        elif child.category in ("gripper", "end_effector_extension"):
            parent_path = last_arm_link_path
            parent_category = last_arm_link_category
        elif child.category == "sensor":
            parent_path = chain_tip_path
            parent_category = chain_tip_category
        else:
            parent_path = chain_tip_path
            parent_category = chain_tip_category

        issues.extend(validate_attachment(parent_category, child))

        child_path = f"{parent_path}/{_safe_prim_segment(child.name)}"
        prim_paths.append(child_path)
        references.append((child_path, child.asset_ref))
        if child.semantic_tags:
            semantic_labels[child_path] = list(child.semantic_tags)

        parent_segment = _safe_prim_segment(parent_path.rsplit('/', 1)[-1])
        joints.append(
            JointSpec(
                name=f"{parent_segment}_to_{_safe_prim_segment(child.name)}",
                joint_type=child.joint_type,
                parent_prim=parent_path,
                child_prim=child_path,
                parent_attach_point=child.parent_attach_point,
                child_attach_point=child.self_attach_point,
                axis=child.joint_axis,
                lower_limit=child.joint_lower_limit,
                upper_limit=child.joint_upper_limit,
                translate=child.translate,
                rotate_xyz_deg=child.rotate_xyz_deg,
            )
        )

        if child.category == "arm_link":
            chain_tip_path = child_path
            chain_tip_category = child.category
            last_arm_link_path = child_path
            last_arm_link_category = child.category
        elif child.category in ("gripper", "end_effector_extension"):
            end_effector_path = child_path
            chain_tip_path = child_path
            chain_tip_category = child.category

    if end_effector_path is None and last_arm_link_path != base_path:
        end_effector_path = last_arm_link_path

    return AssemblyResult(
        robot_prim_path=spec.robot_prim_path,
        prim_paths=prim_paths,
        references=references,
        joints=joints,
        articulation_root=base_path,
        end_effector_path=end_effector_path,
        semantic_labels=semantic_labels,
        issues=issues,
    )


def _safe_prim_segment(name: str) -> str:
    """Sanitize a part name into a USD prim path segment."""
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
    if not cleaned:
        return "Part"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def make_demo_franka_with_robotiq() -> AssemblySpec:
    """Reference example: Franka Panda + Robotiq 2F-85 gripper."""
    base = RobotPart(
        name="franka_base",
        category="base",
        asset_ref="omniverse://localhost/Robots/Franka/panda.usd",
        parent_attach_point="world",
        self_attach_point="base_link",
        joint_type="fixed",
        mass_kg=18.0,
        semantic_tags=["robot", "manipulator", "7dof"],
    )
    arm_tip = RobotPart(
        name="panda_link8",
        category="arm_link",
        asset_ref="omniverse://localhost/Robots/Franka/panda_link8.usd",
        parent_attach_point="flange",
        self_attach_point="base",
        joint_type="revolute",
        joint_axis="Z",
        joint_lower_limit=-2.96,
        joint_upper_limit=2.96,
        translate=(0.0, 0.0, 0.107),
        mass_kg=0.4,
    )
    gripper = RobotPart(
        name="robotiq_2f85",
        category="gripper",
        asset_ref="omniverse://localhost/Robots/Robotiq/2f85.usd",
        parent_attach_point="tool0",
        self_attach_point="base",
        joint_type="fixed",
        mass_kg=0.9,
        semantic_tags=["gripper", "parallel_jaw"],
    )
    return AssemblySpec(
        robot_name="franka_with_robotiq",
        base_part=base,
        children=[arm_tip, gripper],
        robot_prim_path="/World/Robot",
    )


def make_demo_three_link_arm() -> AssemblySpec:
    """3-piece arm + base + gripper (matches the spec gate)."""
    base = RobotPart(
        name="arm_base",
        category="base",
        asset_ref="omniverse://localhost/Robots/Demo/base.usd",
        parent_attach_point="world",
        self_attach_point="base_link",
        joint_type="fixed",
    )
    link_1 = RobotPart(
        name="link_1",
        category="arm_link",
        asset_ref="omniverse://localhost/Robots/Demo/link_1.usd",
        parent_attach_point="base_link",
        self_attach_point="base",
        joint_type="revolute",
        joint_axis="Z",
        joint_lower_limit=-3.14,
        joint_upper_limit=3.14,
    )
    link_2 = RobotPart(
        name="link_2",
        category="arm_link",
        asset_ref="omniverse://localhost/Robots/Demo/link_2.usd",
        parent_attach_point="flange",
        self_attach_point="base",
        joint_type="revolute",
        joint_axis="Y",
        joint_lower_limit=-1.57,
        joint_upper_limit=1.57,
    )
    link_3 = RobotPart(
        name="link_3",
        category="arm_link",
        asset_ref="omniverse://localhost/Robots/Demo/link_3.usd",
        parent_attach_point="flange",
        self_attach_point="base",
        joint_type="revolute",
        joint_axis="Y",
        joint_lower_limit=-1.57,
        joint_upper_limit=1.57,
    )
    gripper = RobotPart(
        name="gripper",
        category="gripper",
        asset_ref="omniverse://localhost/Robots/Demo/gripper.usd",
        parent_attach_point="tool0",
        self_attach_point="base",
        joint_type="fixed",
    )
    return AssemblySpec(
        robot_name="three_link_arm",
        base_part=base,
        children=[link_1, link_2, link_3, gripper],
        robot_prim_path="/World/Robot",
    )
