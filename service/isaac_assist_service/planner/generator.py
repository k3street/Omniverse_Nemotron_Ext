import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any
from .models import PatchPlan, PatchAction, ProvenanceLink, PlanGenerationRequest
from ..analysis.models import ValidationFinding

class PlanGenerator:
    """
    MVP Deterministic Plan Generator.
    In Phase 4, this class will subclass an LLM Generator that feeds validation errors
    into standard LLM Context prompts and parses JSON blocks. 
    For Phase 3 mapping, we statically translate known UI rules to Executor rules.
    """
    def __init__(self):
        """Initialise the deterministic plan generator (no external dependencies)."""
        pass

    def generate_plan(self, req: PlanGenerationRequest, mock_findings: List[Dict[str, Any]]) -> PatchPlan:
        """Translate a list of validation findings into a structured PatchPlan.

        Maps each known rule_id to a deterministic PatchAction. Unknown rule_ids
        are silently skipped. In Phase 4 this method will be replaced by an
        LLM-backed implementation.

        Args:
            req (PlanGenerationRequest): Generation parameters including scope and mode.
            mock_findings (List[Dict[str, Any]]): Validation findings, each containing
                at minimum ``finding_id``, ``rule_id``, and ``prim_path`` keys.

        Returns:
            PatchPlan: Draft or proposed plan whose ``status`` is "draft" when
            ``req.mode == "explain"``, otherwise "proposed".
        """
        actions = []
        
        # 1. Map known Findings to fix actions
        for i, finding_data in enumerate(mock_findings):
            find_id = finding_data.get("finding_id", f"mock_{i}")
            rule_id = finding_data.get("rule_id", "unknown")
            path = finding_data.get("prim_path", "/World")
            
            if rule_id == "schema.missing_collision":
                actions.append(PatchAction(
                    action_id=uuid.uuid4().hex[:8],
                    order=i,
                    write_surface="usd",
                    target_path=path,
                    action_type="add_schema",
                    new_value="PhysicsCollisionAPI",
                    confidence=1.0, # Deterministically safe
                    reasoning="The RigidBody requires a collision shape to prevent falling through the floor.",
                    provenance=[ProvenanceLink(source_type="finding", source_id=find_id, source_name="SchemaConsistencyRule")]
                ))
        
        # 2. Build Plan
        return PatchPlan(
            plan_id=uuid.uuid4().hex,
            created_at=datetime.now(timezone.utc),
            trigger="finding",
            finding_ids=[f.get("finding_id", "unknown") for f in mock_findings],
            title="Fix Physics Collision Integrity",
            description="Adds missing Collision hulls to rigid bodies.",
            actions=actions,
            overall_confidence=1.0,
            compatibility_status="validated",
            status="draft" if req.mode == "explain" else "proposed"
        )
