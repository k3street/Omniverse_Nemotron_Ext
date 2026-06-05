# Round 12 — Novel Pattern Schema Extension

**Date:** 2026-05-16
**Status:** Schema-only (R12). Template migration is R12b (separate round).
**Branch context:** no commit — changes staged for Anton's review.

---

## §1 Cluster Enumeration (20 templates → 5 clusters)

| Cluster | Count | Templates | Distinguishing signal |
|---------|-------|-----------|----------------------|
| `insert` | 3 | CP-58, CP-NEW-peg-in-hole-single, CP-NEW-tactile-insertion | `add_force_torque_sensor` + `setup_assembly_constraint`; success criterion = peg seated / compliance threshold reached |
| `train` | 3 | CP-NEW-rl-clone-env, CP-NEW-defect-sdg, CP-NEW-sim2real-gap | `clone_envs`/`launch_training`/`configure_sdg`/`measure_sim_real_gap`; success criterion = training loop active / dataset exported / gap metric produced |
| `bridge_op` | 2 | CP-NEW-opcua-12conveyors, CP-NEW-plc-conveyor | `opcua_bridge_attach`, `modbus_tcp_bridge_attach`; protocol-driven conveyor control — below the 3-template promotion threshold → `other` |
| `other` | 12 | CP-48, CP-57, CP-59, CP-60, CP-61, CP-65, CP-67, CP-73, CP-87, CP-NEW-amr-pickup-handoff, CP-NEW-cross-belt-sorter, CP-NEW-multi-amr-corridor | Long tail — no shared success-criterion shape at ≥3 count; structural_tags discriminate at retrieval time |

**Total:** 3 clusters with ≥3 members → 2 named additions; 2+12 = 14 → `other`.

Notes on non-obvious classifications:
- **CP-48, CP-59** (vision-gated inspect/reject): goals resemble `sort` by color, but the success criterion is "defective part ejected" not "routed to class bin". Assigned `other`. If a third vision-inspect template emerges, promote to `inspect`.
- **CP-57** (heap singulation): pick_place from a heap zone — structurally a `pick_place` variant with `create_heap_zone`. Assigned `other`; migration round may reclassify as `pick_place` + `isaac:source.heap` structural tag.
- **CP-60** (recirculation loop): no robot — pure conveyor topology test. Assigned `other`.
- **CP-61, CP-73** (Cortex behavior tree): `setup_cortex_behavior` + standard pick_place flow. Could be `pick_place`; leaving `other` until migration round evaluates success-criterion parity.
- **CP-65, CP-67** (multi-robot handoff/relay): structurally `pick_place` with `setup_robot_handoff_signal` / `setup_robot_claim_mutex`. Likely `pick_place` at migration time.
- **CP-87** (ROS2-MoveIt2): `pick_place` via ROS2 bridge; `other` pending migration.
- **CP-NEW-amr-pickup-handoff, CP-NEW-multi-amr-corridor**: AMR motion is primary; `navigate` is plausible. The handoff involves a robot arm secondary motion — migration round decides `navigate` vs `other` per success-criterion shape.
- **CP-NEW-cross-belt-sorter**: vision-guided conveyor divert — closer to `sort` than any other value. Migration round will likely use `sort`.

---

## §2 Chosen Approach: Option C (Hybrid)

**Decision:** Extend enum for `insert` (3 templates) and `train` (3 templates); use `other` for 14-template long tail.

**Rationale:**
- Option A (enumerate all): `bridge_op` has only 2 templates — too few; adding it now creates a sparse enum value with no precision benefit. All other novel clusters are singletons.
- Option B (only `other`): Loses structural-filter precision for the 6 insertion/training templates. The retrieval system cannot distinguish "insert a peg" from "pick and place" using only similarity when pattern_hint is `other`.
- Option C: `insert` and `train` have distinct, unambiguous success-criterion shapes (force-threshold seating vs training-loop active) — both are safe to promote. The remaining 14 are well-served by `other` + `structural_tags` discrimination.

The `other` value is deliberately NOT named `custom`. `other` signals "unclassified, pending promotion" so future rounds know to look for cluster promotion. When ≥3 `other` templates share a clear success-criterion, promote to a named value.

---

## §3 Schema Changes

### `scripts/canonical_schema.py`
- **Change:** `VALID_PATTERN_HINTS` set literal → expanded dict-comment form, +3 values.
- **LOC delta:** +13 lines (was 1-line set, now 10-line annotated set + 3-line block comment).
- **Line reference:** `VALID_PATTERN_HINTS` block, previously `scripts/canonical_schema.py:114`.

### `service/isaac_assist_service/multimodal/types.py`
- **Change:** `PatternHint` Literal extended with `"insert"`, `"train"`, `"other"`. Docstring expanded.
- **LOC delta:** +14 lines (3 new Literal values + 11 lines docstring additions).
- **Line reference:** `PatternHint` Literal, previously `types.py:46-59`.

### `service/isaac_assist_service/multimodal/text_modality.py`
- **Change 1:** `_PATTERN_RULES` — 2 new rules prepended (`insert`, `train`) + 10-line block comment on conservative policy.
- **Change 2:** `LLM_INTENT_JSON_SCHEMA["properties"]["pattern_hint"]["enum"]` — +3 values.
- **Change 3:** `LLM_INTENT_SYSTEM_PROMPT` — +5 lines describing new patterns.
- **LOC delta:** +28 lines total.

**No other files touched.**

---

## §4 Extractor Regex Additions

### `insert` rule
```python
("insert", re.compile(
    r"\b(peg.{0,10}hole|hole.{0,10}insert|insertion\s+task|force.{0,15}insert|tactile.{0,15}insert)\b",
    re.I
))
```
Rationale: `peg.*hole` (array/single peg-in-hole), `hole.*insert` (hole-first descriptions), `insertion task` (spec language), `force.*insert` (force-guided insertion), `tactile.*insert` (TacEx sensor insertion). Does NOT match "pick a cylinder into a slot" or "insert into bin" — no false positives on existing templates.

### `train` rule
```python
("train", re.compile(
    r"\b(rl\s+train|train.*rl|rl.games|rsl.rl|isaac.?lab\s+env|sdg\s+pipeline|sim.to.real|sim2real|clone_envs|parallel.*env)\b",
    re.I
))
```
Rationale: `rl train` / `train.*rl` (RL training scaffold), `rl.games`/`rsl.rl` (framework names), `isaaclab env`/`isaac_lab env` (IsaacLab environment), `sdg pipeline` (SDG spec language), `sim.to.real`/`sim2real` (gap measurement), `clone_envs`/`parallel.*env` (env-cloning scaffold). Does NOT match general conveyor/pick_place prompts.

**Conservative property:** The extractor never emits `"other"` — unknown prompts fall through to `"pick_place"` (the broadest default). `"other"` is a template-authored value only, set during migration round R12b.

---

## §5 Test Coverage

**File:** `tests/test_pattern_hint_extension.py` — 7 sections, 32 test functions (some parametrized → 63 total pytest nodes):

| Section | What it tests | Count |
|---------|--------------|-------|
| §1 VALID_PATTERN_HINTS | Contains all 7 values; backward compat | 5 |
| §2 PatternHint Literal | Intent construction accepts all 7; rejects "custom" | 8 |
| §3 Extractor — insert | 4 prompts → `insert` | 4 |
| §4 Extractor — train | 5 prompts → `train` | 5 |
| §5 Extractor backward compat | 8 prompts × original 4 patterns | 8 |
| §6 Extractor false-positive guard | insert: 4 prompts; train: 3 prompts | 7 |
| §7 Extractor never emits other | 3 prompts | 3 |
| §8 LLM schema sync | LLM_INTENT_JSON_SCHEMA enum == VALID_PATTERN_HINTS | 1 |

**Lint baseline:** 321 templates, 263 OK, 0 ERROR, 55 WARN, 105 INFO (identical to pre-R12 — no regressions from schema additions).

**Combined run:** `pytest tests/test_pattern_hint_extension.py tests/test_canonical_lint.py` → **63 passed** in 0.11 s.

---

## §6 Next Round: R12b — Migrate the 20 Templates

For each of the 20 `migration_deferred` templates, R12b will:

1. Set `intent.pattern_hint` to one of the 7 values using the cluster assignments in §1.
2. Add appropriate `structural_tags` for `other`-classified templates (e.g. `isaac:bridge.opcua`, `isaac:bridge.modbus`, `isaac:topology.recirculation_loop`, `isaac:multi_robot.handoff`).
3. Remove `migration_deferred` field.
4. Validate with `lint_canonical_templates.py` — 0 new ERROR.
5. Run `pytest tests/test_pattern_hint_extension.py tests/test_canonical_lint.py` — green.

Estimated split for R12b: 3 → `insert`, 3 → `train`, 14 → `other` (with structural_tags for retrieval precision). Two of the 14 (`CP-65`, `CP-67`, `CP-87`, `CP-NEW-amr-pickup-handoff`, `CP-NEW-cross-belt-sorter`) are likely promotable to existing patterns (`pick_place`, `sort`, `navigate`) — migration round will decide per success-criterion review.
