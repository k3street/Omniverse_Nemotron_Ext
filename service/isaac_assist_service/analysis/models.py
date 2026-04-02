from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class AnalysisContext(BaseModel):
    fingerprint: Dict[str, Any]
    stage_path: str
    stage_up_axis: str
    stage_meters_per_unit: float
    selected_prims: List[str]
    analysis_scope: str # "selection" | "dirty_layers" | "full"
    
class ProposedChange(BaseModel):
    target_type: str
    target_path: str
    action: str
    property_name: Optional[str]
    old_value: Optional[Any]
    new_value: Optional[Any]

class FixSuggestion(BaseModel):
    description: str
    confidence: float
    changes: List[ProposedChange]

class ValidationFinding(BaseModel):
    finding_id: str
    rule_id: str
    pack: str
    severity: str # "error" | "warning" | "info"
    prim_path: Optional[str]
    message: str
    detail: str
    evidence: Dict[str, Any]
    auto_fixable: bool
    fix_suggestion: Optional[FixSuggestion] = None
    related_docs: List[str] = []

class CausalNode(BaseModel):
    node_id: str
    finding_id: str
    node_type: str
    label: str
    severity: str

class CausalEdge(BaseModel):
    source_id: str
    target_id: str
    relationship: str

class CausalGraph(BaseModel):
    nodes: List[CausalNode] = []
    edges: List[CausalEdge] = []

class StageAnalysisResult(BaseModel):
    analysis_id: str
    # Stage summary stats
    total_prims: int
    prim_type_counts: Dict[str, int]
    sublayer_count: int
    
    # Findings
    findings: List[ValidationFinding]
    findings_by_severity: Dict[str, int]
    
    # Graph
    causal_graph: Optional[CausalGraph] = None
    duration_seconds: float
