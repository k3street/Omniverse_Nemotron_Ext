# Controller Matrix — Implementation Plan

**Goal**: Expand `setup_pick_place_controller` with a complete matrix of
motion controllers so users can pick the right one for their hardware
and scenario, and so the Isaac Assist agent can select automatically
based on spec + environment.

**Scope**: 4 new `target_source` values (`spline`, `curobo`, `diffik`,
`osc`), plus `auto` with prerequisite-based fallback chain. New tool
`list_available_controllers` for agent-side selection. Integration
with existing ROS2/GR00T tools (no duplication — `ros2_cmd`
target_source already wraps ROS2, GR00T lives as its own tool family).

**Total estimate**: ~17h + 15% buffer = **~20h**

---

## Controller matrix (final state)

| target_source | Hardware | Setup | Motion quality | Cycle | Collision-aware | Use for |
|---|---|---|---|---|---|---|
| `native` *(existing)* | CPU | 0h | Reactive (RmpFlow) | 8-15s | Partial | Dynamic targets, moving cubes on belt |
| `sensor_gated` *(existing)* | CPU | 0h | Reactive + teach-replay | 10-15s | No | Industrial sim2real, PLC-mimic |
| `fixed_poses` *(existing)* | CPU | 0h | Pose-replay | varies | No | Cycle-time demos, validation |
| `ros2_cmd` *(existing)* | External | 0h | Depends on external | varies | Depends | Digital twin, PLC-in-loop, hybrid |
| `cube_tracking` *(existing)* | CPU | 0h | Reactive + omniscient | 8-12s | No | ML research demo-gen (NOT sim2real) |
| **`spline` (NEW)** | CPU | 2.5h | Deterministic, smooth | 8-12s | No (pre-checked) | Repetitive identical cycles, CPU-only, sim2real demos |
| **`curobo` (NEW)** | NVIDIA GPU ≥Volta, 4GB VRAM | 4h | Globally optimized, industrial | 3-5s | Yes (Cuboid, SDF, mesh) | Obstacle-rich scenes, precision, production-quality |
| **`diffik` (NEW)** | CPU, Isaac Lab | 2h | Stateless Jacobian, jittery | 12-18s | No | Teleop, Cartesian RL obs, simple free-motion |
| **`osc` (NEW)** | CPU, Isaac Lab | 2h | Task-space impedance (torque) | 20-30s | No | Contact-rich tasks (polish, assembly). **Experimental.** |
| **`auto` (NEW)** | any | 0h | Best-available | varies | varies | Default when hardware unknown or agent selects dynamically |

**Separate tool families (not target_source — already implemented):**
- `load_groot_policy` / `evaluate_groot` / `finetune_groot` — learned-policy direct joint output
- `ros2_connect` / `configure_ros2_bridge` / `setup_ros2_bridge` — ROS2 bridge primitives
- `start_teleop_session` + related — teleoperation

---

## Testprincip: gate per fas

**Varje fas avslutas INTE innan dess tester är skrivna OCH passerar.**
Inga tester sparas till slutet. Det betyder varje FAS innehåller:

1. Kod-arbete
2. Tester (unit + integration + e2e beroende på fas)
3. Regress-körning av ALLA tidigare fasers tester
4. Fail → tillbaka till steg 1

`tests/controllers/` mappen byggs upp inkrementellt. Testfilerna
listade i FAS-beskrivningarna nedan är de som LÄGGS TILL i varje fas.

---

## FAS 0: Inventering (0.5h) — ingen kod

**Deliverable**: `docs/qa/controller_matrix.md` — operator cheat sheet.

- Dokumentera befintliga ROS2/GR00T/teleop-tool-familjer och hur de
  relaterar till target_source-matrisen (kompletterar, inte duplicerar)
- Snapshot av nuvarande `run_conveyor_pick_place.py` som baseline (3/4)

**Tester**: inga (doc-only fas).

**Exit**: cheat sheet skriven, baseline 3/4 bekräftad.

## FAS 1: Refaktorering (0.75h)

**Deliverable**: no-behavior-change refactor, ready for new generators.

- Extrahera `_PP_SCENE_RESET_MGR_SNIPPET` från `_gen_pick_place_native`
  (rad 26686-26773) till module-level-string i `tool_executor.py`
- Extrahera `_PP_OBSERVABILITY_SNIPPET` (ctrl:* attrs + _record_err)
- Regresstest: `python -m scripts.qa.run_conveyor_pick_place
  --controller native --wait 60` → fortfarande 3/4 i bin

## FAS 2: CPU-fallback — spline (2.5h)

**Deliverable**: `_gen_pick_place_spline` + tool_schemas entry.

- `LulaKinematicsSolver` för 6 waypoints per cube: approach (0.2m
  över), pick (cube xyz), lift (0.2m över), transit-mid, drop (bin
  +0.05m), retreat
- Warm-start-chaining: varje waypoints IK använder förra waypointens
  joint-config som `warm_start` → konsekvent redundancy-branch →
  löser "folded arm"/wrist-snap
- `scipy.interpolate.CubicSpline` interpolerar joint-config per tick
  (fallback till `np.linspace` om scipy saknas i Kit-python)
- Sensor-gated state machine: wait_sensor → planning → executing →
  gripping → transit → releasing → returning
- Scene Reset Manager hook registrerad som `"spline_pp"`
- **Regresstest**: `--controller spline` → **mål 4/4 i bin**, CPU-only

## FAS 3: Benchmark-infrastruktur (1.5h)

**Deliverable**: objektiv controller-jämförelse möjlig.

- `run_conveyor_pick_place.py`:
  - `--controller <name>` flagga
  - `--metrics` extended snapshot med: `cubes_in_bin`, `cycle_time_avg`,
    `cycle_time_std`, `joint_range_used`, `error_count`,
    `motion_smoothness` (mean |jerk| EE-bana)
  - Auto-start `motion_observer` för metrisberäkning
- `scripts/qa/benchmark_controllers.py` — loop alla tillgängliga
  controllers, N runs var, JSON-rapport

## FAS 4: Tool-metadata för agent-val (1.25h) — **kritiskt**

**Deliverable**: agenten kan välja rätt controller från spec.

- `_handle_list_available_controllers(args)` i tool_executor — probar
  live Kit-env, returnerar per target_source:
  ```json
  {
    "native": {"available": true, "hardware_req": "CPU",
               "cycle_time_class": "medium", "collision_aware": false,
               "motion_quality": 2, "use_case_fit": ["dynamic_targets",
               "belt_picking"]},
    "curobo": {"available": false, "reason_if_not": "CUDA not found",
               "requirements": ["nvidia-curobo pip package",
               "torch.cuda.is_available()", "VRAM >=4GB"], ...},
    ...
  }
  ```
- Tool-schema-descriptions RIKT utökade per target_source — varje
  option får "Use for: ...", "Avoid: ...", "Cycle: ...",
  "Hardware: ..." så LLM kan matcha scenario från användarens spec
- Registera `DATA_HANDLERS["list_available_controllers"]`
- Schema-entry i `tool_schemas.py` utan params (pure-probe)

## FAS 5: Auto-fallback-kedja (0.75h)

**Deliverable**: `target_source="auto"` fungerar, agenten kan sätta
det för maximal portabilitet.

- `_resolve_auto_target_source(args)` → probar i ordning:
  curobo (GPU+curobo) → native (Franka+Isaac Sim) → diffik (Isaac Lab)
  → spline (scipy eller linear fallback)
- Första success vinner; resolved target + reason embedded i
  JSON-status

## FAS 6: GPU-controller — curobo (4h) — **högsta payoff**

**Deliverable**: industrial-quality motion när GPU finns.

- **Env-bridge decision** (1.5h av de 4): cuRobo bor i
  `isaac_lab_env`, Kit python är Isaac Sims egen. Tre vägar:
  1. `sys.path.insert(0, '<isaac_lab_env site-packages>')` — ABI-känsligt
     men snabbast
  2. Subprocess-sidecar — säker, ~100ms overhead per plan
  3. Persistent HTTP-sidecar — mest isolerad, mest arbete
  - **Prioritet**: (1) först, fallback (2) vid ABI-kollision
- `_gen_pick_place_curobo`:
  - `MotionPlannerCfg.create(robot="franka.yml", scene_model=...)`
  - `planner.warmup(enable_graph=True)` — cache:ad i `builtins._curobo_pp_planner`
  - Per cube: `plan_grasp(grasp_poses, grasp_approach_offset=0.1,
    grasp_lift_offset=0.1, ...)` → 3 segment + `plan_pose(drop)` +
    `plan_pose(home)` → 5 trajektorier totalt
  - Exekvering: per tick, sampla nästa waypoint från aktuellt
    segment → `apply_action(joint_positions)`
- `planning_obstacles` param: USD-prim-paths → auto-konverterade till
  `Cuboid` via `ComputeWorldBound` → skickade till scene-collision-checker
- Scene Reset Manager: clear trajectory list, inte planner (behåll
  warmup-cache)
- **Regresstest**: `--controller curobo` → **mål 4/4 i bin, cycle <5s**

## FAS 7: Isaac Lab-controllers (4h)

### 7a: diffik (2h)
- `from isaaclab.controllers import DifferentialIKController,
  DifferentialIKControllerCfg`
- Jacobian-probe: `franka._articulation_view.get_jacobians()` → slice
  EE-body-row → torch-tensor
- Cartesian waypoints = samma 6 som spline, feed som `pose`-target
- Per-tick: `controller.compute(ee_pos, ee_quat, jacobian, joint_pos)`
  → joint_positions → apply_action
- **Flaggad "no planning — waypoint pre-flight required"**
- **Regresstest**: `--controller diffik` → mål 3/4 (den har inga
  obstacles-hantering, C3 kan missa bin-väggen)

### 7b: osc (2h, experimentellt)
- `from isaaclab.controllers import OperationalSpaceController,
  OperationalSpaceControllerCfg`
- Arm DOFs switchade till effort-mode vid install
- Input: full mass matrix + gravity från articulation view
- Output: joint-torques → `apply_action(joint_efforts=...)`
- Markerat experimental i JSON-status
- **Regresstest**: minimum "no crash", 2/4 acceptabelt för
  experimental

## FAS 8: Schema + tool-registration (0.5h)

- `tool_schemas.py`:
  - `target_source` enum utökad
  - Beskrivning utökad med "auto (recommended when hardware unknown)"
  - Nya params: `curobo_world_yml`, `planning_obstacles`,
    `spline_waypoint_dt`, `diffik_method`
  - Nytt tool-entry `list_available_controllers`
- `tool_executor.py`:
  `DATA_HANDLERS["list_available_controllers"] = _handle_list_available_controllers`
- **Restart uvicorn** (tool_executor cached)

## FAS 9: Benchmark + välj vinnare (1.5h)

```bash
python -m scripts.qa.benchmark_controllers --n-runs 3 \
    --out /tmp/bench_$(date +%Y%m%d).json
```

- Kör 3 runs per controller (native, spline, curobo, diffik, osc)
- Aggregerar mean ± std per metric
- Printar jämförelse-tabell
- **Vinnare-kriterium** (prioritet ordning):
  1. `cubes_in_bin / 4` (>=4/4 är pass)
  2. `motion_smoothness` (lägre är bättre)
  3. `cycle_time_avg` (lägre är bättre)
  4. `error_count` (lägre är bättre)
- Per hardware-klass: GPU+curobo → curobo, else → spline eller native

## FAS 10: Template freeze (1h)

**Deliverable**: `workspace/templates/CP-01.json`, auto-indexerad i ChromaDB.

```json
{
  "task_id": "CP-01",
  "goal": "Conveyor pick-place: Franka on table at z=0.75 with +90°Z, 4 cubes on belt, sensor, bin. Deliver 4/4 cubes.",
  "tools_used": ["create_prim", "robot_wizard", "create_conveyor",
                 "create_bin", "add_proximity_sensor",
                 "setup_pick_place_controller"],
  "thoughts": "Verified 2026-04-2X. Winner: <X>. Benchmarked 5 controllers...",
  "code": "...",
  "failure_modes": [...],
  "verified_date": "...",
  "verified_metrics": {...}
}
```

- Uppdatera `docs/qa/controller_matrix.md` med final tabell
- Säkerställ template_retriever indexerar (auto vid nästa miss)

---

## Risk-log (sammanfattad per fas)

| Fas | Risk | Severity | Mitigation |
|---|---|---|---|
| 2 | scipy saknas i Kit Python | medium | Linear interpolation fallback |
| 2 | IK fail vid waypoint | medium | Fail-fast per waypoint, surface error |
| 6 | torch ABI mismatch cuRobo vs Kit | **high** | Subprocess-sidecar fallback |
| 6 | cuRobo redundancy-constraint failure | medium | current_state=previous segment end, cspace_seed chained |
| 6 | path-pollution (torch shadow) | medium | Insert path precis innan import, ta bort efter |
| 7a | Jacobian API skillnad 5.0 vs 5.1 | medium | Probe båda `get_jacobians()` + `get_jacobian_matrix()` |
| 7a | diffik singularities | medium | method='dls' med lambda=0.05, kick joint6 om stalled |
| 7b | Mass matrix exposure varierande | **high** | Preflight-check, refuse install om saknas |
| 7b | Effort-mode persist efter OSC | medium | Uninstall-hook switchar tillbaka till position |
| 5/8 | Agent väljer fel target_source | medium | Rika schema-descriptions + `list_available_controllers` preflight |

---

## Test matrix (FAS 9)

Per controller × 3 runs = 15 data points. Per run registrera:
- `cubes_in_bin` (0-4)
- `cycle_time` per delivered cube (s)
- `joint_range_used` per arm joint (rad)
- `error_count` från ctrl:*
- `motion_smoothness` (mean |jerk| EE från motion_observer)

Output: `/tmp/bench_YYYYMMDD.json`:
```json
{
  "runs": [...],
  "per_controller_aggregates": {
    "native":   {"cubes_mean": 3.33, "cycle_mean": 12.1, ...},
    "spline":   {"cubes_mean": 4.00, "cycle_mean": 9.5, ...},
    "curobo":   {"cubes_mean": 4.00, "cycle_mean": 4.2, ...},
    ...
  },
  "winner_by_hardware": {
    "gpu_available": "curobo",
    "cpu_only": "spline"
  }
}
```

---

## Progression-condition per fas

- **FAS 1 klar** när native-baseline fortfarande 3/4 efter refactor
- **FAS 2 klar** när spline levererar ≥4/4 i CPU-only benchmark
- **FAS 3 klar** när `benchmark_controllers.py` kör två olika
  controllers och printar jämförbar tabell
- **FAS 4 klar** när `list_available_controllers` returnerar rätt
  JSON för denna hårdvara (GPU present)
- **FAS 5 klar** när `target_source="auto"` väljer rätt automatiskt
- **FAS 6 klar** när curobo levererar ≥4/4 med cycle <5s
- **FAS 7a klar** när diffik installerar utan crash + levererar ≥2/4
- **FAS 7b klar** när osc installerar utan crash
- **FAS 8 klar** när uvicorn restart + `list_available_controllers`
  via chat-endpoint returnerar korrekt JSON
- **FAS 9 klar** när benchmark-JSON har 5 controllers × 3 runs
- **FAS 10 klar** när CP-01.json sparad och template_retriever
  hittar den via semantic query

## FAS 11: Testgenerering (2.5h) — löper parallellt med FAS 2-7

Tester genereras INKREMENTELLT: varje FAS 2+ avslutas INTE innan dess
tester är skrivna och passerar. Ingen big-bang-testkör i slutet.

**Test-filer som genereras:**

### 11a. Generator-nivå (compile-check) — `tests/controllers/test_generators.py`
```python
import pytest
from service.isaac_assist_service.chat.tools.tool_executor import (
    _gen_setup_pick_place_controller,
)

@pytest.mark.parametrize("mode", [
    "native", "sensor_gated", "fixed_poses", "cube_tracking",
    "ros2_cmd", "spline", "curobo", "diffik", "osc", "auto",
])
def test_generator_compiles(mode):
    """Each generator emits syntactically valid Python."""
    code = _gen_setup_pick_place_controller({
        "robot_path": "/World/Franka", "target_source": mode,
        "sensor_path": "/World/PickSensor",
        "belt_path": "/World/ConveyorBelt",
        "source_paths": [f"/World/Cube_{i}" for i in (1,2,3,4)],
        "destination_path": "/World/Bin",
    })
    compile(code, f"<{mode}>", "exec")

def test_generator_contains_scene_reset_registration():
    """Every generator should register with Scene Reset Manager."""
    for mode in ["native", "spline", "curobo", "diffik", "osc"]:
        code = _gen_setup_pick_place_controller({..., "target_source": mode})
        assert "_scene_reset_manager" in code
        assert f".register(" in code

def test_list_available_controllers_handler_exists():
    from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
    assert "list_available_controllers" in DATA_HANDLERS
```

### 11b. Schema-validering — `tests/controllers/test_schemas.py`
```python
import json
from service.isaac_assist_service.chat.tools.tool_schemas import TOOL_SCHEMAS

def test_target_source_enum_complete():
    spec = next(t for t in TOOL_SCHEMAS
                if t["function"]["name"] == "setup_pick_place_controller")
    enum = spec["function"]["parameters"]["properties"]["target_source"]["enum"]
    for mode in ["native", "sensor_gated", "spline", "curobo",
                 "diffik", "osc", "auto"]:
        assert mode in enum, f"{mode} missing from enum"

def test_list_available_controllers_schema_registered():
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert "list_available_controllers" in names
```

### 11c. Integration — `tests/controllers/test_install_each.py`
```python
# Requires Kit + uvicorn running. Skipped if not reachable.
import pytest, httpx

KIT_RPC = "http://127.0.0.1:8001/exec_sync"

def _kit_available():
    try: httpx.get(KIT_RPC.replace("/exec_sync", "/"), timeout=2)
    except Exception: return False
    return True

pytestmark = pytest.mark.skipif(not _kit_available(),
                                reason="Kit RPC not running")

@pytest.mark.parametrize("mode", ["native", "spline", "diffik"])
def test_install_on_clean_scene(mode):
    """Install + verify no exceptions, ctrl:phase attr set."""
    from scripts.qa.run_conveyor_pick_place import (
        kit, PATCH_RESET, PATCH_SCENE, PATCH_FRANKA, patch_controller
    )
    kit(PATCH_RESET); kit(PATCH_SCENE); kit(PATCH_FRANKA)
    r = kit(patch_controller(mode=mode), timeout=60)
    assert r.get("success"), r.get("output")

def test_list_available_controllers_returns_json():
    # Call the handler directly
    from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS
    result = asyncio.run(DATA_HANDLERS["list_available_controllers"]({}))
    assert "native" in result
    assert "available" in result["native"]
```

### 11d. End-to-end delivery — `tests/controllers/test_e2e_delivery.py`
```python
# SLOW — each test runs ~60s. Mark with @pytest.mark.slow
@pytest.mark.slow
@pytest.mark.parametrize("mode,min_cubes", [
    ("native", 3), ("spline", 4), ("curobo", 4),
    ("diffik", 2),  # no collision awareness
])
def test_delivers_cubes_to_bin(mode, min_cubes):
    """Run full pipeline, assert minimum cubes delivered."""
    from scripts.qa.run_conveyor_pick_place import run_full_test
    result = run_full_test(controller=mode, wait=120)
    assert result["cubes_in_bin"] >= min_cubes, \
        f"{mode}: expected ≥{min_cubes}, got {result['cubes_in_bin']}"
```

### 11e. Stop+Play consistency — `tests/controllers/test_stop_play.py`
```python
@pytest.mark.slow
@pytest.mark.parametrize("mode", ["native", "spline", "curobo"])
def test_stop_play_resets_cleanly(mode):
    """First run delivers N cubes. Stop + Play. Second run delivers same N."""
    from scripts.qa.run_conveyor_pick_place import run_full_test, kit
    first = run_full_test(controller=mode, wait=90)
    # Stop + Play programmatically
    kit("""
import omni.timeline as tl
ti = tl.get_timeline_interface()
ti.stop()
import omni.kit.app, time
for _ in range(10): omni.kit.app.get_app().update()
time.sleep(0.5)
ti.play()
""")
    second_wait_snapshot = ... # wait 90s, snapshot
    assert second["cubes_in_bin"] >= first["cubes_in_bin"] - 1, \
        "Stop+Play regression"

@pytest.mark.slow
def test_multiple_stop_play_cycles():
    """3 stop+play cycles should all complete equivalently."""
    ...
```

### 11f. Benchmark smoke — `tests/controllers/test_benchmark.py`
```python
@pytest.mark.slow
def test_benchmark_script_runs_and_produces_json():
    import subprocess, json, tempfile
    out = tempfile.mktemp(suffix=".json")
    r = subprocess.run([
        "python", "-m", "scripts.qa.benchmark_controllers",
        "--n-runs", "1", "--controllers", "native,spline", "--out", out
    ], capture_output=True, timeout=300)
    assert r.returncode == 0
    data = json.loads(open(out).read())
    assert "per_controller_aggregates" in data
    assert "native" in data["per_controller_aggregates"]
```

### 11g. Template retrieval — `tests/templates/test_cp01.py`
```python
def test_cp01_template_exists():
    import json
    with open("workspace/templates/CP-01.json") as f:
        t = json.load(f)
    assert t["task_id"] == "CP-01"
    assert t["verified_metrics"]["cubes_delivered"] in ["4/4", "3/4"]

def test_cp01_retrievable_by_goal():
    """Semantic query should find CP-01 as top match."""
    from service.isaac_assist_service.chat.tools.template_retriever import (
        retrieve_templates
    )
    goals = [
        "set up conveyor pick-and-place",
        "franka picks cubes from a belt",
        "pick and place with conveyor and bin",
    ]
    for goal in goals:
        hits = retrieve_templates(goal, top_k=3)
        assert any(h["task_id"] == "CP-01" for h in hits), \
            f"CP-01 not in top-3 for goal: {goal}"
```

### 11h. Observability consistency — `tests/controllers/test_observability.py`
```python
def test_all_controllers_emit_ctrl_attrs():
    """After install, every controller should set ctrl:phase etc."""
    required_attrs = ["ctrl:phase", "ctrl:cubes_delivered",
                      "ctrl:error_count", "ctrl:tick_count",
                      "ctrl:picked_path"]
    for mode in ["native", "spline", "curobo"]:
        install_and_wait(mode, 10)
        for attr in required_attrs:
            v = kit_get_attr("/World/Franka", attr)
            assert v is not None, f"{mode}: {attr} missing"
```

**Ordning**:
- FAS 2 klar → kör 11a + 11b + 11c + 11d för spline (6 tester)
- FAS 4 klar → 11c + 11h för list_available_controllers
- FAS 5 klar → 11d för auto
- FAS 6 klar → alla tester för curobo
- FAS 7 klar → alla tester för diffik + osc (osc med
  min_cubes=1, märkt experimental)
- FAS 10 klar → 11g för CP-01

**CI-integration** (sen-fas): lägg till pytest-markers i
`pytest.ini` + dokumentera i `docs/qa/testing.md` hur `slow` tester
kan köras separat från snabba.

---

## Plan-review — självkritik (ultrathink-pass)

Efter genomläsning av hela planen hittar jag följande gaps och svagheter:

### Strukturella gaps

**G1. cuRobo 4h är för optimistiskt.** Env-bridge (isaac_lab_env
site-packages i Kit python) har mängder av failure-modes: torch-ABI,
numpy-downgrade, CUDA-driver-kompatibilitet, PYTHONPATH-pollution. Plan
antar option (a) `sys.path.insert` fungerar. Historik visar det gjort
numpy-conflict en gång redan. Realistiskt **5-6h med option (b) subprocess
sidecar som verklig fallback**. Dela FAS 6 i:
- 6a env-bridge + verify (1.5h)
- 6b MotionPlanner single-segment plan (1.5h)
- 6c multi-segment chaining (pick→lift→transit→drop) (2h)
- 6d obstacles + warmup-cache (1h)

**G2. "Motion quality" är fuzzy.** Success-criteria använder "4/4 cubes"
men inte motion-quality. Måste definiera mätbart:
- `max_joint_velocity` (rad/s) per trajektoria — lägre = jämnare
- `wrist_sign_changes` — antal tecken-ändringar i joint7 velocity
  under transit (0 = jämn, 5+ = snap-loop)
- `trajectory_arclength_ratio` — faktisk EE-bana / rak-linje-avstånd
- `max_jerk` — tredje-derivata EE-position (m/s³)
motion_observer samlar redan positioner/velocities, lägg till beräkning
i benchmark-aggregeringen.

**G3. Ingen regress-test på native efter curobo-install.**
Installation av cuRobo downgraderade numpy och websockets —
Isaac Sim kan gå sönder. Lägg till i FAS 6: "efter install, kör
existing test_e2e_delivery för native — måste fortfarande leverera 3/4".

**G4. Scene Reset Manager testad med ETT controller-hook, inte flera
samtidigt.** Om en scen har två controllers aktiva (t.ex. pick-place
på två robotar) - hur beter sig manager? Lägg test:
`test_multiple_controllers_reset_together` i FAS 4 eller 5.

**G5. Rollback-plan per fas saknas.** Om en fas bryter något tidigare:
hur revert? Nämn per fas: "före FAS X, tag git-snapshot av tool_executor
till /tmp/tool_executor_pre_FASX.py.bak". Git commits efter varje
passerad fas.

**G6. Concurrent Kit RPC-constraint inte dokumenterad.** Memory:
"Kit RPC is single-tenant — no concurrent direct_eval". Benchmark-scriptet
måste SERIALISERA körningar. Ska vara tydlig i FAS 3.

**G7. Motion observer-overhead kan skeva benchmark.** JSONL-skrivning
60Hz kan ha mätbar overhead på motion smoothness. FAS 3 bör inkludera
baseline-run UTAN observer för att kvantifiera overhead.

### Agent-selektions-gaps

**G8. Hur mappar LLM hårdvaru-nämnande till controller?** Prompt
"jag har en GTX 1080" — agent måste veta GTX 1080 = Pascal = för gammal
för cuRobo. `list_available_controllers` bör returnera `gpu_arch`
(Pascal/Volta/Turing/Ampere/Ada/Hopper) så agent kan matcha. Använd
`torch.cuda.get_device_capability()` → compute capability (Volta = 7.x,
Turing = 7.5, Ampere = 8.x, etc.) — cuRobo kräver ≥7.0.

**G9. Schema-descriptions rika men ostestade.** Behöver prompt-test:
skicka 5 olika hårdvaru-prompts till agenten, kontrollera att rätt
controller väljs. Lägg till i FAS 9:
- Prompt: "CPU-only, no GPU" → förväntar spline
- Prompt: "RTX 4090, industrial quality" → förväntar curobo
- Prompt: "GTX 1080 + pick-place" → förväntar spline (inte curobo)
- Prompt: "Teleop demo" → förväntar diffik
- Prompt: "Contact-rich polishing" → förväntar osc

### Tekniska gaps

**G10. ctrl:* attrs är ett kontrakt.** Dokumentera i
`docs/qa/ctrl_attrs_schema.md` vilka attrs varje controller emit:ar
och vad de betyder. Consumers (diagnose_scene, auto_judge,
benchmark_controllers) är beroende av detta. Lägg till i FAS 1 (delvis
refaktoreringens mening).

**G11. Template CP-01 code-field.** JSON-schema har `"code": "..."` men
det är oklart vad det ska innehålla — full Python-script? Tool-call-
sekvens som JSON? Efter retrieval, agenten ser `code`-fältet — det
MÅSTE vara körbart. Precisera i FAS 10: `code` = den exakta sekvensen
av tool-calls som gör builden, NOT exec_sync-patchen.

**G12. Benchmark-variance.** 3 runs per controller räcker inte för
att urskilja small differences. Föreslå ≥5 runs, med t-test för
signifikans. Lägg till i FAS 9.

**G13. Ingen explicit "first-time-user"-walkthrough.** När planen är
klar, hur testar en ny användare att allt funkar? Skriv:
`docs/qa/controller_matrix_quickstart.md` med 3-kommando flow
(install, run native baseline, run auto-selection). Lägg till i
FAS 10.

### Risk-nivåer (uppdaterade)

| Risk | Prior | Nu |
|---|---|---|
| cuRobo ABI-bridge | high | **critical** — bumpa buffer till 6h |
| Isaac Sim regress efter curobo install | — | high (nyupptäckt) — lägg regress-test |
| Scene Reset Manager multi-controller | — | medium (nyupptäckt) — lägg test |
| Motion quality subjektivt | — | medium (nyupptäckt) — definiera metriker |

### Tidsbudget-uppdatering

| Fas | Tidigare | Nu | Delta |
|---|---|---|---|
| 0 | 0.5 | 0.5 | — |
| 1 | 0.75 | 1.0 | +0.25 (inkl ctrl:* schema-doc) |
| 2 | 2.5 | 2.5 | — |
| 3 | 1.5 | 2.0 | +0.5 (inkl overhead-baseline) |
| 4 | 1.25 | 1.5 | +0.25 (inkl gpu_arch i list) |
| 5 | 0.75 | 0.75 | — |
| 6 | 4.0 | 6.0 | +2.0 (split 6a-d + regress) |
| 7 | 4.0 | 4.0 | — |
| 8 | 0.5 | 0.5 | — |
| 9 | 1.5 | 2.5 | +1.0 (5 runs + prompt-test + t-test) |
| 10 | 1.0 | 1.5 | +0.5 (quickstart doc + code-field spec) |
| **Totalt** | **17.25** | **22.75** | **+5.5h** |

Plus 15% buffer = **~26h**. Realistiskt 3-4 dagar av fokuserat
arbete.

### Prioritering vid tidsbrist

Om vi bara har 12h: fokusera på FAS 0-5 + FAS 2 + 9 minimum.
Lämna curobo + osc för v2. Verifierad template (CP-01) med spline
som winner ger redan 4/4 delivery — viktigare än GPU-optimering.

### Verdict

Planen är **bra men under-budgeterad**. Huvudjustering: splitta FAS 6
till 6a-d, boosta test-faser, dokumentera ctrl:* schema i FAS 1,
lägg till agent-prompt-test i FAS 9. Ny total ~26h.

Rekommendation: kör FAS 0-5 först (fundament + CPU-fallback) — då har
vi redan 4/4 delivery via spline och en körbar template. cuRobo blir
bonus-optimering för GPU-användare.

---

## Exit criteria (hela planen)

- [ ] Fyra nya controllers implementerade + testade
- [ ] Auto-fallback fungerar på både GPU-maskin och CPU-only
- [ ] Benchmark visar mätbar förbättring (cuRobo ≥2x snabbare än
  native, spline 4/4 cubes)
- [ ] Template CP-01.json frozen och retrievable
- [ ] Agent (via chat-endpoint) kan välja rätt controller utan
  manuell styrning — testa med 3 olika prompts:
  - "build pick-place, I have no GPU" → väljer spline eller native
  - "build pick-place on my RTX 4090" → väljer curobo
  - "build pick-place, don't care what" → väljer auto → curobo
