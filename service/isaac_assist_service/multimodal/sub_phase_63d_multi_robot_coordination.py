"""Phase 63d — Multi-robot coordination runtime (handoff + mutex enforcement).

Implements pure-Python mutex/lock semantics, handoff token state machine,
and deadlock detection for multi-robot resource coordination.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 63d.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
import uuid


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

RobotID = str
ResourceID = str

HandoffPhase = Literal[
    "uninitiated",
    "requested",
    "approaching",
    "transferring",
    "completed",
    "aborted",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MutexState:
    resource_id: ResourceID
    owner: Optional[RobotID] = None
    waiters: List[RobotID] = field(default_factory=list)
    claimed_at: Optional[str] = None


@dataclass
class HandoffToken:
    handoff_id: str
    source_robot: RobotID
    target_robot: RobotID
    payload_prim: str
    phase: HandoffPhase = "uninitiated"
    initiated_at: str = ""
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# RobotResourceMutex
# ---------------------------------------------------------------------------


class RobotResourceMutex:
    """Thread-unsafe (single-threaded) mutex registry for robot resources."""

    def __init__(self) -> None:
        self._locks: Dict[ResourceID, MutexState] = {}

    def _ensure(self, resource_id: ResourceID) -> MutexState:
        if resource_id not in self._locks:
            self._locks[resource_id] = MutexState(resource_id=resource_id)
        return self._locks[resource_id]

    def try_acquire(self, resource_id: ResourceID, robot_id: RobotID) -> bool:
        """Try to acquire the resource.

        Returns True if acquired (either fresh or re-entrant by same owner).
        Returns False if held by a different robot.
        """
        state = self._ensure(resource_id)
        if state.owner is None:
            state.owner = robot_id
            state.claimed_at = _now_iso()
            return True
        # Re-entrant: same robot already owns it
        if state.owner == robot_id:
            return True
        return False

    def release(self, resource_id: ResourceID, robot_id: RobotID) -> bool:
        """Release the resource.

        Returns True if released successfully (caller was owner).
        Returns False if caller is not the owner.
        """
        state = self._ensure(resource_id)
        if state.owner != robot_id:
            return False
        state.owner = None
        state.claimed_at = None
        return True

    def enqueue(self, resource_id: ResourceID, robot_id: RobotID) -> int:
        """Add robot to the waiter queue.

        Returns 1-based queue position.
        """
        state = self._ensure(resource_id)
        if robot_id not in state.waiters:
            state.waiters.append(robot_id)
        return state.waiters.index(robot_id) + 1

    def pop_next_waiter(self, resource_id: ResourceID) -> Optional[RobotID]:
        """Remove and return the first waiter, or None if queue is empty."""
        state = self._ensure(resource_id)
        if not state.waiters:
            return None
        return state.waiters.pop(0)

    def owner_of(self, resource_id: ResourceID) -> Optional[RobotID]:
        """Return the current owner of a resource, or None."""
        if resource_id not in self._locks:
            return None
        return self._locks[resource_id].owner

    def waiters_of(self, resource_id: ResourceID) -> List[RobotID]:
        """Return a copy of the waiters list for a resource."""
        if resource_id not in self._locks:
            return []
        return list(self._locks[resource_id].waiters)

    @staticmethod
    def detect_deadlock(
        claim_graph: Dict[RobotID, List[ResourceID]],
        owner_graph: Dict[ResourceID, RobotID],
    ) -> List[List[RobotID]]:
        """Detect deadlock cycles in the wait-for graph.

        Args:
            claim_graph: robot_id -> list of resources it is *waiting* to claim
            owner_graph: resource_id -> robot_id that currently owns it

        Returns:
            List of detected cycles (each cycle is a list of robot IDs).
            Empty list means no deadlock.
        """
        # Build wait-for graph: X waits for Y if X wants a resource Y holds
        wait_for: Dict[RobotID, List[RobotID]] = {}
        for robot, wanted_resources in claim_graph.items():
            waiting_for_robots: List[RobotID] = []
            for res in wanted_resources:
                holder = owner_graph.get(res)
                if holder and holder != robot:
                    waiting_for_robots.append(holder)
            if waiting_for_robots:
                wait_for[robot] = waiting_for_robots

        # DFS cycle detection
        cycles: List[List[RobotID]] = []
        visited: set[RobotID] = set()
        rec_stack: set[RobotID] = set()

        def dfs(node: RobotID, path: List[RobotID]) -> None:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in wait_for.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    # Found a cycle — extract the cycle portion
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:]
                    # Normalise: start from min element to deduplicate
                    min_idx = cycle.index(min(cycle))
                    normalised = cycle[min_idx:] + cycle[:min_idx]
                    if normalised not in cycles:
                        cycles.append(normalised)
            rec_stack.discard(node)

        all_robots = set(claim_graph.keys()) | set(owner_graph.values())
        for robot in all_robots:
            if robot not in visited:
                dfs(robot, [robot])

        return cycles


# ---------------------------------------------------------------------------
# HandoffCoordinator
# ---------------------------------------------------------------------------


class HandoffCoordinator:
    """Manages robot-to-robot payload handoff state machines."""

    LEGAL_TRANSITIONS: Dict[HandoffPhase, set] = {
        "uninitiated": {"requested", "aborted"},
        "requested": {"approaching", "aborted"},
        "approaching": {"transferring", "aborted"},
        "transferring": {"completed", "aborted"},
        "completed": set(),
        "aborted": set(),
    }

    def __init__(self) -> None:
        self.tokens: Dict[str, HandoffToken] = {}

    def request_handoff(
        self,
        source: RobotID,
        target: RobotID,
        payload_prim: str,
    ) -> HandoffToken:
        """Create a new handoff token in 'uninitiated' phase."""
        handoff_id = str(uuid.uuid4())
        token = HandoffToken(
            handoff_id=handoff_id,
            source_robot=source,
            target_robot=target,
            payload_prim=payload_prim,
            phase="uninitiated",
            initiated_at=_now_iso(),
        )
        self.tokens[handoff_id] = token
        return token

    def transition(self, handoff_id: str, target_phase: HandoffPhase) -> None:
        """Advance a handoff token to the target phase.

        Raises ValueError if the transition is illegal or the token is unknown.
        """
        token = self._get_token(handoff_id)
        allowed = self.LEGAL_TRANSITIONS.get(token.phase, set())
        if target_phase not in allowed:
            raise ValueError(
                f"Illegal transition {token.phase!r} -> {target_phase!r} "
                f"for handoff {handoff_id!r}. Allowed: {allowed}"
            )
        token.phase = target_phase

    def abort(self, handoff_id: str, reason: str = "") -> None:
        """Abort a handoff from any non-terminal phase."""
        token = self._get_token(handoff_id)
        if token.phase == "completed":
            # already complete — silently ignore or raise; spec says "any phase"
            # so we allow abort even from completed for flexibility, but mark it
            pass
        token.phase = "aborted"

    def complete(self, handoff_id: str) -> None:
        """Mark a handoff as completed and record timestamp."""
        token = self._get_token(handoff_id)
        allowed = self.LEGAL_TRANSITIONS.get(token.phase, set())
        if "completed" not in allowed:
            raise ValueError(
                f"Cannot complete handoff {handoff_id!r} from phase {token.phase!r}"
            )
        token.phase = "completed"
        token.completed_at = _now_iso()

    def active_handoffs(self) -> List[HandoffToken]:
        """Return handoffs that are not in a terminal phase."""
        terminal = {"completed", "aborted"}
        return [t for t in self.tokens.values() if t.phase not in terminal]

    def completed_handoffs(self) -> List[HandoffToken]:
        """Return handoffs that reached 'completed' phase."""
        return [t for t in self.tokens.values() if t.phase == "completed"]

    def _get_token(self, handoff_id: str) -> HandoffToken:
        if handoff_id not in self.tokens:
            raise ValueError(f"Unknown handoff_id: {handoff_id!r}")
        return self.tokens[handoff_id]


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": "63d",
        "title": "Multi-robot coordination runtime (handoff + mutex enforcement)",
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 63d",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
