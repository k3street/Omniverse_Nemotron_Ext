"""
MaterialPhysicsMismatchValidator — detect inconsistencies between
visual materials and physics schemas.

Catches:
- RigidBody without CollisionAPI (falls through everything)
- Mesh with material but no physics (floating visual-only objects)
- CollisionAPI with wrong approximation type for dynamic bodies
- Deformable body with collision (conflict)
"""
from typing import List, Dict, Any
import uuid

from .base import ValidationRule
from ..models import ValidationFinding, FixSuggestion, ProposedChange


class MaterialPhysicsMismatchValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "material_physics.mismatch"
        self.pack = "material_physics"
        self.severity = "warning"
        self.name = "Material / physics mismatch"
        self.description = (
            "Checks for inconsistencies between visual materials and "
            "physics schemas — rigid bodies without colliders, "
            "visual-only meshes in physics scenes, and collision "
            "approximation mismatches."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])

        # Track whether this scene has any physics at all
        has_physics_scene = any(
            "PhysicsScene" in p.get("type", "")
            for p in prims
        )

        for prim in prims:
            path = prim.get("path", "")
            prim_type = prim.get("type", "")
            schemas = prim.get("schemas", [])
            attrs = prim.get("attributes", {})
            has_material = prim.get("has_material", False) or bool(
                prim.get("material_path", "")
            )

            # --- RigidBody without CollisionAPI ---
            # Already covered by SchemaConsistencyRule but we add detail
            # about collision approximation
            if "PhysicsRigidBodyAPI" in schemas:
                collision_approx = attrs.get("physics:approximation", "")

                # Triangle mesh collision on dynamic rigids is invalid in PhysX
                if "PhysicsCollisionAPI" in schemas and collision_approx == "none":
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="material_physics.no_collision_approx",
                        pack=self.pack,
                        severity="warning",
                        prim_path=path,
                        message="Dynamic rigid body has no collision approximation.",
                        detail=(
                            f"Prim '{path}' has RigidBodyAPI + CollisionAPI "
                            f"but no collision approximation set. PhysX will "
                            f"use triangle mesh collision which is invalid "
                            f"for dynamic bodies — use 'convexHull' or "
                            f"'convexDecomposition'."
                        ),
                        evidence={
                            "schemas": schemas,
                            "approximation": collision_approx,
                        },
                        auto_fixable=True,
                    ))

            # --- Mesh with material but no physics in a physics scene ---
            if (has_physics_scene
                    and prim_type in ("Mesh", "BasisCurves", "Points")
                    and has_material
                    and "PhysicsRigidBodyAPI" not in schemas
                    and "PhysicsCollisionAPI" not in schemas
                    and "PhysxDeformableBodyAPI" not in schemas
                    and "PhysxDeformableSurfaceAPI" not in schemas):
                # Exclude ground planes and environment meshes (usually static)
                if not any(kw in path.lower() for kw in (
                    "ground", "floor", "sky", "dome", "backdrop",
                    "environment", "background",
                )):
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="material_physics.visual_only_mesh",
                        pack=self.pack,
                        severity="info",
                        prim_path=path,
                        message="Visual-only mesh in a physics scene.",
                        detail=(
                            f"Prim '{path}' has a material but no physics "
                            f"schemas. It will be visible but won't interact "
                            f"with physics — objects pass through it. Add "
                            f"CollisionAPI if it should be solid."
                        ),
                        evidence={
                            "prim_type": prim_type,
                            "has_material": True,
                            "schemas": schemas,
                        },
                        auto_fixable=True,
                    ))

            # --- Deformable body + CollisionAPI conflict ---
            is_deformable = (
                "PhysxDeformableBodyAPI" in schemas
                or "PhysxDeformableSurfaceAPI" in schemas
            )
            if is_deformable and "PhysicsCollisionAPI" in schemas:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="material_physics.deformable_collision_conflict",
                    pack=self.pack,
                    severity="error",
                    prim_path=path,
                    message="Deformable body has rigid CollisionAPI — conflict.",
                    detail=(
                        f"Prim '{path}' has both a deformable body schema "
                        f"and PhysicsCollisionAPI. Deformables use their own "
                        f"collision system; the rigid CollisionAPI should be "
                        f"removed to avoid solver conflicts."
                    ),
                    evidence={"schemas": schemas},
                    auto_fixable=True,
                ))

            # --- RigidBody + triangle mesh collision (dynamic body) ---
            if ("PhysicsRigidBodyAPI" in schemas
                    and "PhysicsCollisionAPI" in schemas
                    and collision_approx == "triangleMesh"):
                # PhysX rejects triangle mesh for dynamic rigid bodies
                kinematic = attrs.get("physics:kinematicEnabled", False)
                if not kinematic:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="material_physics.triangle_mesh_dynamic",
                        pack=self.pack,
                        severity="error",
                        prim_path=path,
                        message="Dynamic body uses triangle mesh collision.",
                        detail=(
                            f"Prim '{path}' is a dynamic rigid body using "
                            f"'triangleMesh' collision approximation. PhysX "
                            f"rejects this for non-kinematic bodies. Use "
                            f"'convexHull' or 'convexDecomposition' instead."
                        ),
                        evidence={
                            "approximation": collision_approx,
                            "kinematic": kinematic,
                        },
                        auto_fixable=True,
                    ))

        return findings

    def auto_fixable(self) -> bool:
        return True

    def suggest_fix(self, finding: ValidationFinding):
        if finding.rule_id == "material_physics.visual_only_mesh":
            return FixSuggestion(
                description=f"Add CollisionAPI to '{finding.prim_path}'",
                confidence=0.7,
                changes=[ProposedChange(
                    target_type="prim",
                    target_path=finding.prim_path,
                    action="apply_schema",
                    property_name="PhysicsCollisionAPI",
                )],
            )
        if finding.rule_id in (
            "material_physics.no_collision_approx",
            "material_physics.triangle_mesh_dynamic",
        ):
            return FixSuggestion(
                description=f"Set collision approximation to 'convexHull' on '{finding.prim_path}'",
                confidence=0.9,
                changes=[ProposedChange(
                    target_type="attribute",
                    target_path=finding.prim_path,
                    action="set",
                    property_name="physics:approximation",
                    old_value=finding.evidence.get("approximation"),
                    new_value="convexHull",
                )],
            )
        if finding.rule_id == "material_physics.deformable_collision_conflict":
            return FixSuggestion(
                description=f"Remove CollisionAPI from deformable '{finding.prim_path}'",
                confidence=0.85,
                changes=[ProposedChange(
                    target_type="prim",
                    target_path=finding.prim_path,
                    action="remove_schema",
                    property_name="PhysicsCollisionAPI",
                )],
            )
        return None
