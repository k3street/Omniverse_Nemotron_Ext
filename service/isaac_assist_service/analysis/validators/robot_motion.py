"""
RobotMotionValidator — diagnose common wheeled robot movement issues.

Catches:
- Caster/swivel joints with non-zero stiffness (resists free rotation)
- Collision prims without physics material bindings (no friction)
- Conflicting ArticulationControllers writing to the same joints
- Gravity misconfiguration (zero vector, wrong sign, wrong axis)
- Missing physics material on ground/floor surfaces
"""
from typing import List, Dict, Any, Set
from collections import Counter
import uuid

from .base import ValidationRule
from ..models import ValidationFinding, FixSuggestion, ProposedChange


class RobotMotionValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "robot_motion"
        self.pack = "robot_motion"
        self.severity = "warning"
        self.name = "Robot motion diagnostic"
        self.description = (
            "Detects common issues that prevent wheeled robots from moving "
            "correctly — caster impedance, missing friction, conflicting "
            "controllers, and gravity misconfiguration."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])
        og_nodes = stage_data.get("omnigraph_nodes", [])

        findings.extend(self._check_caster_stiffness(prims))
        findings.extend(self._check_floor_friction(prims))
        findings.extend(self._check_gravity(prims))
        findings.extend(self._check_conflicting_controllers(og_nodes))

        return findings

    # ── Caster stiffness ──────────────────────────────────────────────────

    def _check_caster_stiffness(self, prims: List[Dict]) -> List[ValidationFinding]:
        findings = []
        for prim in prims:
            path = prim.get("path", "")
            name = path.rsplit("/", 1)[-1].lower() if "/" in path else path.lower()
            prim_type = prim.get("type", "")
            attrs = prim.get("attributes", {})

            is_caster = any(kw in name for kw in ("caster", "swivel"))
            is_joint = "Joint" in prim_type or "JointAPI" in " ".join(
                prim.get("schemas", [])
            )
            if not (is_caster and is_joint):
                continue

            stiffness = attrs.get(
                "drive:angular:physics:stiffness",
                attrs.get("physics:stiffness"),
            )
            if stiffness is None:
                continue
            try:
                stiffness_f = float(stiffness)
            except (ValueError, TypeError):
                continue

            if stiffness_f > 0:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="robot_motion.caster_stiffness",
                    pack=self.pack,
                    severity="error",
                    prim_path=path,
                    message=f"Caster joint has stiffness={stiffness_f} — resists free rotation.",
                    detail=(
                        f"Joint '{path}' is a caster/swivel joint with "
                        f"stiffness={stiffness_f}. This actively resists "
                        f"rotation, preventing the robot from turning. "
                        f"Set stiffness to 0 for free caster rotation."
                    ),
                    evidence={"stiffness": stiffness_f},
                    auto_fixable=True,
                    fix_suggestion=FixSuggestion(
                        description=f"Set caster stiffness to 0 on '{path}'",
                        confidence=0.95,
                        changes=[
                            ProposedChange(
                                target_type="attribute",
                                target_path=path,
                                action="set",
                                property_name="drive:angular:physics:stiffness",
                                old_value=stiffness_f,
                                new_value=0.0,
                            ),
                        ],
                    ),
                ))

        return findings

    # ── Floor / ground friction ───────────────────────────────────────────

    def _check_floor_friction(self, prims: List[Dict]) -> List[ValidationFinding]:
        findings = []

        # Collect prims that have physics material bindings
        prims_with_phys_mat: Set[str] = set()
        for prim in prims:
            schemas = prim.get("schemas", [])
            if "PhysicsMaterialAPI" in " ".join(schemas):
                prims_with_phys_mat.add(prim.get("path", ""))

        for prim in prims:
            path = prim.get("path", "")
            name = path.rsplit("/", 1)[-1].lower() if "/" in path else path.lower()
            schemas = prim.get("schemas", [])
            attrs = prim.get("attributes", {})

            is_floor = any(kw in name for kw in ("floor", "ground", "plane", "rug", "carpet"))
            has_collision = "PhysicsCollisionAPI" in " ".join(schemas)

            if not (is_floor and has_collision):
                continue

            # Check if this prim has a physics material bound
            has_phys_mat = "PhysicsMaterialAPI" in " ".join(schemas)
            # Also check material binding attribute
            mat_binding = attrs.get("material:binding:physics", "")

            if not has_phys_mat and not mat_binding:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="robot_motion.floor_no_friction",
                    pack=self.pack,
                    severity="error",
                    prim_path=path,
                    message="Floor/ground surface has no physics material — wheels will slip.",
                    detail=(
                        f"Prim '{path}' is a floor/ground collision surface "
                        f"without a bound physics material. Without friction "
                        f"properties, the default is very low friction and "
                        f"wheels will spin in place without moving the robot. "
                        f"Bind a PhysicsMaterial with staticFriction ~0.6."
                    ),
                    evidence={"has_collision": True, "has_physics_material": False},
                    auto_fixable=True,
                    fix_suggestion=FixSuggestion(
                        description=f"Create and bind a physics material to '{path}'",
                        confidence=0.9,
                        changes=[
                            ProposedChange(
                                target_type="prim",
                                target_path=path + "PhysicsMaterial",
                                action="create",
                                property_name="physics:staticFriction",
                                old_value=None,
                                new_value=0.6,
                            ),
                        ],
                    ),
                ))

        return findings

    # ── Gravity misconfiguration ──────────────────────────────────────────

    def _check_gravity(self, prims: List[Dict]) -> List[ValidationFinding]:
        findings = []

        for prim in prims:
            prim_type = prim.get("type", "")
            if prim_type != "PhysicsScene" and "PhysicsScene" not in prim_type:
                continue

            path = prim.get("path", "")
            attrs = prim.get("attributes", {})

            grav_dir = attrs.get("physics:gravityDirection")
            grav_mag = attrs.get("physics:gravityMagnitude")

            # Check zero gravity direction
            if grav_dir is not None:
                try:
                    if isinstance(grav_dir, (list, tuple)):
                        if all(float(v) == 0 for v in grav_dir):
                            findings.append(ValidationFinding(
                                finding_id=uuid.uuid4().hex[:8],
                                rule_id="robot_motion.zero_gravity",
                                pack=self.pack,
                                severity="error",
                                prim_path=path,
                                message="Gravity direction is (0,0,0) — no gravity!",
                                detail=(
                                    f"PhysicsScene '{path}' has gravityDirection="
                                    f"{grav_dir}. With a zero vector, no gravity "
                                    f"is applied. Wheels won't contact the floor. "
                                    f"Set to (0, 0, -1) for Z-up scenes."
                                ),
                                evidence={"gravityDirection": grav_dir},
                                auto_fixable=True,
                                fix_suggestion=FixSuggestion(
                                    description="Set gravity direction to (0, 0, -1)",
                                    confidence=0.95,
                                    changes=[
                                        ProposedChange(
                                            target_type="attribute",
                                            target_path=path,
                                            action="set",
                                            property_name="physics:gravityDirection",
                                            old_value=list(grav_dir),
                                            new_value=[0, 0, -1],
                                        ),
                                    ],
                                ),
                            ))
                except (ValueError, TypeError):
                    pass

            # Check negative or infinite magnitude
            if grav_mag is not None:
                try:
                    mag = float(grav_mag)
                    if mag < 0:
                        findings.append(ValidationFinding(
                            finding_id=uuid.uuid4().hex[:8],
                            rule_id="robot_motion.negative_gravity_mag",
                            pack=self.pack,
                            severity="error",
                            prim_path=path,
                            message=f"Gravity magnitude is negative ({mag}).",
                            detail=(
                                f"PhysicsScene '{path}' has gravityMagnitude="
                                f"{mag}. Negative magnitude combined with the "
                                f"direction vector may invert gravity or cause "
                                f"numerical issues. Set to 9.81."
                            ),
                            evidence={"gravityMagnitude": mag},
                            auto_fixable=True,
                        ))
                except (ValueError, TypeError):
                    pass

        return findings

    # ── Conflicting ArticulationControllers ────────────────────────────────

    def _check_conflicting_controllers(
        self, og_nodes: List[Dict]
    ) -> List[ValidationFinding]:
        findings = []

        # Find all ArticulationController nodes and their target joints
        controllers: List[Dict[str, Any]] = []
        for node in og_nodes:
            node_type = node.get("type", "")
            if "ArticulationController" not in node_type:
                continue

            inputs = node.get("inputs", {})
            robot_path = inputs.get("robotPath", "")
            joint_names = inputs.get("jointNames", [])
            graph_path = node.get("graph_path", node.get("path", ""))

            controllers.append({
                "path": node.get("path", ""),
                "graph_path": graph_path,
                "robot_path": robot_path,
                "joint_names": joint_names if isinstance(joint_names, list) else [],
            })

        # Group by robot_path + joint overlap
        if len(controllers) < 2:
            return findings

        # Check for controllers targeting the same robot
        by_robot: Dict[str, List[Dict]] = {}
        for ctrl in controllers:
            rp = ctrl["robot_path"]
            if rp:
                by_robot.setdefault(rp, []).append(ctrl)

        for robot_path, ctrls in by_robot.items():
            if len(ctrls) < 2:
                continue

            # Check for overlapping joint names
            all_joint_sets = [set(c["joint_names"]) for c in ctrls]
            for i in range(len(ctrls)):
                for j in range(i + 1, len(ctrls)):
                    overlap = all_joint_sets[i] & all_joint_sets[j]
                    if overlap or (not all_joint_sets[i] and not all_joint_sets[j]):
                        # Both target same robot and same (or all) joints
                        ctrl_a = ctrls[i]
                        ctrl_b = ctrls[j]
                        overlap_str = ", ".join(sorted(overlap)) if overlap else "all joints"

                        findings.append(ValidationFinding(
                            finding_id=uuid.uuid4().hex[:8],
                            rule_id="robot_motion.conflicting_controllers",
                            pack=self.pack,
                            severity="error",
                            prim_path=robot_path,
                            message=(
                                f"Two ArticulationControllers command the same "
                                f"joints on '{robot_path}' — they will fight."
                            ),
                            detail=(
                                f"Controllers at '{ctrl_a['path']}' (graph: "
                                f"{ctrl_a['graph_path']}) and '{ctrl_b['path']}' "
                                f"(graph: {ctrl_b['graph_path']}) both target "
                                f"robot '{robot_path}' with overlapping joints: "
                                f"[{overlap_str}]. One controller's velocity "
                                f"commands will override the other each tick, "
                                f"causing the robot to barely move or jitter. "
                                f"Disable one of the graphs."
                            ),
                            evidence={
                                "controller_a": ctrl_a["path"],
                                "controller_b": ctrl_b["path"],
                                "overlapping_joints": sorted(overlap) if overlap else "all",
                                "graph_a": ctrl_a["graph_path"],
                                "graph_b": ctrl_b["graph_path"],
                            },
                            auto_fixable=False,
                        ))

        return findings

    def auto_fixable(self) -> bool:
        return True

    def suggest_fix(self, finding: ValidationFinding):
        if finding.rule_id == "robot_motion.caster_stiffness":
            return finding.fix_suggestion
        if finding.rule_id == "robot_motion.floor_no_friction":
            return finding.fix_suggestion
        if finding.rule_id == "robot_motion.zero_gravity":
            return finding.fix_suggestion
        return None
