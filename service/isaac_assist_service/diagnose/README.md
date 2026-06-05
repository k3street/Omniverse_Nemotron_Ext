# diagnose — Pre-Flight Constraint Validator

Phase 1 module of the master execution plan. Spec:
[`docs/specs/2026-05-09-diagnose-scene-feasibility.md`](../../../docs/specs/2026-05-09-diagnose-scene-feasibility.md).

## What it does

`diagnose_scene_feasibility(args)` runs deterministic geometric checks on a
built scene to predict whether `simulate_traversal_check` would succeed.
Outputs a verdict in `{feasible, tightly_feasible, overconstrained, infeasible}`
plus per-axis violations and suggested alternatives.

Cuts agent feedback time from 60-180s sim to <2s analysis.

## File structure

| File | Purpose |
|---|---|
| `schema.py` | `Severity`/`Verdict`/`Violation`/`Alternative`/`FeasibilityReport` dataclasses + `THRESHOLDS` table + `classify_verdict()` |
| `messages.py` | Canonical Swedish + English templates for each (axis, severity) pair. **Never LLM-paraphrased** per spec §G. |
| `cache.py` | In-memory TTL cache (60s default) keyed by stable-hash of scene-graph features. `MUTATE_GEOMETRY_TOOLS` set drives invalidation. |
| `metrics.py` | Pure-Python scoring functions per axis. Take pre-resolved physics-query results as inputs (Kit-RPC-free, unit-testable). |
| `tool.py` | Async orchestrator handler. Calls `solve_ik` / `check_singularity` / `check_path_clearance` via `execute_tool_call`, feeds results into `metrics`, builds `FeasibilityReport`. Cache-aware. |

## Wiring

The handler is registered into `tool_executor.DATA_HANDLERS` via:
```python
from .diagnose.tool import register_diagnose_handlers
register_diagnose_handlers(DATA_HANDLERS)
```

(Wiring is deferred until Phase 0 baseline locks — Phase 1.1 in master plan.
Until then, the module is fully self-contained.)

MCP schema lives in `chat/tools/tool_schemas.py` (added 2026-05-09).
UI description override lives in `chat/tools/descriptions.py`.

## Usage

```python
result = await execute_tool_call("diagnose_scene_feasibility", {
    "robot_path": "/World/Franka",
    "pick_pose": [0.4, 0.0, 0.5],
    "drop_pose": [0.4, -0.3, 0.2],
    "obstacles": ["/World/Bin", "/World/Pillar"],
    "robot_base": [0, 0, 0],
    "max_reach": 0.855,    # Franka default
    "seed": 42,
    "lang": "sv",          # "sv" (default) or "en"
})

# result["verdict"] in {feasible, tightly_feasible, overconstrained, infeasible}
# result["violations"] = [{"axis", "severity", "value", "threshold", "message"}, ...]
# result["alternatives"] = [{"axis", "suggestion", "expected_value"?, "delta"?}, ...]
# result["metrics"] = {"pick_ik_feasible": bool, "drop_reach_utilization": float, ...}
# result["seed_used"] = 42
# result["cache_hit"] = False (or True on second call)
# result["elapsed_ms"] = 420
```

CLI smoke harness:
```bash
python scripts/qa/diagnose_one.py CP-37
python scripts/qa/diagnose_one.py CP-22 --lang en --no-cache
```

Suite-level baseline:
```bash
python scripts/qa/feasibility_baseline.py            # all 86 CPs
python scripts/qa/feasibility_baseline.py --update   # refresh frozen baselines
python scripts/qa/feasibility_baseline.py --canonicals CP-22,CP-37
```

## Verdict taxonomy

- **feasible** — all axes pass, expect simulate_traversal_check success
- **tightly_feasible** — at least one WARNING (manipulability low, reach 95%+); auto-tune candidate
- **overconstrained** — at least one ERROR (clearance < 60%, sensor-zone never sees cube); template author should reposition
- **infeasible** — at least one CRITICAL (no IK, drop inside obstacle bbox, robot starts in collision); scene must be rewritten

## Determinism

Same scene + same `seed` → byte-identical metrics dict on two consecutive
calls (modulo `elapsed_ms`). Verified by `tests/test_diagnose_tool.py::test_determinism_same_seed_byte_identical`.

## Tests

96 unit tests (l0, all green):
- `test_diagnose_schema.py` — 19 tests, classify_verdict transitions, dataclass serialization
- `test_diagnose_messages.py` — 11 tests, Swedish + English templates, missing-kwarg tolerance
- `test_diagnose_cache.py` — 13 tests, key stability, TTL, invalidation
- `test_diagnose_metrics.py` — 21 tests, all 7 axes at boundary thresholds
- `test_diagnose_tool.py` — 12 tests, end-to-end orchestrator with mocked `execute_tool_call`
- `test_auto_judge_feasibility.py` — 10 tests, scene_feasibility scoring axis (Opus §I)
- `test_phase2_triage.py` — 10 tests, triage classification

Run all:
```bash
pytest tests/test_diagnose_*.py tests/test_auto_judge_feasibility.py tests/test_phase2_triage.py
```

## Pending (Phase 1.x)

- **1.1** — `register_diagnose_handlers(DATA_HANDLERS)` in tool_executor.py (deferred until baseline lock)
- **1.3** — Run `feasibility_baseline.py` on all 86 canonicals; persist verdicts
- **1.5** — `--feasibility` flag for `verify_pickplace_pipeline` per Opus §F
- **1.6 (future)** — `diagnose_layout_spec(layout_spec)` entrypoint per Opus §A; multi-robot `cycles` arg per §E

## References

- Spec: `docs/specs/2026-05-09-diagnose-scene-feasibility.md` (with Opus review §A-K)
- Master plan Phase 1: `docs/specs/2026-05-09-master-execution-plan.md`
- Industrial-expansion (downstream consumer): `docs/specs/2026-05-09-industrial-expansion-spec.md`
- Phase 2 triage: `scripts/qa/phase2_triage.py`
- Schema shape: Constraint / Violation / severity / verdict — self-contained reimplementation
