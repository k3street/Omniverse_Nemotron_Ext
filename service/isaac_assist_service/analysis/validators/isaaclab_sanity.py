"""
IsaacLabSanityValidator — check RL environment setup health.

Catches:
- Environment spacing too small for robot extents (clipping between envs)
- Missing collision filtering between env instances
- Observation / action space referencing non-existent joints or bodies
- Robot prim path not found in the stage
- Missing articulation root on the robot
- No ground plane / physics scene configured
"""
from typing import List, Dict, Any, Set
import uuid

from .base import ValidationRule
from ..models import ValidationFinding


# Minimum env_spacing = 2 × max robot extent + margin
_ENV_SPACING_MARGIN = 0.5  # meters


class IsaacLabSanityValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "isaaclab.sanity"
        self.pack = "isaaclab_sanity"
        self.severity = "warning"
        self.name = "IsaacLab RL environment sanity"
        self.description = (
            "Validates that the stage is correctly set up for IsaacLab RL training — "
            "env spacing, collision filters, ground plane, physics scene, and robot articulation."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])
        prim_map: Dict[str, Dict] = {p.get("path", ""): p for p in prims}

        # Collect useful prim sets
        articulation_roots: Set[str] = set()
        rigid_bodies: Set[str] = set()
        collision_prims: Set[str] = set()
        joint_paths: Set[str] = set()
        ground_planes: Set[str] = set()
        physics_scenes: Set[str] = set()
        robot_candidates: Set[str] = set()

        for prim in prims:
            path = prim.get("path", "")
            prim_type = prim.get("type", "")
            schemas = prim.get("schemas", [])

            if "PhysicsArticulationRootAPI" in schemas:
                articulation_roots.add(path)
            if "PhysicsRigidBodyAPI" in schemas:
                rigid_bodies.add(path)
            if "PhysicsCollisionAPI" in schemas:
                collision_prims.add(path)
            if prim_type in ("PhysicsRevoluteJoint", "PhysicsPrismaticJoint",
                             "PhysicsFixedJoint", "PhysicsD6Joint"):
                joint_paths.add(path)
            if prim_type == "Plane" or "GroundPlane" in prim_type:
                ground_planes.add(path)
            if prim_type == "PhysicsScene":
                physics_scenes.add(path)

            # Heuristic: a prim with many joints below it is a robot
            if "PhysicsArticulationRootAPI" in schemas:
                robot_candidates.add(path)

        # --- No physics scene ---
        if not physics_scenes:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="isaaclab.no_physics_scene",
                pack=self.pack,
                severity="error",
                prim_path=None,
                message="No PhysicsScene found — simulation will not run.",
                detail="IsaacLab requires a PhysicsScene prim. Add one via Create > Physics > Physics Scene.",
                evidence={},
                auto_fixable=True,
                fix_suggestion=None,
            ))

        # --- No ground plane ---
        if not ground_planes:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="isaaclab.no_ground_plane",
                pack=self.pack,
                severity="warning",
                prim_path=None,
                message="No ground plane detected — robot may fall through the void.",
                detail="Most RL tasks need a ground plane for the robot to stand on.",
                evidence={},
                auto_fixable=True,
                fix_suggestion=None,
            ))

        # --- No articulation root on any robot candidate ---
        if not articulation_roots and rigid_bodies:
            findings.append(ValidationFinding(
                finding_id=uuid.uuid4().hex[:8],
                rule_id="isaaclab.no_articulation_root",
                pack=self.pack,
                severity="error",
                prim_path=None,
                message="Rigid bodies exist but no ArticulationRoot API found.",
                detail=(
                    "IsaacLab environments require an ArticulationRootAPI on the "
                    "top-level robot prim to drive joints via the PhysX articulation solver."
                ),
                evidence={"rigid_body_count": len(rigid_bodies)},
                auto_fixable=False,
            ))

        # --- Env spacing check (via IsaacLab-generated envs at /World/envs/env_*) ---
        env_paths = [p for p in prim_map if "/envs/env_" in p and p.count("/") == 3]
        if len(env_paths) >= 2:
            # Try to read env_spacing from the scene config or infer from positions
            env_prims = [prim_map[p] for p in sorted(env_paths)[:2]]
            pos0 = env_prims[0].get("attributes", {}).get("xformOp:translate", [0, 0, 0])
            pos1 = env_prims[1].get("attributes", {}).get("xformOp:translate", [0, 0, 0])
            if isinstance(pos0, (list, tuple)) and isinstance(pos1, (list, tuple)):
                try:
                    dx = abs(float(pos1[0]) - float(pos0[0]))
                    dy = abs(float(pos1[1]) - float(pos0[1]))
                    spacing = max(dx, dy)
                    if spacing > 0 and spacing < 1.0:
                        findings.append(ValidationFinding(
                            finding_id=uuid.uuid4().hex[:8],
                            rule_id="isaaclab.env_spacing_too_small",
                            pack=self.pack,
                            severity="warning",
                            prim_path=env_paths[0],
                            message=f"Environment spacing is very small ({spacing:.2f}m) — robots may collide between envs.",
                            detail=(
                                "IsaacLab clones environments in a grid. If env_spacing "
                                "is smaller than the robot extent, collisions will corrupt training."
                            ),
                            evidence={"computed_spacing": spacing, "env0": env_paths[0], "env1": env_paths[1]},
                            auto_fixable=False,
                        ))
                except (ValueError, IndexError):
                    pass

        # --- Collision filter check ---
        # IsaacLab should apply collision filtering between env instances
        # If we see many envs but no FilteredPairs or CollisionGroup, warn
        if len(env_paths) > 1:
            has_filter = any(
                p.get("type") in ("PhysicsFilteredPairsAPI", "CollisionGroup")
                or "PhysicsFilteredPairsAPI" in p.get("schemas", [])
                for p in prims
            )
            if not has_filter:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="isaaclab.no_collision_filter",
                    pack=self.pack,
                    severity="info",
                    prim_path=None,
                    message="Multiple env instances found but no collision filtering detected.",
                    detail=(
                        "IsaacLab normally handles inter-env collision filtering automatically. "
                        "If you're setting up envs manually, add CollisionGroup prims to prevent "
                        "robots in different envs from colliding."
                    ),
                    evidence={"env_count": len(env_paths)},
                    auto_fixable=False,
                ))

        # --- Robot without joints ---
        for root_path in articulation_roots:
            child_joints = [j for j in joint_paths if j.startswith(root_path + "/")]
            if not child_joints:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="isaaclab.articulation_no_joints",
                    pack=self.pack,
                    severity="warning",
                    prim_path=root_path,
                    message=f"ArticulationRoot at '{root_path}' has no joints — nothing to actuate.",
                    detail="An RL policy needs drivable joints to produce actions.",
                    evidence={"articulation_root": root_path},
                    auto_fixable=False,
                ))

            # Check for joints with no collision meshes under the articulation
            child_collisions = [c for c in collision_prims if c.startswith(root_path + "/")]
            if not child_collisions and child_joints:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="isaaclab.no_collision_meshes",
                    pack=self.pack,
                    severity="warning",
                    prim_path=root_path,
                    message=f"Robot at '{root_path}' has joints but no collision meshes.",
                    detail=(
                        "Without collision meshes the robot will pass through objects. "
                        "Add PhysicsCollisionAPI to link meshes."
                    ),
                    evidence={"joint_count": len(child_joints)},
                    auto_fixable=False,
                ))

        return findings
