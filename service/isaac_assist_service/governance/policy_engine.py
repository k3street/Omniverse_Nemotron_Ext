import logging
import re
from typing import List, Dict, Any, Tuple

from ..planner.models import PatchAction
from .models import GovernanceConfig

logger = logging.getLogger(__name__)

class PolicyEngine:
    """Evaluates the risk level of operations proposed by the Planner."""

    def __init__(self, config: GovernanceConfig = None):
        self.config = config or GovernanceConfig()

    def evaluate_action(self, action: PatchAction) -> Tuple[str, List[str]]:
        """
        Evaluates a single patch action and returns its risk level along with reasons.
        Returns: (risk_level, list_of_reasons)
        """
        risk_level = "low"
        reasons = []

        # 1. High Risk Rules
        if action.write_surface == "python":
            if "os.environ" in (action.new_value or "") or "subprocess" in (action.new_value or ""):
                risk_level = "high"
                reasons.append("Modifies environment variables or invokes subprocesses.")
            elif action.target_path.startswith("/tmp") or action.target_path.startswith("/var"):
                risk_level = "high"
                reasons.append("Touches sensitive system directories.")
        
        # 2. Medium Risk Rules
        if risk_level != "high":
            if action.write_surface == "python":
                risk_level = "medium"
                reasons.append("Modifies execution code.")
            elif action.write_surface == "settings" and "network" in action.target_path.lower():
                risk_level = "medium"
                reasons.append("Modifies network settings.")
            
        # 3. Confidence Rules
        if action.confidence < 0.5:
            risk_level = max(risk_level, "medium", key=lambda x: ["low", "medium", "high"].index(x))
            reasons.append(f"Low confidence generation ({action.confidence}).")

        # 4. Low Risk (Default)
        if not reasons:
            reasons.append("Standard USD/Settings modification.")

        return risk_level, reasons

    def evaluate_plan(self, actions: List[PatchAction]) -> Dict[str, Any]:
        """
        Evaluates a full list of actions. Returns the maximum risk level.
        """
        highest_risk = "low"
        risk_order = {"low": 1, "medium": 2, "high": 3}
        
        results = []
        for action in actions:
            risk, reasons = self.evaluate_action(action)
            highest_risk = max(highest_risk, risk, key=lambda x: risk_order[x])
            results.append({
                "action_id": action.action_id,
                "risk_level": risk,
                "reasons": reasons
            })

        # Autonomy determination
        requires_approval = highest_risk in ["medium", "high"]
        if self.config.operational_mode == "interactive":
            requires_approval = True

        return {
            "overall_risk": highest_risk,
            "requires_approval": requires_approval,
            "action_evaluations": results
        }
