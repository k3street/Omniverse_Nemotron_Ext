import sys
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
from pathlib import Path

from .agents.pm import ProjectManagerAgent
from .agents.coder import CoderAgent
from .agents.qa import QAAgent
from .agents.critic import CriticAgent

from .models import PatchPlan, PatchAction, ProvenanceLink, PlanGenerationRequest
from ..config import config

logger = logging.getLogger(__name__)

class SwarmPlanGenerator:
    """
    Submits a PlanGenerationRequest to the MCP Coder->QA->Critic->PM swarm.
    Returns parsed Extension actions.
    """
    async def generate_plan_async(self, req: PlanGenerationRequest, mock_findings: List[Dict[str, Any]]) -> PatchPlan:
        """Run the Coder→QA→Critic→PM swarm and return a PatchPlan.

        Builds a natural-language task prompt from ``req`` and ``mock_findings``,
        submits it to the multi-agent loop via ``ProjectManagerAgent.run``, then
        wraps the loop result in a ``PatchPlan`` with a single "swarm_python_script"
        action when the agents reach consensus.

        Args:
            req (PlanGenerationRequest): Generation parameters; ``user_request`` is
                used as the base prompt when present.
            mock_findings (List[Dict[str, Any]]): Validation findings appended to the
                prompt. Each entry should contain ``prim_path`` and ``rule_id``.

        Returns:
            PatchPlan: Plan with ``status="proposed"``, ``compatibility_status="validated"``
            and ``overall_confidence=0.9`` on swarm pass; confidence drops to 0.2 and
            status remains "proposed" with ``compatibility_status="failed"`` on swarm fail.
        """
        prompt = req.user_request or "Fix the stage according to best OpenUSD/Isaac Sim practices."
        if mock_findings:
            prompt += "\nSpecific validation issues found:\n"
            for f in mock_findings:
                prompt += f"- {f.get('prim_path')}: {f.get('rule_id')}\n"

        logger.info(f"Submitting to Swarm agent loop: {prompt[:50]}...")
        
        # Build the Task Payload standard
        task = {
            "id": "omniverse_extension_patch",
            "category": "isaac_sim",
            "difficulty": "medium",
            "prompt": prompt,
            "required_keywords": [],
            "required_patterns": [],
            "reference_apis": []
        }

        # Initialize core agents leveraging the local Config framework
        coder  = CoderAgent(model_tag=config.local_model_name)
        qa     = QAAgent(sim_mode="auto")
        critic = CriticAgent()
        pm     = ProjectManagerAgent(
            coder=coder,
            qa=qa,
            critic=critic,
            max_iterations=3,
        )

        import asyncio
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, pm.run, task)
        
        # Parse PM LoopResult
        status = result.status
        final_code = "\n\n".join(result.final_code) if result.final_code else ""
        
        actions = []
        if status == "pass" and final_code:
            actions.append(PatchAction(
                action_id=uuid.uuid4().hex[:8],
                order=0,
                write_surface="python",
                target_path="/tmp/swarm_generated_patch.py",
                action_type="swarm_python_script",
                new_value=final_code,
                confidence=0.9 if result.final_scores.get("pass_rate", 0) > 0.8 else 0.5,
                reasoning="Agent swarm fully validated the Python patch.",
                provenance=[ProvenanceLink(source_type="swarm", source_id="generate", source_name="PM_Agent")]
            ))
            
        # Return formatted Plan
        return PatchPlan(
            plan_id=uuid.uuid4().hex,
            created_at=datetime.now(timezone.utc),
            trigger="swarm_agent",
            finding_ids=req.finding_ids,
            user_request=req.user_request,
            title="Swarm Multi-Agent Patch Execution",
            description=f"Generated via natively integrated Swarm. Status: {status}. Total Time: {round(result.total_time_s, 1)}s",
            actions=actions,
            overall_confidence=0.9 if status == "pass" else 0.2,
            compatibility_status="validated" if status == "pass" else "failed",
            status="proposed"
        )
