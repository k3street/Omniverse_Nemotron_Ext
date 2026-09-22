"""L0 tests for the model spend cap.

The cap exists to stop a run before it overspends, so the cases that matter
are the ones where it must refuse: an unpriced model, a budget too thin for
another call, and the long-context surcharge that makes a single call cost
several times its headline rate.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from scripts.model_budget import (
    BudgetExceeded,
    BudgetLedger,
    call_cost,
    price_for,
)

ASTRA = "gpt-6-astra"


def test_cost_uses_published_rates():
    # 100k in, 10k out at $10/$50 per million.
    assert call_cost(ASTRA, 100_000, 10_000) == pytest.approx(1.0 + 0.5)


def test_long_prompts_cost_more_per_token():
    # Over 272k input the whole request is billed at 2x input, 1.5x output.
    under = call_cost(ASTRA, 272_000, 1_000)
    over = call_cost(ASTRA, 272_001, 1_000)
    assert over > under * 1.9


def test_an_unpriced_model_raises_rather_than_metering_at_zero():
    # Silently metering at zero would disable the cap entirely.
    with pytest.raises(KeyError):
        price_for("some-model-we-forgot")


def test_prefix_match_picks_the_longest_entry():
    assert price_for("gpt-6-astra-2026-09-08") == price_for(ASTRA)


def test_ledger_accumulates_and_reports_remaining():
    ledger = BudgetLedger(model=ASTRA, cap_usd=25.0)
    ledger.record(100_000, 10_000)
    assert ledger.spent_usd == pytest.approx(1.5)
    assert ledger.remaining_usd == pytest.approx(23.5)
    assert ledger.calls == 1


def test_call_is_refused_once_the_cap_is_spent():
    ledger = BudgetLedger(model=ASTRA, cap_usd=1.0)
    ledger.record(100_000, 10_000)  # $1.50, over cap
    with pytest.raises(BudgetExceeded):
        ledger.check_before_call()


def test_call_is_refused_when_the_remainder_cannot_cover_another():
    # Still under the cap, but the priciest call so far would break it.
    ledger = BudgetLedger(model=ASTRA, cap_usd=2.0)
    ledger.record(100_000, 10_000)  # $1.50 spent, $0.50 left
    with pytest.raises(BudgetExceeded):
        ledger.check_before_call()


def test_a_healthy_ledger_allows_the_next_call():
    ledger = BudgetLedger(model=ASTRA, cap_usd=25.0)
    ledger.record(100_000, 10_000)
    ledger.check_before_call()  # must not raise


def test_explicit_rates_override_the_table():
    ledger = BudgetLedger(model="anything", cap_usd=5.0, rates=(1.0, 2.0))
    assert ledger.record(1_000_000, 1_000_000) == pytest.approx(3.0)
