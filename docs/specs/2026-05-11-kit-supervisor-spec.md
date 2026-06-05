# Kit Supervisor — Implementation Spec v2

**Date:** 2026-05-11
**Status:** v2 — implementation-ready; supersedes 2026-05-11 v1 first-draft
**Owner:** TBD
**Estimated LOC:** ~900-1200 (supervisor + Kit-side endpoint + tests)

---

## 0. Reading Guide

§1-3: Why (problem + goal + architecture)
§4: Component specifications (read for implementation)
§5: Kit-side hooks (the only Kit-extension change)
§6: Configuration surface
§7: Integration with multi_run_regression
§8: State machine + failure modes
§9: Telemetry event schemas
§10: Test plan with coverage targets
§11: Performance SLAs
§12: Roll-out + backwards-compat
§13: Open questions

---

## 1. Problem statement

Kit / Isaac Sim state drifts after ~25-30 sequential canonical-template
executions. Symptoms (observed 2026-05-11):

| Symptom | Empirical signature |
|---|---|
| PhysX explosion | `cube_final` x/y/z magnitudes ∈ [10⁵, 10⁹] m |
| Speed runaway | `cube_speed` > 10⁵ m/s |
| Slow stage load | `elapsed_s` > 2x p50 baseline |
| cuRobo cache stale | plan failures on previously-working scenes |
| Memory growth | RSS growth ≥ 1.8x baseline ([Kit #51](https://github.com/isaac-sim/IsaacSim/issues) ≈ 200MB/CP) |

**Empirical evidence (this repo, 2026-05-11):**
- Batch 2 of 13 CPs after ~28 sequential CPs since restart: 5 CPs
  showed cube_final ∈ [10⁷, 10⁹] m and speed ∈ [10⁵, 10⁸] m/s
- Same CPs after Kit restart (batch 5): 4/5 unlocked
  (CP-43, CP-44, CP-47, CP-77 → stable_ok)
- One sample of drift-unlock-rate ≈ 80% via restart alone

**Today's mitigation:** manual Kit restart every ~25 CPs. Adds friction
and breaks unattended long-batch verify (overnight 109-CP runs).

**Upstream issues (NVIDIA-side, can't patch ourselves):**
- [Kit #51](https://github.com/isaac-sim/IsaacSim/issues) memory leak
- [cuRobo #603](https://github.com/NVlabs/curobo/issues/603) stale stage poses
- [IsaacLab #4561](https://github.com/isaac-sim/IsaacLab/issues/4561) surface velocity GPU pipeline

---

## 2. Goals and non-goals

### 2.1 Goals
- **G1:** `multi_run_regression.py --canonicals "<all 109>" --use-supervisor`
  runs to completion unattended, even if Kit drifts multiple times.
- **G2:** Drift recovery is *automatic and transparent*: failing CPs are
  retried once on fresh Kit; baseline JSON reports both retry attempts.
- **G3:** Per-CP elapsed baselines are learned online (EMA) and used to
  detect future drift.
- **G4:** All restart/drift events emitted as structured telemetry per §9.
- **G5:** Soft-reset path (cheaper than full restart) handles cuRobo
  cache staleness without process kill.

### 2.2 Non-goals
- Fixing upstream NVIDIA bugs (rapporteras, ej patchas)
- Speeding up cold-start cuRobo (~30-60s; accepted)
- Multi-Kit pooling (single-tenant Kit RPC stands)
- Cross-session Kit sharing (each session owns its Kit)

### 2.3 Success metrics (post-rollout)
- Unattended 109-CP overnight run completes in ≤4 hours
- Restart count per 109-CP run ≤ 6 (steady-state; one per ~20 CPs)
- DriftDetector precision (drift→restart actually helped): ≥80%
- Zero false-negative drift (results with cube > 100m never marked OK)
- Latency overhead per CP from supervision: ≤200ms p95

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│ multi_run_regression.py                                  │
│   if --use-supervisor:                                   │
│     sup = KitSupervisor(SupervisorConfig(...))           │
│     await sup.start()                                    │
│     for cp in canonicals:                                │
│       result = await sup.run_with_supervision(cp, run_fn)│
│     await sup.stop()  # emit final summary               │
└──────────────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────┐
│ KitSupervisor (orchestrator)                             │
│                                                          │
│   DriftDetector    HealthProbe       MemoryMonitor       │
│       │                │                  │              │
│       └────────────────┴──────────────────┘              │
│                       │                                  │
│                       ▼                                  │
│   RestartManager ───► Kit RPC (port 8001)                │
│                            │                             │
│              soft-reset ───┤                             │
│              (POST /admin/reset_world)                   │
│              hard-restart ─┤                             │
│              (kill + launch)                             │
└──────────────────────────────────────────────────────────┘
```

---

## 4. Component specifications

### 4.1 DriftDetector

**Purpose:** Pure-function classifier mapping `(cp, result, elapsed_s)`
→ `DriftSignal{level, reason, evidence}`.

```python
DriftLevel = Literal["ok", "warn", "drift"]

@dataclass(frozen=True)
class DriftSignal:
    level: DriftLevel
    reason: str
    evidence: dict[str, Any]   # serializable; used by telemetry
```

**Classification rules (in order; first match wins):**

| Order | Rule | Output |
|---|---|---|
| 1 | `cube_final[i]` absolute > 100 m | drift, "cube_position_absurd" |
| 2 | `speed` > 1000 m/s | drift, "speed_absurd" |
| 3 | `elapsed_s` > 2.5x EMA(cp) | drift, "elapsed_far_above_baseline" |
| 4 | `elapsed_s` > 1.5x EMA(cp) | warn, "elapsed_above_baseline" |
| 5 | default | ok, "" |

**Calibration:**
- EMA smoothing: `baseline = 0.7 * prev + 0.3 * new` (only on non-drift outcomes)
- Bootstraps from first observation per cp
- Per-CP baselines persisted via supervisor.stats() for debugging

### 4.2 RestartManager

**Purpose:** Full kill + relaunch of Kit process.

**API:**
```python
class RestartManager:
    async def restart(self, probe: HealthProbe) -> bool: ...
```

**Implementation contract:**
1. Locate Kit PID via `ss -ltnp` on configured port (default 8001)
2. `os.kill(pid, SIGKILL)` — Kit doesn't gracefully shut down for our use
3. Sleep 3s for OS to reap process
4. Launch via `bash <launch_script> <launch_args...>` in new session
5. Poll `probe.is_healthy()` every 3s, fail after `restart_timeout_s`
6. Return True iff probe returns ok within timeout

**Failure modes:**
- launch_script doesn't exist → log + return False (caller decides; supervisor escalates to fatal)
- Kit doesn't come up in timeout → log + return False; caller may retry once
- Multiple processes on port → kill all (defensive; race with manual restart)

### 4.3 HealthProbe

**Purpose:** Detect Kit liveness and slow-degradation.

**API:**
```python
class HealthProbe:
    async def is_healthy(self) -> Tuple[bool, str]:
        """{"ok": true} response within timeout_s."""

    async def is_responsive(self, max_latency_ms: float = 500) -> Tuple[bool, float]:
        """Probe responds within latency budget. Returns (ok, observed_ms)."""
```

`is_healthy()` is a binary up/down check used by the restart loop.
`is_responsive()` measures wall-time latency to catch slow degradation
not visible to is_healthy (e.g., GIL contention, GC pauses).

**Connection:** aiohttp client with `total=timeout_s` timeout. Closed
cleanly after each call (no connection pooling — Kit RPC restart
invalidates pools).

### 4.4 MemoryMonitor

**Purpose:** Track RSS + GPU memory growth.

**API:**
```python
class MemoryMonitor:
    def find_kit_pid(self, port: int) -> Optional[int]: ...
    def current_mb(self, pid: int) -> float:
        """RSS of process + descendants in MB."""
    def gpu_mb(self) -> float:
        """nvidia-smi sum of python-process GPU memory in MB."""
    def has_grown_rss(self, current_mb: float, threshold_x: float) -> bool: ...
    def has_grown_gpu(self, current_gpu_mb: float, threshold_x: float) -> bool: ...
```

**Baselines:** captured by `KitSupervisor.start()` and re-captured after
each restart (after Kit is healthy + 5s settle).

**Fallbacks (when optional deps unavailable):**
- No psutil → read `/proc/{pid}/status` VmRSS
- No nvidia-smi → return 0.0 (GPU monitoring degrades to no-op)

### 4.5 KitSupervisor (orchestrator)

**State machine:**

```
States: INIT → HEALTHY ↔ DRIFTED → RESTARTING → HEALTHY ↔ DEGRADED → ABORT
                  │              │
                  └─soft_reset───┘
```

**Transitions:**
- INIT → HEALTHY: `start()` returns True
- HEALTHY → DRIFTED: classifier returns "drift" OR pre-check fails
- DRIFTED → RESTARTING: invoke RestartManager
- RESTARTING → HEALTHY: RestartManager succeeded; baselines re-captured
- RESTARTING → DEGRADED: RestartManager failed once; retry once
- DEGRADED → ABORT: second restart failure; raise SupervisorAbortError
- HEALTHY → HEALTHY (soft_reset): cp_count % soft_reset_every_n == 0

**run_with_supervision algorithm:**
```python
async def run_with_supervision(cp, runner):
    # Pre-check
    pre_reason = await should_restart_pre()
    if pre_reason:
        emit("supervisor_restart_decision", cp=cp, reason=pre_reason, phase="pre")
        if not await do_hard_restart():
            raise SupervisorAbortError(...)
    elif should_soft_reset():
        emit("supervisor_soft_reset", cp=cp)
        await do_soft_reset()

    # Execute
    t0 = monotonic()
    try:
        result = await runner()
    except Exception as e:
        emit("supervisor_runner_exception", cp=cp, exc=str(e))
        raise
    elapsed = monotonic() - t0

    # Classify
    signal = detector.classify(cp, result, elapsed)
    emit("supervisor_drift_classification", cp=cp, level=signal.level,
         reason=signal.reason, elapsed_s=elapsed)

    if signal.level == "drift":
        emit("supervisor_drift_detected", cp=cp, evidence=signal.evidence)
        if not await do_hard_restart():
            raise SupervisorAbortError(...)
        if config.retry_on_drift:
            t1 = monotonic()
            result = await runner()
            elapsed_retry = monotonic() - t1
            signal_retry = detector.classify(cp, result, elapsed_retry)
            attach_supervisor_meta(result, signal_retry, elapsed_retry, retry=True)
            if signal_retry.level != "drift":
                detector.record_elapsed(cp, elapsed_retry)
            return result

    if signal.level != "drift":
        detector.record_elapsed(cp, elapsed)
    cp_count_since_restart += 1
    attach_supervisor_meta(result, signal, elapsed, retry=False)
    return result
```

**Error escalation:**
- `SupervisorAbortError` raised on second restart failure → caller
  (multi_run_regression) terminates batch with error baseline
- Single restart failure logged + retried once before abort

---

## 5. Kit-side hook: `/admin/reset_world`

**Purpose:** Soft-reset to clear cuRobo cache without full process restart.

**Location:** `service/isaac_assist_service/multimodal/routes.py` (existing
FastAPI router; add one route).

**Spec:**
```python
@router.post("/admin/reset_world")
async def reset_world(body: ResetWorldRequest = Body(...)) -> ResetWorldResponse:
    """Soft-reset: new_stage + gc.collect + cuRobo cache flush.

    Use case: Kit is healthy but cuRobo cache contains stale stage poses.
    Cheaper than full process restart. Does NOT fix PhysX scene
    corruption — those require hard-restart.
    """
```

**Request:**
```python
class ResetWorldRequest(BaseModel):
    flush_curobo: bool = True   # clear planner cache
    new_stage: bool = True      # create fresh USD stage
    gc_collect: bool = True     # run python gc
    timeout_s: float = 30.0     # how long to wait for completion
```

**Response:**
```python
class ResetWorldResponse(BaseModel):
    ok: bool
    duration_ms: float
    actions_performed: list[str]   # ["curobo_flushed", "stage_reset", "gc_done"]
    errors: list[str]              # per-action errors
```

**Implementation note:** route forwards to Kit-side `exec_sync` patch
that does the actual work in Kit's stage context.

### 5.1 Soft-reset semantics

Soft-reset is best-effort cleanup that does NOT fix:
- Accumulated PhysX scene corruption (need hard-restart)
- Memory leaks (need hard-restart)
- GPU driver state issues (need hard-restart)

Soft-reset DOES fix:
- cuRobo cache holding stale prim poses
- Lingering USD stage references preventing GC
- Python heap fragmentation (modest)

**Supervisor policy:**
- soft-reset every `soft_reset_every_n` CPs (default 10)
- hard-restart every `restart_every_n` CPs (default 25)
- hard-restart on drift detection (immediate)

---

## 6. Configuration surface

```python
@dataclass
class SupervisorConfig:
    # Restart cadence
    restart_every_n: int = 25
    soft_reset_every_n: int = 10
    enable_soft_reset: bool = True

    # Drift thresholds
    drift_on_explode: bool = True
    elapsed_warn_x: float = 1.5
    elapsed_drift_x: float = 2.5

    # Memory thresholds
    memory_threshold_x: float = 1.8
    gpu_memory_threshold_x: float = 1.5

    # Retry behavior
    retry_on_drift: bool = True
    max_retries_per_cp: int = 1
    abort_after_failed_restarts: int = 2

    # Timeouts (seconds)
    health_check_interval_s: float = 30.0
    health_timeout_s: float = 2.0
    restart_timeout_s: float = 120.0
    soft_reset_timeout_s: float = 30.0

    # Kit RPC connection
    kit_host: str = "localhost"
    kit_port: int = 8001

    # Launch
    launch_script: Path = Path("/home/anton/projects/robotics_lab/launch_isaac_sim_with_assist.sh")
    launch_args: list[str] = field(default_factory=lambda: ["--headless"])

    # Telemetry
    telemetry_emit: bool = True
    telemetry_session_id: str = field(default_factory=lambda: f"sup-{uuid.uuid4().hex[:8]}")
```

**Env-var overrides:** any field with name `FOO` is overridable by
`SUPERVISOR_FOO=...`. Implemented in `SupervisorConfig.from_env()`.

---

## 7. Integration with multi_run_regression

```python
# scripts/qa/multi_run_regression.py — additions only
p.add_argument("--use-supervisor", action="store_true")
p.add_argument("--restart-every-n", type=int, default=25)

# In main:
supervisor = None
if args.use_supervisor:
    supervisor = KitSupervisor(SupervisorConfig(
        restart_every_n=args.restart_every_n,
    ))
    ok = await supervisor.start()
    if not ok:
        print("[FAIL] supervisor.start failed")
        return 3

for label in targets:
    async def _do_run(_label=label):
        return await asyncio.wait_for(
            _run_one(_label, args.n_runs, args.seed),
            timeout=args.per_cp_timeout,
        )
    try:
        if supervisor:
            r = await supervisor.run_with_supervision(label, _do_run)
        else:
            r = await _do_run()
    except SupervisorAbortError as e:
        r = {"label": label, "verdict": "SUPERVISOR_ABORT", "err": str(e)}
        # Terminate batch: cannot continue without Kit
        break
    except Exception as e:
        r = {"label": label, "verdict": "EXC", "err": str(e)[:100]}

if supervisor:
    sup_stats = supervisor.stats()
    payload["supervisor_stats"] = sup_stats
    print(f"[supervisor] {sup_stats['total_restarts']} restarts, "
          f"{sup_stats['total_drift_events']} drift events")
```

---

## 8. State machine & failure modes

### 8.1 State diagram (text form)

```
            ┌─────────┐
            │  INIT   │
            └────┬────┘
                 │ start()
                 ▼
          ┌──────────────┐    cp_count % soft_n == 0
   ┌──────│   HEALTHY    │ ◄────────────────────────┐
   │      └──────┬───────┘                          │
   │             │ drift OR pre-check fail          │
   │             ▼                                  │
   │      ┌──────────────┐                          │
   │      │   DRIFTED    │                          │
   │      └──────┬───────┘                          │
   │             │                                  │
   │             ▼                                  │
   │      ┌──────────────┐  RestartManager.success  │
   │      │  RESTARTING  │──────────────────────────┘
   │      └──────┬───────┘
   │             │ RestartManager.fail
   │             ▼
   │      ┌──────────────┐  retry_restart.success
   │      │   DEGRADED   │─────────────────────► HEALTHY (recurse)
   │      └──────┬───────┘
   │             │ retry_restart.fail
   │             ▼
   │      ┌──────────────┐
   │      │    ABORT     │ → raise SupervisorAbortError
   │      └──────────────┘
   │
   └─ soft_reset cycle within HEALTHY
```

### 8.2 Failure mode enumeration

| Mode | Condition | Supervisor response |
|---|---|---|
| F1: Drift detected | cube > 100m or speed > 1000 m/s | hard-restart + retry CP |
| F2: Elapsed drift | elapsed_s > 2.5x EMA | hard-restart + retry CP |
| F3: Health probe fails | /health returns non-ok or timeout | hard-restart |
| F4: Memory grew | RSS > 1.8x baseline | hard-restart |
| F5: Periodic threshold | cp_count >= restart_every_n | hard-restart |
| F6: cuRobo stale | (heuristic, future) | soft-reset |
| F7: Restart fails 1x | RestartManager returns False | retry restart once |
| F8: Restart fails 2x | second RestartManager returns False | raise SupervisorAbortError |
| F9: Runner exception | underlying CP execution raises | re-raise; do NOT restart (likely test bug) |
| F10: Stale connection | aiohttp connection error | health probe retry |

---

## 9. Telemetry events

All events emitted via existing multimodal telemetry infrastructure
(`service/isaac_assist_service/multimodal/telemetry.py`).

### 9.1 New event types

```python
EVENT_SUPERVISOR_STARTED = "supervisor_started"
EVENT_SUPERVISOR_STOPPED = "supervisor_stopped"
EVENT_SUPERVISOR_DRIFT_CLASSIFICATION = "supervisor_drift_classification"
EVENT_SUPERVISOR_DRIFT_DETECTED = "supervisor_drift_detected"
EVENT_SUPERVISOR_RESTART_DECISION = "supervisor_restart_decision"
EVENT_SUPERVISOR_RESTART_STARTED = "supervisor_restart_started"
EVENT_SUPERVISOR_RESTART_COMPLETED = "supervisor_restart_completed"
EVENT_SUPERVISOR_RESTART_FAILED = "supervisor_restart_failed"
EVENT_SUPERVISOR_SOFT_RESET = "supervisor_soft_reset"
EVENT_SUPERVISOR_MEMORY_GROWTH = "supervisor_memory_growth"
EVENT_SUPERVISOR_RUNNER_EXCEPTION = "supervisor_runner_exception"
EVENT_SUPERVISOR_ABORT = "supervisor_abort"
```

### 9.2 Event payload schemas

**supervisor_started:**
```typescript
{ baseline_rss_mb: number, baseline_gpu_mb: number, kit_pid: number,
  config: SupervisorConfig }
```

**supervisor_drift_classification:** (emitted for every CP, level=ok|warn|drift)
```typescript
{ cp: string, level: "ok"|"warn"|"drift", reason: string,
  elapsed_s: number, baseline_elapsed_s: number|null }
```

**supervisor_drift_detected:** (emitted only on level=drift)
```typescript
{ cp: string, evidence: { cube_final?: number[], speed?: number,
  elapsed_s?: number, ratio?: number } }
```

**supervisor_restart_decision:**
```typescript
{ cp: string|null, reason: string, phase: "pre"|"on_drift", cp_count: number }
```

**supervisor_restart_completed:**
```typescript
{ duration_ms: number, new_baseline_rss_mb: number, new_baseline_gpu_mb: number }
```

**supervisor_restart_failed:**
```typescript
{ duration_ms: number, attempt: 1|2, will_retry: boolean }
```

**supervisor_soft_reset:**
```typescript
{ cp: string, actions: string[], duration_ms: number, errors: string[] }
```

**supervisor_abort:**
```typescript
{ total_restarts: number, total_drift_events: number, last_error: string }
```

### 9.3 Aggregator additions

`scripts/qa/analyze_multimodal_usage.py` adds new dashboards:

```python
def supervisor_health_summary(events):
    """Drift events, restart count, CPs/restart, abort rate."""

def supervisor_drift_precision(events):
    """% of drift detections that resulted in successful retry."""

def supervisor_per_cp_baselines(events):
    """Per-CP elapsed_s distributions for calibration."""
```

---

## 10. Test plan

### 10.1 L0 unit (`tests/test_kit_supervisor.py`)
**Target: ≥30 tests, ≥85% line coverage of kit_supervisor.py**

- DriftDetector: all 5 classification rules + edge cases (missing fields,
  empty per_run, negative speed)
- DriftDetector: EMA update math (verify formula)
- MemoryMonitor: has_grown_* with various inputs
- SupervisorConfig: defaults + custom + env override
- KitSupervisor state transitions: HEALTHY → DRIFTED → RESTARTING → HEALTHY
- KitSupervisor state transitions: failed restart → DEGRADED → ABORT
- KitSupervisor: should_restart_pre logic per condition
- KitSupervisor: retry-on-drift with stub runner

### 10.2 L1 light-integration (`pytest -m supervisor_integration`)
**Target: ≥10 tests; requires Kit RPC mock**

- Real Kit RPC mock returns 200 → probe is_healthy returns True
- Mock returns 500 → is_healthy returns False with reason
- Latency injection → is_responsive flags slow degradation
- RestartManager kill: stub psutil + signal — verify pid cleanup logic
- Telemetry emission: every restart fires exactly one
  supervisor_restart_completed event

### 10.3 E2E (`pytest -m supervisor_e2e`, opt-in slow tests)
**Target: 1-2 happy-path scenarios; require live Kit**

- 30-CP run with synthetic drift injection — verify restart fires + retry
- 50-CP unattended run completes; restart fires at least once
- Compare CP unlock-rate with supervisor vs without (same canonicals)

### 10.4 Manual validation (operator)
- Overnight 109-CP all-canonicals run; report unlock rate, restart
  count, abort rate, total wall time
- Compare against 2026-05-11 baseline (38/109 GREEN, 7 manual restarts)

---

## 11. Performance SLAs

| Operation | p50 budget | p95 budget | Hard limit |
|---|---|---|---|
| supervisor pre-check | 50ms | 200ms | 2s (health timeout) |
| drift classification | 1ms | 5ms | 10ms |
| memory probe | 20ms | 100ms | 500ms |
| hard restart | 45s | 90s | 120s (timeout) |
| soft reset | 5s | 15s | 30s |
| per-CP overhead (sans restart) | 60ms | 250ms | 1s |

**Acceptable per-CP overhead:** ≤200ms p95 (negligible vs ~70s per-CP
build+verify).

---

## 12. Roll-out & backwards-compat

### 12.1 Phased roll-out

| Phase | Scope | Risk gate |
|---|---|---|
| P0 | Land kit_supervisor.py + tests (no integration) | unit tests pass |
| P1 | Add --use-supervisor flag to multi_run_regression | opt-in only |
| P2 | Add /admin/reset_world endpoint | route is no-op fallback safe |
| P3 | Wire soft-reset call from supervisor | flag-gated |
| P4 | One-week opt-in dogfood; collect metrics | manual review |
| P5 | Default --use-supervisor=on for regression scripts | dogfood ok |
| P6 | Mark legacy non-supervisor path deprecated | telemetry confirms |

### 12.2 Backwards compatibility

- `multi_run_regression.py` works without --use-supervisor (no behavior
  change)
- `/admin/reset_world` endpoint returns 404 in older Kit deployments
  (route added in this PR; supervisor handles 404 gracefully)
- Telemetry events are additive; legacy aggregator skips unknown types

### 12.3 Migration triggers

If supervisor causes regressions:
- Tag commit, revert --use-supervisor flag to default off
- Keep code in tree for diagnostic use
- Continue investigation in branch

---

## 13. Open questions

1. **Calibration data needed:** per-CP p50 / p95 elapsed_s baseline.
   Solve via 1 clean overnight run with supervisor in shadow-mode
   (records but doesn't act).
2. **Soft-reset vs hard-restart heuristic:** when does cuRobo cache
   flush suffice vs need full process? Empirical; deferred to P3
   dogfood data.
3. **Should supervisor be in-process or sidecar?** In-process (called
   from regression script) is simpler. Sidecar (separate daemon
   watching Kit) survives multi_run_regression crashes. **Decision: in-
   process for v1; sidecar deferred to v2 if abort rate high.**
4. **Headless vs GUI:** does headless drift faster or slower than GUI?
   Need data. May affect default config.
5. **Multiple Kit instances:** out of scope; single-tenant stands.
6. **Cross-machine Kit RPC** (e.g., supervisor on workstation, Kit on
   render farm): deferred.

---

## 14. References

- v1 first-draft spec: this file's git history (commit `73e336c`)
- Multimodal foundation spec: `docs/specs/2026-05-08-multimodal-foundation-spec.md`
  (telemetry infrastructure inheritance)
- Memory: `feedback_kit_restart_autonomous.md` (2026-05-11 autonomous-restart authorization)
- Master execution plan: `docs/specs/2026-05-09-master-execution-plan.md`
- Empirical drift data: `workspace/baselines/verify-2026-05-11-batch{1..6}-baseline.json`

---

## 15. Implementation checklist

- [ ] `scripts/qa/kit_supervisor.py` per §4
- [ ] `service/isaac_assist_service/multimodal/routes.py`: add `/admin/reset_world` per §5
- [ ] `service/isaac_assist_service/multimodal/telemetry.py`: add 12 new event types per §9.1
- [ ] `scripts/qa/multi_run_regression.py`: --use-supervisor flag + integration per §7
- [ ] `tests/test_kit_supervisor.py`: ≥30 L0 tests per §10.1
- [ ] `tests/test_supervisor_integration.py` (new): ≥10 L1 tests per §10.2 (marked `pytest.mark.supervisor_integration`)
- [ ] `scripts/qa/analyze_multimodal_usage.py`: 3 new aggregator functions per §9.3
- [ ] Documentation in `service/isaac_assist_service/multimodal/__init__.py` export updates
- [ ] Update master execution plan to reference supervisor as Phase 5/6 prereq for unattended runs
