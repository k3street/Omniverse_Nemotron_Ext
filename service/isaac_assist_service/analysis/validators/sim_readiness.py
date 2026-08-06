"""Sim-readiness validator — catches rigid-body layouts PhysX rejects.

Checks (over serialized stage data, so schema/path level only):
- Nested rigid bodies: a prim with ``PhysicsRigidBodyAPI`` whose ancestor
  also has it. PhysX treats this as one body and silently ignores the
  inner one, or errors on articulations.
- Rigid body with no ``PhysicsCollisionAPI`` anywhere in its subtree —
  the body free-falls through everything.
- Mesh prims under a rigid body that lack ``PhysicsCollisionAPI``
  (partial collision coverage: fingers/tools pass through the uncovered
  geometry).
"""
from typing import List, Dict, Any
import uuid

from .base import ValidationRule
from ..models import ValidationFinding


class SimReadinessRule(ValidationRule):
    """Validator that checks assets are structured for simulation."""

    def __init__(self):
        """Initialise sim-readiness rule metadata."""
        super().__init__()
        self.rule_id = "sim_ready.structure"
        self.pack = "sim_readiness"
        self.severity = "warning"
        self.name = "Sim-readiness structure check"
        self.description = (
            "Detects nested rigid bodies, rigid bodies without collision "
            "geometry, and meshes with partial collision coverage."
        )

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        """Check rigid-body structure and return any findings.

        Args:
            stage_data (dict): Serialized stage data from the UI extension.

        Returns:
            List[ValidationFinding]: One finding per structural problem.
        """
        findings = []
        prims = stage_data.get("prims", [])

        rigid_paths = [
            p.get("path", "")
            for p in prims
            if "PhysicsRigidBodyAPI" in p.get("schemas", [])
        ]
        rigid_set = set(rigid_paths)

        def _subtree(root: str):
            prefix = root.rstrip("/") + "/"
            return [
                p for p in prims
                if p.get("path", "") == root or p.get("path", "").startswith(prefix)
            ]

        # --- Nested rigid bodies ---
        for path in rigid_paths:
            ancestor = path.rsplit("/", 1)[0]
            while ancestor and ancestor != "/":
                if ancestor in rigid_set:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="sim_ready.nested_rigid_body",
                        pack=self.pack,
                        severity="error",
                        prim_path=path,
                        message="Nested rigid body.",
                        detail=(
                            f"Prim '{path}' has RigidBodyAPI but so does its "
                            f"ancestor '{ancestor}'. PhysX does not support "
                            "nested rigid bodies — remove one (keep it on the "
                            "asset root only, e.g. via make_sim_ready)."
                        ),
                        evidence={"ancestor_rigid_body": ancestor},
                        auto_fixable=False,
                    ))
                    break
                ancestor = ancestor.rsplit("/", 1)[0]

        # --- Rigid body subtree collision coverage ---
        for path in rigid_paths:
            subtree = _subtree(path)
            has_any_collision = any(
                "PhysicsCollisionAPI" in p.get("schemas", []) for p in subtree
            )
            if not has_any_collision:
                # schema_consistency already flags RB-without-collision on the
                # same prim; this variant covers the whole subtree, so only
                # report when the sibling rule stayed silent (subtree > 1).
                if len(subtree) > 1:
                    findings.append(ValidationFinding(
                        finding_id=uuid.uuid4().hex[:8],
                        rule_id="sim_ready.no_collision_in_subtree",
                        pack=self.pack,
                        severity="error",
                        prim_path=path,
                        message="Rigid body subtree has no collision geometry.",
                        detail=(
                            f"Prim '{path}' is a rigid body but no prim in its "
                            "subtree has CollisionAPI — it will fall through "
                            "the floor. Run make_sim_ready on it."
                        ),
                        evidence={"subtree_prims": len(subtree)},
                        auto_fixable=False,
                    ))
                continue

            uncovered = [
                p.get("path", "")
                for p in subtree
                if p.get("type", "") == "Mesh"
                and "PhysicsCollisionAPI" not in p.get("schemas", [])
            ]
            if uncovered:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id="sim_ready.partial_collision",
                    pack=self.pack,
                    severity="warning",
                    prim_path=path,
                    message="Rigid body has meshes without collision.",
                    detail=(
                        f"Rigid body '{path}' has {len(uncovered)} mesh prim(s) "
                        "without CollisionAPI — other objects will pass through "
                        "those parts. Run make_sim_ready to cover the full "
                        "subtree (or add the meshes to skip_name_patterns "
                        "deliberately)."
                    ),
                    evidence={"meshes_without_collision": uncovered[:20]},
                    auto_fixable=False,
                ))

        return findings
