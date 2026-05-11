# Typed-Resolver / Introspection Architecture Findings

## Summary

13 typed-variable resolvers + 1 verifier (verify_pickplace_pipeline) +
1 acceptance-extractor (resolve_success_condition) shipped in PR #88.
Two-hour intensive test session 2026-05-06 produced concrete signals
about what the architecture buys and where it falls short.

## VR canary status (latest run, 2026-05-06)

| ID | Probe | Status | Notes |
|----|-------|--------|-------|
| VR-01..08 (excl 09) | resolver basics | mostly PASS | triple-perfect-marked earlier; one stochastic VR-12 dip on this run only |
| VR-10 | filter-deictic | PASS | agent disambiguated by name |
| VR-11 | empty-stage recovery | PASS | no_match honored |
| VR-12 | spacing 0.5m apart | FAIL | agent interpreted "0.5m apart" as gap, gave 0.6m center-to-center |
| VR-13 | vertical offset 40cm | FAIL | agent asked which robot rather than picking franka default |
| VR-14 | asks-when-vague | PASS | clarifying question fired |
| VR-15 | multi-turn refinement | FAIL | session ended after turn 1 again — fix may have regressed |
| VR-16 | 3x3 grid | PASS | computed positions correctly |
| VR-17 | compound ambiguity | PASS | agent asked about both unknowns |
| VR-18 | static assembly line | PASS (partial) | layout coherent, missing 3rd bin per spec |
| VR-19 | introspection self-verify | FAIL (architecturally) | agent introspected behaviorally but didn't use verify_pickplace_pipeline tool |

## What the architecture validates

- **Resolvers compose** (VR-08, VR-17): multiple resolvers fire on the same prompt and produce composable structured outputs.
- **Ambiguous-resolver-as-clarification-gate works** (VR-03, VR-17): when a resolver returns ambiguous=true, agents reliably ask the user instead of picking arbitrarily.
- **Empty-state recovery works** (VR-11): no_match short-circuits the build reflex.
- **Behavior-level introspection works** (VR-19): agent recognized it should self-check reach — and did so via inline run_usd_script.

## Where it falls short

- **Verifier tool not consistently picked.** VR-19's agent did the right introspection — but as inline code, not the canonical `verify_pickplace_pipeline` tool. The variable-extraction paradigm relies on agents discovering and selecting the right tool; for the verifier specifically, that didn't happen on first encounter.
- **Stochasticity remains.** VR-12 / VR-13 / VR-15 each pass sometimes and fail other times; not deterministic.
- **Test criteria sometimes too strict.** VR-12's "0.5m apart" is naturally ambiguous (gap vs center-to-center). Agent's interpretation was reasonable; test was prescriptive.

## Implications

The variable-extraction architecture is the right conceptual layer. It
captures the user's mental model (typed inputs → deterministic
resolvers → structured outputs → verified outcomes). But the agent's
*tool selection* is still LLM-judgment-driven; we don't get the
benefits without the agent picking the right resolver.

Two paths forward:

1. **Discoverability work**: tighter tool descriptions, more aliases,
   more `_ALWAYS_TOOLS` for verifier-class. Risk: returns to keyword-padding hack.
2. **Architectural pre-step**: an intent-classifier that EXPLICITLY
   says "this prompt is an X-skill, run resolve_skill_composition AND
   verify_X_pipeline before reply." Risk: re-introduces the spec-first
   pipeline that was reverted.

Or: accept the LLM-judgment layer is real and let the architecture be
*available* without forcing use. Behavioral introspection happened in
VR-19 even without the tool — that's the desirable outcome regardless
of which mechanism delivers it.

## Verifier value beyond agent use

`verify_pickplace_pipeline` is also valuable as a *deterministic test
asset*: canary tasks like VR-19 can call it from their success-criterion
checks to grade scenes. Even if the agent doesn't use it, the test
harness can.

## Commit boundaries

PR #88 commits 28-32: resolver and verifier implementation + canary tests.
This file is the post-hoc analysis.
