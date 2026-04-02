from typing import List, Dict, Any
from .base import ValidationRule
from ..models import ValidationFinding
import uuid

class SchemaConsistencyRule(ValidationRule):
    def __init__(self):
        super().__init__()
        self.rule_id = "schema.missing_collision"
        self.pack = "schema_consistency"
        self.severity = "warning"
        self.name = "Physics prim without collision"
        self.description = "Checks if a RigidBody lacks Collision geometries."

    def check(self, stage_data: Dict[str, Any]) -> List[ValidationFinding]:
        findings = []
        
        prims = stage_data.get("prims", [])
        for prim in prims:
            schemas = prim.get("schemas", [])
            # If it's a rigid body without a collision API
            if "PhysicsRigidBodyAPI" in schemas and "PhysicsCollisionAPI" not in schemas:
                findings.append(ValidationFinding(
                    finding_id=uuid.uuid4().hex[:8],
                    rule_id=self.rule_id,
                    pack=self.pack,
                    severity=self.severity,
                    prim_path=prim.get("path"),
                    message="Rigid body is missing collision API.",
                    detail=f"Prim '{prim.get('path')}' has RigidBody applied but will fall through the floor because it has no Collision schema.",
                    evidence={"schemas_applied": schemas},
                    auto_fixable=True
                ))
                
        return findings
