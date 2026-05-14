"""Pydantic models for the Stage Analyzer subsystem."""
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime


class AnalysisContext(BaseModel):
    """Context block the UI extension sends with each analysis request.

    Attributes:
        fingerprint: Machine fingerprint dict from the fingerprint collector.
        stage_path: USD file path of the currently open stage.
        stage_up_axis: ``"Y"`` or ``"Z"`` — the stage's declared up-axis.
        stage_meters_per_unit: Unit scale (e.g. 0.01 for centimeters).
        selected_prims: Paths of prims currently selected in the viewport.
        analysis_scope: ``"selection"`` | ``"dirty_layers"`` | ``"full"``.
    """
    fingerprint: Dict[str, Any]
    stage_path: str
    stage_up_axis: str
    stage_meters_per_unit: float
    selected_prims: List[str]
    analysis_scope: str # "selection" | "dirty_layers" | "full"
    
class ProposedChange(BaseModel):
    """A single atomic change proposed as part of an auto-fix suggestion.

    Attributes:
        target_type: ``"attribute"`` | ``"schema"`` | ``"prim"`` etc.
        target_path: USD prim path the change applies to.
        action: ``"set"`` | ``"add"`` | ``"remove"`` | ``"delete"``.
        property_name: USD attribute or schema name, when applicable.
        old_value: Current value, for context / rollback.
        new_value: Desired value after the fix.
    """
    target_type: str
    target_path: str
    action: str
    property_name: Optional[str]
    old_value: Optional[Any]
    new_value: Optional[Any]

class FixSuggestion(BaseModel):
    """Structured auto-fix recommendation produced by a validator.

    Attributes:
        description: Human-readable description of what the fix does.
        confidence: Planner confidence in the fix being correct (0–1).
        changes: Ordered list of atomic changes to apply.
    """
    description: str
    confidence: float
    changes: List[ProposedChange]

class ValidationFinding(BaseModel):
    """A single issue found by a validation rule.

    Attributes:
        finding_id: Short random hex ID for deduplication.
        rule_id: Dotted rule identifier, e.g. ``"articulation.zero_drive"``.
        pack: Validator pack name, e.g. ``"articulation_integrity"``.
        severity: ``"error"`` | ``"warning"`` | ``"info"``.
        prim_path: USD prim path the finding references, if any.
        message: One-line human-readable summary.
        detail: Extended explanation including fix guidance.
        evidence: Attribute values or other data that triggered the finding.
        auto_fixable: True if ``suggest_fix`` can produce a valid change set.
        fix_suggestion: Structured fix, or None if not auto-fixable.
        related_docs: URLs to relevant NVIDIA docs.
    """
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
    """A node in the causal error graph.

    Attributes:
        node_id: Unique node identifier within the graph.
        finding_id: Associated ``ValidationFinding.finding_id``.
        node_type: ``"root_cause"`` | ``"symptom"`` | ``"effect"``.
        label: Short display label.
        severity: Matches the linked finding's severity.
    """
    node_id: str
    finding_id: str
    node_type: str
    label: str
    severity: str

class CausalEdge(BaseModel):
    """A directed edge in the causal error graph.

    Attributes:
        source_id: ID of the causing node.
        target_id: ID of the caused node.
        relationship: Description of the causal link, e.g. ``"causes"``.
    """
    source_id: str
    target_id: str
    relationship: str

class CausalGraph(BaseModel):
    nodes: List[CausalNode] = []
    edges: List[CausalEdge] = []

class StageAnalysisResult(BaseModel):
    """Aggregate output of a full stage analysis run.

    Attributes:
        analysis_id: UUID hex identifying this run.
        total_prims: Total number of prims in the analysed scope.
        prim_type_counts: ``{type_name: count}`` histogram.
        sublayer_count: Number of sublayers in the stage.
        findings: All ``ValidationFinding`` objects produced.
        findings_by_severity: ``{severity: count}`` summary dict.
        causal_graph: Optional dependency graph between findings.
        duration_seconds: Wall-clock time the analysis took.
    """
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
