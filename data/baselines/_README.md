# Baselines — frozen `BaselineSnapshot` JSONs

This directory holds per-CP baseline snapshots produced by
`service/isaac_assist_service/qa/baseline.py::freeze_baseline`. Each
file is named `{scenario_id}.json` (e.g. `CP-37.json`) and is
deserialised by `compare_to_baseline` when a fresh regression run
needs to be diffed against the frozen point.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8d. These snapshots are
the load-bearing artefact every later "harness honesty" claim cites
— do not edit them by hand; re-freeze with `freeze_baseline(...)`.

## Schema

A `BaselineSnapshot` is the Pydantic model below. JSON keys match
the model field names exactly:

| Field | Type | Notes |
|-------|------|-------|
| `scenario_id` | `str` | Stable CP identifier, matches the file stem |
| `frozen_at` | ISO 8601 datetime (UTC) | When the freeze happened |
| `n_runs` | `int >= 0` | Number of runs in the baseline |
| `status` | `int` (BaselineStatus) | `-1=stable_fail`, `0=flaky`, `1=stable_ok` |
| `per_seed_results` | `list[RunResult]` | One entry per executed run |
| `settle_state_hash` | `str` or `null` | Optional post-run state fingerprint |

A `RunResult` is:

| Field | Type | Notes |
|-------|------|-------|
| `seed` | `int` | The seed the runner saw |
| `passed` | `bool` | Whether the run passed |
| `elapsed_s` | `float >= 0` | Wall-clock seconds the runner took |
| `error` | `str` or `null` | `repr(exc)` if the runner raised, else `null` |

## Status taxonomy

`BaselineStatus` is an `IntEnum` ordered by severity (lower = worse):

* `stable_fail (-1)` — every run failed; root cause is documented in
  the gap log. Canonical-instantiator must fall through to the LLM
  tool-loop when it sees this.
* `flaky (0)` — some runs passed, some failed (or fewer than the
  N-of-M threshold of consecutive passes was observed).
* `stable_ok (1)` — every run in the snapshot passed and the count
  cleared the N-of-M threshold (default 3).

## Example — synthetic `CP-37.json`

```json
{
  "scenario_id": "CP-37",
  "frozen_at": "2026-05-12T08:42:11.317420+00:00",
  "n_runs": 5,
  "status": 1,
  "per_seed_results": [
    {
      "seed": 0,
      "passed": true,
      "elapsed_s": 4.812,
      "error": null
    },
    {
      "seed": 1,
      "passed": true,
      "elapsed_s": 4.901,
      "error": null
    },
    {
      "seed": 2,
      "passed": true,
      "elapsed_s": 4.770,
      "error": null
    },
    {
      "seed": 3,
      "passed": true,
      "elapsed_s": 4.853,
      "error": null
    },
    {
      "seed": 4,
      "passed": true,
      "elapsed_s": 4.798,
      "error": null
    }
  ],
  "settle_state_hash": null
}
```

This synthetic CP-37 snapshot is `stable_ok` (`status: 1`): five
runs at seeds 0..4, all passed, no errors. A later regression run
that produces a fail at any of these seeds would surface in
`BaselineDelta.mismatching_seeds`; a status drop (e.g. CP-37 going
to `flaky`) sets `regressed: true` and the canonical-instantiator
gate refuses the hard-instantiate path.

## Freeze / compare from code

```python
from service.isaac_assist_service.qa.baseline import (
    freeze_baseline,
    compare_to_baseline,
)
from service.isaac_assist_service.qa.regression import run_with_seed_set

# Freeze (writes data/baselines/CP-37.json):
snap = freeze_baseline("CP-37", n_runs=5, runner=my_runner)

# Later, diff a fresh run:
fresh = run_with_seed_set("CP-37", seeds=list(range(5)), runner=my_runner)
delta = compare_to_baseline("CP-37", fresh.runs)
if delta.regressed:
    raise RuntimeError(delta.message)
```
