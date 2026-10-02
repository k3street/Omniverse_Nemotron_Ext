import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate_planner import infrastructure_cause  # noqa: E402


def test_an_empty_provider_account_is_infrastructure():
    log = '"type": "insufficient_quota",\n"code": "credit_balance_exhausted"'
    assert infrastructure_cause(log, {}) == "model provider out of credits"


def test_a_dropped_connection_is_infrastructure():
    assert infrastructure_cause("ERROR: [Errno 104] Connection reset by peer", {}) == "network connection dropped"


def test_an_outside_interrupt_is_infrastructure():
    assert infrastructure_cause("    time.sleep(5)\nKeyboardInterrupt", {}) == "interrupted from outside the run"


def test_a_startup_hang_is_infrastructure():
    assert infrastructure_cause("", {"outcome": "hung_at_startup"}) == "simulator hung at startup"


def test_a_policy_failure_is_not():
    log = "ERROR: Scheduler motion handoff budget exhausted without selecting a different runtime operation: 3/3"
    assert infrastructure_cause(log, {"outcome": "exited"}) is None
