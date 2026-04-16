from typing import Dict, Any, List
from .models import StageAnalysisResult
import uuid
import time
from .validators.schema_consistency import SchemaConsistencyRule
from .validators.import_health import ImportHealthValidator
from .validators.material_physics import MaterialPhysicsMismatchValidator

class AnalysisOrchestrator:
    def __init__(self):
        # Register standard MVP packs
        self.rules = [
            SchemaConsistencyRule(),
            ImportHealthValidator(),
            MaterialPhysicsMismatchValidator(),
        ]

    def run_analysis(self, stage_data: Dict[str, Any]) -> StageAnalysisResult:
        start = time.time()
        
        all_findings = []
        for rule in self.rules:
            # Dynamically execute checks over the JSON chunk
            all_findings.extend(rule.check(stage_data))
            
        severity_counts = {"error": 0, "warning": 0, "info": 0}
        for f in all_findings:
            if f.severity in severity_counts:
                severity_counts[f.severity] += 1
                
        duration = time.time() - start
        
        # Build deterministic output
        return StageAnalysisResult(
            analysis_id=uuid.uuid4().hex,
            total_prims=len(stage_data.get("prims", [])),
            prim_type_counts={}, # Stubs
            sublayer_count=0,
            findings=all_findings,
            findings_by_severity=severity_counts,
            duration_seconds=duration,
            causal_graph=None
        )
