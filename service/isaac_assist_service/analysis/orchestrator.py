from typing import Dict, Any, List, Optional
from .models import StageAnalysisResult
import uuid
import time
import logging

from .validators import create_all_validators, get_registered_validators

logger = logging.getLogger(__name__)


class AnalysisOrchestrator:
    def __init__(self, enabled_packs: Optional[List[str]] = None):
        """
        Initialize with validators from the registry.
        If enabled_packs is None, all registered packs are loaded.
        """
        self.rules = create_all_validators(enabled_packs)
        packs = get_registered_validators()
        logger.info(
            f"[analysis] Loaded {len(self.rules)}/{len(packs)} validator packs"
        )

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
        prims = stage_data.get("prims", [])
        type_counts: Dict[str, int] = {}
        for p in prims:
            t = p.get("type", "Unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        return StageAnalysisResult(
            analysis_id=uuid.uuid4().hex,
            total_prims=len(prims),
            prim_type_counts=type_counts,
            sublayer_count=stage_data.get("sublayer_count", 0),
            findings=all_findings,
            findings_by_severity=severity_counts,
            duration_seconds=duration,
            causal_graph=None
        )
