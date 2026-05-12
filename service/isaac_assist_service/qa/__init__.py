"""QA primitives — stable-baseline taxonomy + multi-run regression harness.

Public Isaac Assist machinery for the per-CP three-way classification
(`stable_ok` / `flaky` / `stable_fail`) that gates every later
"harness honesty" claim. The taxonomy, regression runner, and
baseline snapshot freeze/compare API are first-class IA primitives
rather than QA-script implementation details.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8d.
"""
