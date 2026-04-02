from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

class ProvenanceLink(BaseModel):
    source_type: str
    source_id: str
    source_name: str
    trust_tier: Optional[int] = None
    url: Optional[str] = None

class PatchAction(BaseModel):
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
    step_id: str
    description: str
    rule_ids: List[str]
    expected_outcome: str

class PlanValidationResult(BaseModel):
    plan_id: str
    validated_at: datetime
    findings_resolved: List[str]
    findings_unchanged: List[str]
    findings_new: List[str]
    is_regressive: bool
    recommendation: str

class PatchPlan(BaseModel):
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
    finding_ids: List[str]
    user_request: Optional[str] = None
    scope: str
    mode: str
