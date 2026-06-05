# Round 11 — Motion-Controller Retrieval Filter

**Date**: 2026-05-15
**Status**: Landed, env-gated (RETRIEVAL_MC_FILTER=off by default)

---

## §1 Design Rationale

### Why post-filter (not pre-filter via ChromaDB `where`)

The `motion_controllers` field lives in the template JSON blob, not in ChromaDB
metadata. Pushing it into ChromaDB metadata would require:
- A metadata schema migration (re-embed all 63+ templates)
- Careful ChromaDB `$in`/`$contains` syntax for list fields (poorly supported)
- Re-indexing on every template update

Post-filter avoids all of this. ChromaDB returns `n_results` candidates;
we filter by motion-controller constraints afterward. The only cost is that
effective `top_k` can be reduced by the filter — acceptable because:
- Most queries won't use the filter (None by default)
- The unmigrated-template inclusion rule keeps recall high

### Why env-gated (RETRIEVAL_MC_FILTER=off by default)

The filter is new infra with zero production consumers today. Env-gating lets
us ship and QC the logic without risking regression in the live retrieval path.
Flip to `on` once:
1. End-to-end tests confirm correct behavior with real ChromaDB
2. At least one caller (orchestrator or canonical_instantiator) passes the
   constraint from user-intent parsing

### Backward compatibility

When `motion_controller_constraint=None` (the new parameter's default), the
entire filter code block is skipped — the call path is byte-identical to
the pre-Round-11 baseline. Existing callers (orchestrator line 920,
canonical_instantiator) pass no constraint and are unaffected.

### Unmigrated-template inclusion rule

Templates without a `motion_controllers` field pass all filters. This is the
safe default: we don't penalize templates that predate the field. As the
field propagates (62+ already done), the filter becomes progressively stricter.

---

## §2 Files Changed + LOC

| File | Change | Net LOC |
|------|--------|---------|
| `service/isaac_assist_service/chat/tools/template_retriever.py` | Add `import os` + `_mc_filter_enabled()` + `_parse_mc_base_name()` + `_apply_motion_controller_filter()` + update `retrieve_templates_with_scores` signature + docstring | +78 |
| `tests/test_motion_controller_retrieval_filter.py` | New test file | +219 |

No changes to: orchestrator.py, canonical_instantiator.py, templates, lint
scripts, or schema files.

---

## §3 Test Coverage

**19 tests** in `tests/test_motion_controller_retrieval_filter.py`:

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestNoFilter` | 3 | None / empty constraint → no-op |
| `TestMustVerified` | 3 | `must_verified` excludes wrong-controller templates; no-mc included |
| `TestMustNotFailed` | 2 | `must_not_failed` excludes matching failed keys; unrelated failures pass |
| `TestNoMcField` | 3 | No `motion_controllers` field → included for all filter combinations |
| `TestEnvGating` | 4 | env off → filter ignored; env on → filter active; `_mc_filter_enabled()` logic |
| `TestBaseNameMatching` | 4 | `curobo@1.8.2` → `curobo`; versioned filter vs unversioned template; helper unit tests |

All 19 pass. Broader suite: 59/59 pass (includes rehydration, lint, dispatch).

---

## §4 What This Unlocks

Users / orchestrators can now ask for controller-specific templates:

```python
# "Find me Franka pick-place that works with cuRobo"
retrieve_templates_with_scores(
    "Franka pick-place",
    top_k=5,
    motion_controller_constraint={"must_verified": ["curobo"]},
)

# "Find assembly templates that haven't failed with admittance control"
retrieve_templates_with_scores(
    "assembly multi-robot",
    top_k=5,
    motion_controller_constraint={"must_not_failed": ["admittance"]},
)

# Combined: verified with cuRobo AND not failed with cortex
retrieve_templates_with_scores(
    "pick and place",
    top_k=5,
    motion_controller_constraint={
        "must_verified": ["curobo"],
        "must_not_failed": ["cortex"],
    },
)
```

The `retrieve_with_intent_filter` path (structural-filter-first) is **not** yet
wired to motion_controller_constraint — it calls `retrieve_templates_with_scores`
as its fallback but doesn't pass a constraint. That's a follow-up wiring task.

---

## §5 Follow-Up

### When to flip RETRIEVAL_MC_FILTER=on by default

Prerequisite checklist:
1. At least one real caller passes `motion_controller_constraint` from intent
2. Integration test with live ChromaDB confirms top-K reduction is acceptable
   (doesn't drop to 0 for common queries when env is on)
3. Orchestrator / user-query parser produces constraint from natural language
   ("that works with cuRobo", "not using admittance")

### Ranking-boost extension (prefer_verified)

The constraint shape already includes a `prefer_verified` key slot in the spec
design. Implementation: after `_apply_motion_controller_filter`, re-sort `out`
to push entries whose `verified` list matches `prefer_verified` to the front,
keeping similarity score as a tiebreaker. This is a ~10-line addition and
doesn't require env re-gating (same RETRIEVAL_MC_FILTER gate suffices).

### Wire constraint into retrieve_with_intent_filter

`retrieve_with_intent_filter` → Stage-2 ChromaDB query → then calls
`retrieve_templates_with_scores` as fallback. Add `motion_controller_constraint`
passthrough to both the fallback call and the Stage-2 post-processing block.
