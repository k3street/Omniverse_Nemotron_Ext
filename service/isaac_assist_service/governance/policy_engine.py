"""Risk-assessment engine for patch actions.

Each ``PatchAction`` proposed by the planner is evaluated against a small
rule set: Python code that touches system paths or environment variables is
``"high"``; any Python modification is ``"medium"``; USD/settings changes
are ``"low"`` by default.  Low planner confidence upgrades risk toward
``"medium"``.
"""
import logging
import re
from typing import List, Dict, Any, Tuple

from service.isaac_assist_service.planner.models import PatchAction
from service.isaac_assist_service.governance.models import GovernanceConfig

logger = logging.getLogger(__name__)

class PolicyEngine:
    """Evaluates the risk level of operations proposed by the Planner."""

    def __init__(self, config: GovernanceConfig = None):
        """Initialise the engine with a :class:`GovernanceConfig` (defaults to a fresh instance)."""
        self.config = config or GovernanceConfig()

    def evaluate_action(self, action: PatchAction) -> Tuple[str, List[str]]:
        """Assess the risk level of a single patch action.

        Rules applied in priority order:
        - ``"high"`` if Python code touches ``os.environ`` / ``subprocess`` or
          writes to ``/tmp`` / ``/var``.
        - ``"medium"`` if the write surface is ``"python"`` (any code change) or
          modifies network settings.
        - ``"medium"`` upgraded from ``"low"`` when planner confidence < 0.5.
        - ``"low"`` for standard USD / settings writes.

        Args:
            action (PatchAction): The action to evaluate.

        Returns:
            tuple[str, list[str]]: ``(risk_level, reasons)`` where ``risk_level``
            is ``"low"`` | ``"medium"`` | ``"high"`` and ``reasons`` is a list of
            human-readable justification strings.
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
        """Evaluate a full list of actions and aggregate to the highest risk level.

        Args:
            actions (list[PatchAction]): All actions in a patch plan.

        Returns:
            dict: ``{overall_risk, requires_approval, action_evaluations}`` where
            ``action_evaluations`` is a list of per-action
            ``{action_id, risk_level, reasons}`` dicts.
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
