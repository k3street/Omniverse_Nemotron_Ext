"""Track what a planner run spends on model calls, and stop it at a cap.

A run issues hundreds of calls with a high-detail image attached to each, so
the bill is decided long before anyone looks at it. Counting calls by hand
after the fact -- which is how the previous campaign was kept near its limit --
tells you what you already spent, not what you are about to.

Two decisions worth stating:

* An unknown model raises rather than metering at zero. A silent $0 ledger on
  a model we forgot to price is indistinguishable from a cheap run, and the
  cap would never fire.
* The cap is checked BEFORE each call, using the most expensive call seen so
  far as the estimate of the next one. Checking only afterwards means the call
  that breaks the cap has already been paid for.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# USD per million tokens, as published for direct API access.
# gpt-6-astra: prompts over 272K input tokens are billed at 2x input and
# 1.5x output for the whole request.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-6-astra": (10.0, 50.0),
    "gpt-5.1": (1.25, 10.0),
    "gemini-robotics-er-2-preview": (0.30, 2.50),
    "gemini-robotics-er-1.6-preview": (0.30, 2.50),
}

# The long-context surcharge is a per-model pricing rule, not a general one:
# gpt-6-astra bills a request with more than 272K input tokens at 2x input and
# 1.5x output for the whole request. Applying it to every model overcharged
# them, and applying it to caller-supplied rates overrode the caller.
LONG_CONTEXT_TOKENS = 272_000
LONG_CONTEXT_INPUT_MULTIPLIER = 2.0
LONG_CONTEXT_OUTPUT_MULTIPLIER = 1.5
LONG_CONTEXT_MODELS = ("gpt-6-astra",)


class BudgetExceeded(RuntimeError):
    """Raised instead of issuing a call that would break the cap."""


def price_for(model: str) -> tuple[float, float]:
    """USD per million input/output tokens, by longest matching prefix."""
    if model in PRICES:
        return PRICES[model]
    matches = [key for key in PRICES if model.startswith(key)]
    if matches:
        return PRICES[max(matches, key=len)]
    raise KeyError(
        f"No price for model {model!r}; add it to PRICES or pass explicit rates. "
        "Metering an unpriced model at zero would silently disable the cap."
    )


def call_cost(
    model: str, prompt_tokens: int, completion_tokens: int,
    rates: tuple[float, float] | None = None,
) -> float:
    """Cost of one call in USD."""
    if rates is not None:
        # The caller stated its own rates; they are authoritative.
        input_rate, output_rate = rates
        surcharged = False
    else:
        input_rate, output_rate = price_for(model)
        surcharged = model.startswith(LONG_CONTEXT_MODELS)
    if surcharged and prompt_tokens > LONG_CONTEXT_TOKENS:
        input_rate *= LONG_CONTEXT_INPUT_MULTIPLIER
        output_rate *= LONG_CONTEXT_OUTPUT_MULTIPLIER
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


@dataclass
class BudgetLedger:
    """Running spend for one run, with a hard cap."""

    model: str
    cap_usd: float
    rates: tuple[float, float] | None = None
    spent_usd: float = 0.0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    most_expensive_call_usd: float = field(default=0.0)

    @property
    def remaining_usd(self) -> float:
        return max(self.cap_usd - self.spent_usd, 0.0)

    def check_before_call(self) -> None:
        """Refuse a call the remaining budget probably cannot cover."""
        if self.spent_usd >= self.cap_usd:
            raise BudgetExceeded(
                f"model budget spent: ${self.spent_usd:.2f} of ${self.cap_usd:.2f} "
                f"over {self.calls} calls"
            )
        if self.most_expensive_call_usd > self.remaining_usd:
            raise BudgetExceeded(
                f"${self.remaining_usd:.2f} left of ${self.cap_usd:.2f} will not cover "
                f"another call; the priciest so far cost ${self.most_expensive_call_usd:.2f}"
            )

    def record(self, prompt_tokens: int, completion_tokens: int) -> float:
        cost = call_cost(self.model, prompt_tokens, completion_tokens, self.rates)
        self.spent_usd += cost
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.most_expensive_call_usd = max(self.most_expensive_call_usd, cost)
        return cost

    def summary(self) -> str:
        return (
            f"{self.calls} calls, {self.prompt_tokens} in / {self.completion_tokens} out, "
            f"${self.spent_usd:.2f} of ${self.cap_usd:.2f}"
        )
