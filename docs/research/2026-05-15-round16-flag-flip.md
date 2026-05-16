# Round 16 — MULTIMODAL_TEXT_INTENT Default Flip

**Date:** 2026-05-16
**Branch state:** post-R15c (commit bbd67d3)

---

## §1 Justification

R15c confirmed struct-filter-first retrieval beats the legacy embedding-only
baseline:

| Metric    | Baseline (R13) | Struct-filter (R15c) |
|-----------|---------------|----------------------|
| hit@1     | 0.833         | **0.867** (+0.034)   |
| hit@3     | 0.900         | 0.900 (parity)       |

The improvement is driven by:
- Stage 1 structural pre-filter (pattern_hint + counts) applied before vector
  similarity, removing wrong-geometry candidates early.
- R15 query-bug fix: null-signal specs no longer bypass Stage 2 entirely.
- R15b Stage 1 unconstrained-default fix: unset fields default to
  "unconstrained" rather than zero, allowing broader recall.

With hit@1 above baseline and zero regressions in 59 retrieval-related tests,
promoting the flag from opt-in to opt-out is warranted.

---

## §2 Files Changed

| File | Change | LOC delta |
|------|--------|-----------|
| `service/isaac_assist_service/chat/orchestrator.py:895-898` | Default `"off"` → `"on"`, invert logic to `not in (off values)` | 0 net (comment updated) |
| `tests/test_multimodal_text_intent_flag.py` | New: 3-class unit test for flag evaluation | +67 |

**Exact change in orchestrator.py (line 896):**

Before:
```python
os.environ.get("MULTIMODAL_TEXT_INTENT", "off").lower()
in ("on", "true", "1", "yes")
```

After:
```python
os.environ.get("MULTIMODAL_TEXT_INTENT", "on").lower()
not in ("off", "false", "0", "no")
```

---

## §3 Test Results

### Flag unit tests (new, R16)
File: `tests/test_multimodal_text_intent_flag.py`

- Test 1: no env-var set → `True` (default ON) — PASS
- Test 2: `MULTIMODAL_TEXT_INTENT=off/false/0/no` (8 parametrize values) → `False` — PASS
- Test 3: `MULTIMODAL_TEXT_INTENT=on/true/1/yes` (8 parametrize values) → `True` — PASS

### Full retrieval regression suite
Command:
```
python -m pytest tests/test_multimodal_text_intent_flag.py \
  tests/test_retrieval_struct_filter.py \
  tests/test_struct_filter_query_construction.py \
  tests/test_template_cache_rehydration.py \
  tests/test_motion_controller_retrieval_filter.py \
  tests/test_canonical_lint.py -q --no-header
```
Result: **59 passed, 0 failed** (25 s)

### Benchmark (new default, no env-var)
```
python tests/test_retrieval_struct_filter.py
```
Result: `hit@1=0.867  hit@3=0.900  mode_accuracy=0.700  hard_instantiate_rate=0.167`
struct_path=16/30  fallback=14/30

Matches R15c — the flag flip does not alter benchmark behaviour (the
benchmark calls `retrieve_with_intent_filter` directly, bypassing the flag).

---

## §4 Rollback Procedure

No code change required. Set the environment variable before starting the
service:

```bash
export MULTIMODAL_TEXT_INTENT=off
```

Or in `.env` / `launch_service.sh`:

```
MULTIMODAL_TEXT_INTENT=off
```

Accepted off-values: `off`, `false`, `0`, `no` (case-insensitive).

---

## §5 What's Next

With struct-filter default ON and the flag mechanism validated, the next
meaningful work item is:

**R12 — novel_pattern schema extension**

The `pattern_hint` field in `LayoutSpec.intent` currently supports a fixed
enum of known patterns (pick_place, assembly, inspection, etc.). R12 extends
this to accept freeform `novel_pattern` strings so that prompts for
non-catalogued workflows can still flow through Stage 1 with a degraded-but-
non-zero filter, rather than falling back to pure embedding similarity.

This was previously deferred because it required confidence that the
struct-filter path was stable in production. R16 provides that confidence.
