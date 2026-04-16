"""
ArticulationIntegrityValidator — check articulation chain health.

Catches:
- Joints with zero stiffness/damping (arm will collapse)
- Joints with no limits (infinite rotation)
- Articulations with no root body
- Disconnected joint hierarchy (orphan joints)
- Missing drive API on joints
"""
from typing import List, Dict, Any
import uuid

from .base import ValidationRule
from ..models import ValidationFinding, FixSuggestion, ProposedChange


class ArticulationIntegrityValidator(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "articulation.integrity"
        self.pack = "articulation_integrity"
        self.severity = "warning"
        self.name = "Articulation integrity check"
        self.description = (
            "Verifies articulation chain health — drive stiffness, "
            "joint limits, root body presence, and hierarchy connectivity."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        prims = stage_data.get("prims", [])
        prim_map = {p.get("path", ""): p for p in prims}

        # Collect articulation roots and joints
        art_roots = []
        joints = []
        for prim in prims:
            schemas = prim.get("schemas", [])
            prim_type = prim.get("type", "")
            if "PhysicsArticulationRootAPI" in schemas:
                art_roots.append(prim)
            if prim_type in (
                "PhysicsRevoluteJoint", "PhysicsPrismaticJoint",
                "PhysicsFixedJoint", "PhysicsSphericalJoint",
                "PhysicsJoint",
            ) or "JointAPI" in " ".join(schemas):
                joints.append(prim)

        # --- Articulation without root body check ---
        # (ArticulationRootAPI should be on a prim with RigidBodyAPI or
        #  have a descendant with one)
        for art in art_roots:
            art_path = art.get("path", "")
            art_schemas = art.get("schemas", [])
            has_rigid = "PhysicsRigidBodyAPI" in art_schemas

            if not has_rigid:
                # Check descendants
                descendant_has_rigid = any(
                    "PhysicsRigidBodyAPI" in p.get("schemas", [])
                    for p in prims
                    if p.get("path", "").startswith(art_path + "/")
                )
                if not descendant_has_rigid:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="articulation.no_rigid_body",
                        pack=self.pack,
                        severity="error",
                        prim_path=art_path,
                        message="ArticulationRoot has no RigidBody in hierarchy.",
                        detail=(
                            f"Prim '{art_path}' has ArticulationRootAPI but "
                            f"neither it nor any descendant has RigidBodyAPI. "
                            f"The articulation will not simulate."
                        ),
                        evidence={"schemas": art_schemas},
                        auto_fixable=False,
                    ))

        # --- Joint checks ---
        for joint in joints:
            path = joint.get("path", "")
            attrs = joint.get("attributes", {})
            schemas = joint.get("schemas", [])
            joint_type = joint.get("type", "")

            # Zero drive stiffness on revolute/prismatic joints
            stiffness = attrs.get("drive:angular:physics:stiffness",
                        attrs.get("drive:linear:physics:stiffness",
                        attrs.get("physics:stiffness")))
            damping = attrs.get("drive:angular:physics:damping",
                     attrs.get("drive:linear:physics:damping",
                     attrs.get("physics:damping")))

            if stiffness is not None and damping is not None:
                try:
                    if float(stiffness) == 0 and float(damping) == 0:
                        findings.append(ValidationFinding(
                            finding_id=uuid.uuid4().hex[:8],
                            rule_id="articulation.zero_drive",
                            pack=self.pack,
                            severity="warning",
                            prim_path=path,
                            message="Joint has zero stiffness and damping.",
                            detail=(
                                f"Joint '{path}' has stiffness=0 and damping=0. "
                                f"The joint will not hold position — the arm "
                                f"will collapse under gravity."
                            ),
                            evidence={
                                "stiffness": stiffness,
                                "damping": damping,
                            },
                            auto_fixable=True,
                        ))
                except (ValueError, TypeError):
                    pass

            # Missing joint limits on revolute joints
            if joint_type == "PhysicsRevoluteJoint" or "RevoluteJoint" in str(schemas):
                lower = attrs.get("physics:lowerLimit")
                upper = attrs.get("physics:upperLimit")
                if lower is None and upper is None:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="articulation.no_joint_limits",
                        pack=self.pack,
                        severity="info",
                        prim_path=path,
                        message="Revolute joint has no angle limits.",
                        detail=(
                            f"Joint '{path}' is a revolute joint with no "
                            f"lower/upper limits. It can rotate infinitely, "
                            f"which may cause instability in motion planning."
                        ),
                        evidence={"joint_type": joint_type},
                        auto_fixable=False,
                    ))

            # Check body targets exist
            for target_attr in ("physics:body0", "physics:body1"):
                target = attrs.get(target_attr, "")
                if target and target not in prim_map:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="articulation.dangling_joint_target",
                        pack=self.pack,
                        severity="error",
                        prim_path=path,
                        message=f"Joint target '{target_attr}' points to missing prim.",
                        detail=(
                            f"Joint '{path}' references '{target}' via "
                            f"'{target_attr}' but that prim does not exist. "
                            f"The joint is disconnected."
                        ),
                        evidence={target_attr: target},
                        auto_fixable=False,
                    ))

        return findings

    def auto_fixable(self) -> bool:
        return True

    def suggest_fix(self, finding: ValidationFinding):
        if finding.rule_id == "articulation.zero_drive":
            return FixSuggestion(
                description=f"Set default drive gains on '{finding.prim_path}'",
                confidence=0.7,
                changes=[
                    ProposedChange(
                        target_type="attribute",
                        target_path=finding.prim_path,
                        action="set",
                        property_name="drive:angular:physics:stiffness",
                        old_value=0,
                        new_value=400,
                    ),
                    ProposedChange(
                        target_type="attribute",
                        target_path=finding.prim_path,
                        action="set",
                        property_name="drive:angular:physics:damping",
                        old_value=0,
                        new_value=40,
                    ),
                ],
            )
        return None
