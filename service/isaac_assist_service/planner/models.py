from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

class ProvenanceLink(BaseModel):
    """Traceable origin reference for a plan action or plan itself.

    Attributes:
        source_type (str): Category of origin, e.g. "finding", "swarm", "user".
        source_id (str): Unique identifier within the source system.
        source_name (str): Human-readable name of the originating rule or agent.
        trust_tier (int, optional): Confidence tier (lower = more trusted). Defaults to None.
        url (str, optional): Link to external documentation or finding detail. Defaults to None.
    """
    source_type: str
    source_id: str
    source_name: str
    trust_tier: Optional[int] = None
    url: Optional[str] = None

class PatchAction(BaseModel):
    """A single atomic change to be applied to the USD stage, Python code, or settings.

    Attributes:
        action_id (str): Short unique hex identifier for this action.
        order (int): Execution order within the parent plan (0-based).
        depends_on (List[str]): action_ids that must be applied before this one.
        write_surface (str): Target surface — "usd", "python", or "settings".
        target_path (str): USD prim path, file path, or settings key to modify.
        action_type (str): Operation kind — "set_property", "add_schema", or
            "swarm_python_script".
        property_name (str, optional): USD attribute name for set_property actions.
        old_value (Any, optional): Current value before the patch (for diffs / undo).
        new_value (Any, optional): Desired value or code block to write.
        file_diff (str, optional): Unified diff string for text-file patches.
        confidence (float): Planner confidence (0.0–1.0) that this action is correct.
        reasoning (str): Human-readable justification for the action.
        provenance (List[ProvenanceLink]): Origin chain for this action.
        approved (bool): True once the user has approved the action. Defaults to False.
        applied (bool): True once the Kit executor has applied the action. Defaults to False.
        apply_error (str, optional): Error message if application failed. Defaults to None.
    """
    action_id: str
    order: int
    depends_on: List[str] = []

    write_surface: str # "usd" | "python" | "settings"
    target_path: str

    action_type: str # "set_property" | "add_schema"
    property_name: Optional[str] = None
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    file_diff: Optional[str] = None

    confidence: float
    reasoning: str
    provenance: List[ProvenanceLink] = []

    approved: bool = False
    applied: bool = False
    apply_error: Optional[str] = None

class ValidationStep(BaseModel):
    """One manual or automated verification step associated with a plan.

    Attributes:
        step_id (str): Unique identifier for this step.
        description (str): What to check or do.
        rule_ids (List[str]): Validation rule identifiers that this step exercises.
        expected_outcome (str): What a passing result looks like.
    """
    step_id: str
    description: str
    rule_ids: List[str]
    expected_outcome: str

class PlanValidationResult(BaseModel):
    """Outcome of re-running the stage analyzer after a plan has been applied.

    Attributes:
        plan_id (str): The plan whose application is being validated.
        validated_at (datetime): UTC timestamp of the validation run.
        findings_resolved (List[str]): finding_ids that are no longer present.
        findings_unchanged (List[str]): finding_ids that still exist unchanged.
        findings_new (List[str]): finding_ids introduced by the applied patch.
        is_regressive (bool): True if any new findings were introduced.
        recommendation (str): Human-readable next-action suggestion.
    """
    plan_id: str
    validated_at: datetime
    findings_resolved: List[str]
    findings_unchanged: List[str]
    findings_new: List[str]
    is_regressive: bool
    recommendation: str

class PatchPlan(BaseModel):
    """Complete plan for fixing one or more stage validation findings.

    Attributes:
        plan_id (str): UUID hex string identifying this plan.
        created_at (datetime): UTC timestamp when the plan was generated.
        trigger (str): What initiated the plan — "finding", "swarm_agent", etc.
        finding_ids (List[str]): Validation finding IDs this plan addresses.
        user_request (str, optional): Free-text user instruction that guided generation.
        title (str): Short summary of the plan's intent.
        description (str): Longer explanation of what will be changed and why.
        actions (List[PatchAction]): Ordered list of atomic changes.
        validation_steps (List[ValidationStep]): Post-apply verification steps.
        overall_confidence (float): Aggregated confidence across all actions (0.0–1.0).
        compatibility_status (str): "validated", "proposed", or "failed".
        provenance (List[ProvenanceLink]): Origin chain for the plan as a whole.
        status (str): Lifecycle state — "draft", "proposed", "applied", "rejected".
        snapshot_id (str, optional): USD snapshot taken before application (for rollback).
        validation_result (PlanValidationResult, optional): Result of post-apply validation.
    """
    plan_id: str
    created_at: datetime

    trigger: str
    finding_ids: List[str]
    user_request: Optional[str] = None

    title: str
    description: str
    actions: List[PatchAction]
    validation_steps: List[ValidationStep] = []

    overall_confidence: float
    compatibility_status: str
    provenance: List[ProvenanceLink] = []

    status: str
    snapshot_id: Optional[str] = None
    validation_result: Optional[PlanValidationResult] = None

class PlanGenerationRequest(BaseModel):
    """Input parameters for the /planner/generate endpoint.

    Attributes:
        finding_ids (List[str]): Validation findings the plan should address.
        user_request (str, optional): Optional natural-language guidance for the planner.
        scope (str): Stage scope to consider — e.g. "full_stage" or prim path prefix.
        mode (str): Generation mode — "explain" (draft only) or "propose" (ready to apply).
    """
    finding_ids: List[str]
    user_request: Optional[str] = None
    scope: str
    mode: str
