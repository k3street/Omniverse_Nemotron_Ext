"""
client.py
----------
Async HTTP clients for the two external manipulation services:

  ContinuityManagerClient  — talks to the Continuity Manager (default port 7100)
  PolicyBankClient         — talks to the RL Policy Bank       (default port 7101)

Both services follow the Pi0.5 / Policy Bank API defined in
docs/03_pi05_planner.md and docs/04_rl_policy_bank.md.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp

from ..config import config

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10.0)
_PLAN_TIMEOUT    = aiohttp.ClientTimeout(total=30.0)   # LLM-backed endpoint can be slow


class _BaseClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def _get(self, path: str, timeout=_DEFAULT_TIMEOUT) -> Optional[Dict]:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            try:
                async with s.get(f"{self.base_url}{path}") as r:
                    if r.status != 200:
                        logger.error("%s GET %s → %d", self.base_url, path, r.status)
                        return None
                    return await r.json()
            except aiohttp.ClientError as e:
                logger.error("%s GET %s failed: %s", self.base_url, path, e)
                return None

    async def _post(self, path: str, body: Dict, timeout=_DEFAULT_TIMEOUT) -> Optional[Dict]:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            try:
                async with s.post(f"{self.base_url}{path}", json=body) as r:
                    if r.status not in (200, 201):
                        err = await r.text()
                        logger.error("%s POST %s → %d: %s", self.base_url, path, r.status, err[:300])
                        return None
                    return await r.json()
            except aiohttp.ClientError as e:
                logger.error("%s POST %s failed: %s", self.base_url, path, e)
                return None

    async def is_healthy(self) -> bool:
        result = await self._get("/healthz")
        return result is not None


# ---------------------------------------------------------------------------
# Continuity Manager client (port 7100 by default, Pi0.5-compatible contract)
# ---------------------------------------------------------------------------

class ContinuityManagerClient(_BaseClient):
    """
    Talks to the Continuity Manager service.

    The CM exposes:
      GET  /healthz
      POST /tasks          — submit a TaskSpec for execution
      GET  /tasks/{id}     — poll state machine status
      POST /tasks/{id}/abort  — transition to SAFE_HOLD
      GET  /tasks/{id}/telemetry  — phase outcome JSONL

    When Isaac Assist is acting as the Pi0.5 planner, the CM is the
    downstream consumer of generated Task Specs.
    """

    def __init__(self, base_url: str = "") -> None:
        super().__init__(base_url or config.continuity_manager_url)

    async def submit_task(self, task_spec_dict: Dict) -> Optional[Dict]:
        """POST a TaskSpec dict. Returns {task_id, state} on success."""
        return await self._post("/tasks", task_spec_dict, timeout=_PLAN_TIMEOUT)

    async def get_status(self, task_id: str) -> Optional[Dict]:
        """GET current state machine state for a task."""
        return await self._get(f"/tasks/{task_id}")

    async def abort_task(self, task_id: str) -> Optional[Dict]:
        """POST abort → CM transitions to SAFE_HOLD."""
        return await self._post(f"/tasks/{task_id}/abort", {})

    async def get_telemetry(self, task_id: str) -> List[Dict]:
        """GET phase outcome records as a list of dicts."""
        result = await self._get(f"/tasks/{task_id}/telemetry")
        if result is None:
            return []
        if isinstance(result, list):
            return result
        return result.get("records", [])


# ---------------------------------------------------------------------------
# Policy Bank client (port 7101 by default)
# ---------------------------------------------------------------------------

class PolicyBankClient(_BaseClient):
    """
    Talks to the RL Policy Bank service.

    Endpoints used by Isaac Assist:
      GET  /healthz
      GET  /policies              — list loaded (skill, embodiment, version)
      POST /reset                 — clear per-skill recurrent state
    """

    def __init__(self, base_url: str = "") -> None:
        super().__init__(base_url or config.policy_bank_url)

    async def list_policies(self) -> List[Dict]:
        result = await self._get("/policies")
        if result is None:
            return []
        if isinstance(result, list):
            return result
        return result.get("policies", [])

    async def reset_skill(self, skill_name: str, embodiment_id: str) -> bool:
        result = await self._post(
            "/reset",
            {"skill_name": skill_name, "embodiment_id": embodiment_id},
        )
        return result is not None

    async def act(
        self,
        skill_name: str,
        embodiment_id: str,
        observation: Dict[str, Any],
        phase_context: Dict[str, Any],
    ) -> Optional[Dict]:
        """POST to /act — hot path, 30–100 Hz. Returns {action, value_estimate, info}."""
        return await self._post(
            "/act",
            {
                "skill_name": skill_name,
                "embodiment_id": embodiment_id,
                "observation": observation,
                "phase_context": phase_context,
            },
            timeout=aiohttp.ClientTimeout(total=1.0),   # must be fast
        )
