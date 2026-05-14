# Controller matrix — quickstart

**Audience**: first-time-user who wants to run a pick-place scenario
and see the controller matrix work on their machine.

**Prerequisites**: Isaac Sim 5.x running with Kit RPC on port 8001,
uvicorn running the isaac_assist_service on port 8000.

## 1) Probe what's available

```bash
python -c "
import asyncio
from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
import json
result = asyncio.run(DATA_HANDLERS['list_available_controllers']({}))
print('GPU:', result['env']['gpu'].get('arch_name'),
      'cc', result['env']['gpu'].get('compute_capability'),
      result['env']['gpu'].get('vram_gb'), 'GB')
print('Recommended for this machine:', result['recommended_for_hardware'])
for name, ctrl in result['controllers'].items():
    status = '✓' if ctrl['available'] else '✗'
    reason = ctrl.get('reason_if_not', '')
    print(f'  {status} {name:<15} {ctrl[\"summary\"][:60]}  {reason[:60]}')
"
```

Expected output on a machine with NVIDIA GPU + scipy + no cuRobo:
```
GPU: Blackwell cc 12.0 11.5 GB
Recommended for this machine: ['spline', 'native', 'sensor_gated']
  ✓ native          Canonical Isaac Sim franka.PickPlaceController + RmpFlow. Reactive. ...
  ✓ spline          Pre-planned 6-waypoint Cartesian trajectory with warm-start IK chaining ...
  ✗ curobo          GPU-accelerated global trajectory optimization with collision checking ...  curobo not found ...
  ✗ diffik          Stateless Jacobian-based differential IK (Isaac Lab). ...  isaaclab not importable ...
  ...
```

## 2) Run the baseline (native controller)

```bash
python -m scripts.qa.run_conveyor_pick_place --controller native --wait 120
```

**Expected:** 0-1 / 4 cubes in bin. This is the baseline any new
controller must beat.

## 3) Run the recommended controller (spline)

```bash
python -m scripts.qa.run_conveyor_pick_place --controller spline --wait 120
```

**Expected:** 3 / 4 cubes in bin, deterministic across back-to-back
runs. Verified winner in FAS 9 benchmark.

## 4) Run the auto-resolver

```bash
python -m scripts.qa.run_conveyor_pick_place --controller auto --wait 120
```

**Expected:** For Franka on CPU-only hardware: falls back to `native`
(Franka-detected rule). On a GPU machine with cuRobo + compatible
Warp: resolves to `curobo`. With `robot_path` NOT containing
"franka": falls back to `spline`.

## 5) Run the benchmark across all available controllers

```bash
python -m scripts.qa.benchmark_controllers \
    --controllers native,spline \
    --n-runs 3 \
    --wait 120 \
    --out /tmp/bench_$(date +%Y%m%d_%H%M).json
```

Serial execution (Kit RPC is single-tenant). ~7 min per controller.
Final JSON report names the winner.

## 6) Retrieve CP-01 template

The `workspace/templates/CP-01.json` template captures the verified
build sequence for this scenario. The chat endpoint's
`template_retriever` picks it up automatically via ChromaDB — when
a user says "set up a pick-place cell on a table", CP-01 is the
top hit. The agent reads `code` (agent-level tool-call sequence)
and executes it as-is.

## 7) Extend with a new controller

1. Add a `_gen_pick_place_<name>()` function in
   `service/isaac_assist_service/chat/tools/tool_executor.py`
2. Register in `_gen_setup_pick_place_controller` dispatcher
3. Add to the `target_source` enum in
   `service/isaac_assist_service/chat/tools/tool_schemas.py`
4. Add metadata in `_CONTROLLER_METADATA` + availability rule in
   `_handle_list_available_controllers`
5. Register a Scene Reset Manager hook with a unique name
6. Emit the `ctrl:*` attr contract (see `ctrl_attrs_schema.md`)
7. Run benchmark to validate delivery rate

## Troubleshooting

- **"curobo not found"** — cuRobo is in isaac_lab_env but Kit's Warp
  (1.8.2) is too old (`TypeError: func() got unexpected kwarg 'module'`).
  Fix: upgrade Kit's Warp to 1.9+ or use spline.
- **Benchmark results vary run-to-run** — all controllers should be
  deterministic. If native drops from 1/4 to 0/4, it's likely Scene
  Reset Manager hook interference from stale installs. Restart Kit
  between benchmark groups, or add Scene Reset Manager `unregister`
  calls before each install.
- **uvicorn serves stale generator code** — tool_executor is cached
  at startup. After edits to the file, `kill <uvicorn_pid>` and
  restart before testing via the chat endpoint. Direct Python
  imports always pick up fresh code.
