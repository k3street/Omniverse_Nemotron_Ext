"""Unit tests for phase2_triage._classify."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.qa.phase2_triage import _classify


class TestClassify:
    def test_infeasible_to_template_fix(self):
        cls, _ = _classify("infeasible", "stable_fail")
        assert cls == "2a-TEMPLATE_FIX"

    def test_overconstrained_to_template_tune(self):
        cls, _ = _classify("overconstrained", "flaky")
        assert cls == "2b-TEMPLATE_TUNE"

    def test_tightly_feasible_failing_to_controller_tune(self):
        cls, _ = _classify("tightly_feasible", "stable_fail")
        assert cls == "2c-CONTROLLER_TUNE"

        cls, _ = _classify("tightly_feasible", "flaky")
        assert cls == "2c-CONTROLLER_TUNE"

    def test_feasible_failing_to_controller_bug(self):
        cls, _ = _classify("feasible", "stable_fail")
        assert cls == "2d-CONTROLLER_BUG"

    def test_feasible_passing_to_stable_ok(self):
        cls, _ = _classify("feasible", "stable_ok")
        assert cls == "STABLE_OK"

    def test_tightly_feasible_passing_to_marginal_ok(self):
        cls, _ = _classify("tightly_feasible", "stable_ok")
        assert cls == "MARGINAL_OK"

    def test_no_feasibility_with_failure_unknown(self):
        cls, _ = _classify(None, "stable_fail")
        assert cls == "UNKNOWN_NEED_DIAGNOSE"

    def test_no_feasibility_with_pass_stable_ok(self):
        cls, _ = _classify(None, "stable_ok")
        assert cls == "STABLE_OK"

    def test_only_feasibility_failing_unknown(self):
        # Without function_gate status, classify based on feasibility alone
        cls, _ = _classify(None, None)
        assert cls == "UNKNOWN_NEED_DIAGNOSE"

    def test_infeasible_overrides_passing_baseline(self):
        # Infeasible verdict means scene is broken even if baseline passes
        # (probably means baseline is stale or test is flaky)
        cls, _ = _classify("infeasible", "stable_ok")
        assert cls == "2a-TEMPLATE_FIX"
