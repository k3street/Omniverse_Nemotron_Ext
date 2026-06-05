"""Phase 63d — Multi-robot coordination runtime: pytest suite.

Gate: mutex prevents concurrent claim, handoff transfers token, deadlock detected.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Fixtures / imports
# ---------------------------------------------------------------------------


@pytest.fixture
def coord_module():
    from service.isaac_assist_service.multimodal import sub_phase_63d_multi_robot_coordination as m
    return m


@pytest.fixture
def mutex(coord_module):
    return coord_module.RobotResourceMutex()


@pytest.fixture
def coordinator(coord_module):
    return coord_module.HandoffCoordinator()


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_metadata(coord_module):
    md = coord_module.get_phase_metadata()
    assert md["phase"] == "63d"
    assert md["status"] == "landed"
    assert "handoff" in md["title"].lower() or "coordination" in md["title"].lower()


# ---------------------------------------------------------------------------
# 2. Mutex — fresh resource
# ---------------------------------------------------------------------------


def test_try_acquire_fresh_resource(mutex):
    """Acquiring a fresh (unclaimed) resource returns True."""
    result = mutex.try_acquire("conveyor_1", "robot_A")
    assert result is True
    assert mutex.owner_of("conveyor_1") == "robot_A"


# ---------------------------------------------------------------------------
# 3. Mutex — held by another robot
# ---------------------------------------------------------------------------


def test_try_acquire_held_by_other_returns_false(mutex):
    """Acquiring a resource already held by another robot returns False."""
    mutex.try_acquire("conveyor_1", "robot_A")
    result = mutex.try_acquire("conveyor_1", "robot_B")
    assert result is False
    assert mutex.owner_of("conveyor_1") == "robot_A"


# ---------------------------------------------------------------------------
# 4. Mutex — re-entrant by same robot
# ---------------------------------------------------------------------------


def test_try_acquire_reentrant_same_robot(mutex):
    """A robot can re-acquire a resource it already holds (re-entrant)."""
    mutex.try_acquire("conveyor_1", "robot_A")
    result = mutex.try_acquire("conveyor_1", "robot_A")
    assert result is True


# ---------------------------------------------------------------------------
# 5. Mutex — release by non-owner
# ---------------------------------------------------------------------------


def test_release_by_non_owner_returns_false(mutex):
    """Releasing a resource owned by another robot returns False."""
    mutex.try_acquire("conveyor_1", "robot_A")
    result = mutex.release("conveyor_1", "robot_B")
    assert result is False
    assert mutex.owner_of("conveyor_1") == "robot_A"


# ---------------------------------------------------------------------------
# 6. Mutex — release by owner + re-acquire
# ---------------------------------------------------------------------------


def test_release_by_owner_then_re_acquire(mutex):
    """After a proper release, the resource can be acquired by another robot."""
    mutex.try_acquire("conveyor_1", "robot_A")
    released = mutex.release("conveyor_1", "robot_A")
    assert released is True
    assert mutex.owner_of("conveyor_1") is None

    # Now robot_B can claim it
    result = mutex.try_acquire("conveyor_1", "robot_B")
    assert result is True
    assert mutex.owner_of("conveyor_1") == "robot_B"


# ---------------------------------------------------------------------------
# 7. Mutex — enqueue + pop_next_waiter FIFO
# ---------------------------------------------------------------------------


def test_enqueue_and_pop_next_waiter_fifo(mutex):
    """Enqueue preserves insertion order; pop_next_waiter returns first inserted."""
    mutex.try_acquire("conveyor_1", "robot_A")
    pos_b = mutex.enqueue("conveyor_1", "robot_B")
    pos_c = mutex.enqueue("conveyor_1", "robot_C")

    assert pos_b == 1
    assert pos_c == 2
    assert mutex.waiters_of("conveyor_1") == ["robot_B", "robot_C"]

    next_waiter = mutex.pop_next_waiter("conveyor_1")
    assert next_waiter == "robot_B"
    assert mutex.waiters_of("conveyor_1") == ["robot_C"]

    next_waiter2 = mutex.pop_next_waiter("conveyor_1")
    assert next_waiter2 == "robot_C"
    assert mutex.waiters_of("conveyor_1") == []

    assert mutex.pop_next_waiter("conveyor_1") is None


# ---------------------------------------------------------------------------
# 8. Deadlock — simple 2-cycle
# ---------------------------------------------------------------------------


def test_detect_deadlock_two_cycle(coord_module):
    """A <-> B cycle: A holds R1 wants R2; B holds R2 wants R1."""
    claim_graph = {
        "robot_A": ["R2"],
        "robot_B": ["R1"],
    }
    owner_graph = {
        "R1": "robot_A",
        "R2": "robot_B",
    }
    cycles = coord_module.RobotResourceMutex.detect_deadlock(claim_graph, owner_graph)
    assert len(cycles) >= 1
    # Every detected cycle should contain both robots
    flat = {r for cycle in cycles for r in cycle}
    assert "robot_A" in flat
    assert "robot_B" in flat


# ---------------------------------------------------------------------------
# 9. Deadlock — no cycle returns empty
# ---------------------------------------------------------------------------


def test_detect_deadlock_no_cycle(coord_module):
    """When there is no circular wait, detect_deadlock returns an empty list."""
    claim_graph = {
        "robot_A": ["R2"],
    }
    owner_graph = {
        "R1": "robot_A",
        "R2": "robot_C",  # robot_C doesn't want anything
    }
    cycles = coord_module.RobotResourceMutex.detect_deadlock(claim_graph, owner_graph)
    assert cycles == []


# ---------------------------------------------------------------------------
# 10. HandoffCoordinator — request_handoff creates token in "uninitiated"
# ---------------------------------------------------------------------------


def test_request_handoff_creates_uninitiated_token(coordinator):
    """request_handoff() produces a HandoffToken with phase='uninitiated'."""
    token = coordinator.request_handoff("robot_A", "robot_B", "/World/Box")
    assert token.phase == "uninitiated"
    assert token.source_robot == "robot_A"
    assert token.target_robot == "robot_B"
    assert token.payload_prim == "/World/Box"
    assert token.handoff_id in coordinator.tokens


# ---------------------------------------------------------------------------
# 11. LEGAL_TRANSITIONS — forward chain
# ---------------------------------------------------------------------------


def test_legal_transitions_full_happy_path(coord_module, coordinator):
    """uninitiated -> requested -> approaching -> transferring -> completed."""
    token = coordinator.request_handoff("robot_A", "robot_B", "/World/Box")
    hid = token.handoff_id

    coordinator.transition(hid, "requested")
    assert coordinator.tokens[hid].phase == "requested"

    coordinator.transition(hid, "approaching")
    assert coordinator.tokens[hid].phase == "approaching"

    coordinator.transition(hid, "transferring")
    assert coordinator.tokens[hid].phase == "transferring"

    coordinator.complete(hid)
    assert coordinator.tokens[hid].phase == "completed"
    assert coordinator.tokens[hid].completed_at is not None


# ---------------------------------------------------------------------------
# 12. LEGAL_TRANSITIONS — illegal transition raises ValueError
# ---------------------------------------------------------------------------


def test_illegal_transition_raises_value_error(coordinator):
    """Jumping from 'uninitiated' to 'transferring' directly raises ValueError."""
    token = coordinator.request_handoff("robot_A", "robot_B", "/World/Box")
    with pytest.raises(ValueError, match="Illegal transition"):
        coordinator.transition(token.handoff_id, "transferring")


# ---------------------------------------------------------------------------
# 13. abort — callable from any non-terminal phase
# ---------------------------------------------------------------------------


def test_abort_from_any_phase(coordinator):
    """abort() can be called from any phase and marks the token as 'aborted'."""
    for starting_phase_transitions in [
        [],
        ["requested"],
        ["requested", "approaching"],
        ["requested", "approaching", "transferring"],
    ]:
        token = coordinator.request_handoff("robot_A", "robot_B", "/World/Box")
        hid = token.handoff_id
        for phase in starting_phase_transitions:
            coordinator.transition(hid, phase)
        coordinator.abort(hid, reason="test abort")
        assert coordinator.tokens[hid].phase == "aborted"


# ---------------------------------------------------------------------------
# 14. active_handoffs / completed_handoffs filtering
# ---------------------------------------------------------------------------


def test_active_and_completed_handoffs_filter(coordinator):
    """active_handoffs returns non-terminal tokens; completed_handoffs returns only 'completed'."""
    t1 = coordinator.request_handoff("robot_A", "robot_B", "/World/Box1")
    t2 = coordinator.request_handoff("robot_A", "robot_C", "/World/Box2")
    t3 = coordinator.request_handoff("robot_B", "robot_C", "/World/Box3")

    # Complete t1 through its full chain
    coordinator.transition(t1.handoff_id, "requested")
    coordinator.transition(t1.handoff_id, "approaching")
    coordinator.transition(t1.handoff_id, "transferring")
    coordinator.complete(t1.handoff_id)

    # Abort t2
    coordinator.abort(t2.handoff_id)

    # t3 stays active
    active = coordinator.active_handoffs()
    completed = coordinator.completed_handoffs()

    active_ids = {t.handoff_id for t in active}
    completed_ids = {t.handoff_id for t in completed}

    assert t3.handoff_id in active_ids
    assert t1.handoff_id not in active_ids
    assert t2.handoff_id not in active_ids

    assert t1.handoff_id in completed_ids
    assert t2.handoff_id not in completed_ids
    assert t3.handoff_id not in completed_ids
