# Kit Supervisor — First Draft Spec

**Date:** 2026-05-11
**Status:** first draft — open for review, not yet committed to implementation
**Owner:** TBD
**Estimated LOC:** ~600-1000 (supervisor) + ~100 (Kit-side hooks)

---

## 1. Problem statement

Kit / Isaac Sim state drifts after ~25-30 sequential canonical-template
executions. Symptoms:
- PhysX explosions (cube positions in millions of meters, speeds in
  millions of m/s)
- cuRobo plan failures with previously-working scenes
- Slow stage loads (2x+ baseline)
- GPU memory growth (~200MB/CP per [Kit #51](https://github.com/isaac-sim/IsaacSim/issues))

**Empirical evidence (this repo, 2026-05-11):**
- Batch 2 of 13 CPs after ~28 sequential CPs: 5 CPs showed PhysX
  explosion (cube positions e+08, speeds e+07-e+08)
- Same CPs after Kit restart (batch 5): 4/5 unlocked (CP-43, CP-44,
  CP-47, CP-77 — all became stable_ok)
- One sample of "drift unlock-rate" ≈ 80% recovery via restart alone

**Today's mitigation:** manual Kit restart every ~25 CPs. Adds friction
and breaks unattended long-batch verify (overnight 109-CP runs
impossible).

**Upstream issues (NVIDIA-side, can't patch ourselves):**
- [Kit #51](https://github.com/isaac-sim/IsaacSim/issues) memory leak
- [cuRobo #603](https://github.com/NVlabs/curobo/issues/603) stale stage poses
- [IsaacLab #4561](https://github.com/isaac-sim/IsaacLab/issues/4561) surface velocity GPU pipeline

---

## 2. Goal

Build a **supervisor layer** that wraps Kit RPC such that
`multi_run_regression.py --canonicals "<all 109>" --n-runs 1` runs
unattended to completion with auto-recovery from drift. Operator can
go to bed; wake up to a clean baseline JSON.

**Non-goals:**
- Fixing upstream NVIDIA bugs (they will be reported, not patched)
- Speeding up cold-start cuRobo (~30-60s; accepted)
- Multi-Kit pooling (single-tenant Kit RPC stands)

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│ multi_run_regression.py (existing)                       │
│   for CP in canonicals:                                  │
│     result = await supervisor.run_canonical(CP)          │
└──────────────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────┐
│ kit_supervisor.py (NEW)                                  │
│                                                          │
│   ┌────────────────┐  ┌────────────────┐                │
│   │ DriftDetector  │  │ RestartManager │                │
│   └────────────────┘  └────────────────┘                │
│   ┌────────────────┐  ┌────────────────┐                │
│   │ HealthProbe    │  │ MemoryMonitor  │                │
│   └────────────────┘  └────────────────┘                │
│                                                          │
│   run_canonical(CP):                                     │
│     pre_check() → ok or restart                          │
│     result = await kit_rpc(...)                          │
│     post_check(result) → ok or flag for restart          │
│     state.tick()  → restart if N-threshold              │
└──────────────────────────────────────────────────────────┘
                      │ Kit RPC (port 8001)
                      ▼
┌──────────────────────────────────────────────────────────┐
│ Kit / Isaac Sim                                          │
│   + stage-hygiene endpoint (NEW, optional):              │
│     /admin/reset_world → new_stage + gc + curobo flush   │
└──────────────────────────────────────────────────────────┘
```

---

## 4. Component specifications

### 4.1 DriftDetector

Pure function. Given a CP result (per_run cube_final, speed, etc.),
returns one of: `OK`, `WARN`, `DRIFT`.

```python
@dataclass
class DriftSignal:
    level: Literal["ok", "warn", "drift"]
    reason: str
    evidence: dict

def classify_result(result: dict) -> DriftSignal:
    pr = (result.get("per_run") or [{}])[0]
    cube = pr.get("cube_final", [0, 0, 0])
    speed = float(pr.get("speed", 0))

    # Hard-drift: absurd positions or velocities
    if any(abs(x) > 100 for x in cube):
        return DriftSignal("drift", "cube_position_absurd", {"cube": cube})
    if speed > 1000:
        return DriftSignal("drift", "speed_absurd", {"speed": speed})

    # Soft-warn: elapsed time 2x baseline (TBD per-CP histogram)
    return DriftSignal("ok", "", {})
```

**Calibration:** run all-109 baseline once, record p95 elapsed per CP;
WARN at 1.5x p95, DRIFT at 2.5x p95.

### 4.2 RestartManager

```python
class RestartManager:
    def __init__(self, launch_script: Path):
        self.launch_script = launch_script
        self.kit_pid: Optional[int] = None

    async def restart(self) -> bool:
        """Kill Kit, relaunch, wait for /health. Returns True on success."""
        self._kill_kit()
        await asyncio.sleep(2)
        self._launch_kit()
        return await self._wait_health(timeout=120)

    def _kill_kit(self): ...
    def _launch_kit(self): ...
    async def _wait_health(self, timeout: float) -> bool: ...
```

**Restart cost:** ~30-60s for warm relaunch (cuRobo cache pre-built),
~120s cold. Acceptable overhead in unattended runs.

### 4.3 HealthProbe

```python
class HealthProbe:
    async def is_healthy(self) -> tuple[bool, str]:
        """Hit /health endpoint with 2s timeout. Return (ok, reason)."""
        ...

    async def is_responsive(self, max_latency_ms: float = 500) -> bool:
        """Send no-op RPC, measure latency. Detects slow-degradation."""
        ...
```

### 4.4 MemoryMonitor

```python
class MemoryMonitor:
    def __init__(self, kit_pid: int, baseline_mb: float = 0):
        self.kit_pid = kit_pid
        self.baseline_mb = baseline_mb

    def current_mb(self) -> float:
        """Sum RSS of Kit process + children (psutil)."""
        ...

    def gpu_mb(self) -> float:
        """Query nvidia-smi for Kit's GPU memory."""
        ...

    def has_grown(self, threshold_x: float = 1.5) -> bool:
        return self.current_mb() > self.baseline_mb * threshold_x
```

### 4.5 Supervisor (orchestrator)

```python
class KitSupervisor:
    def __init__(self,
                 restart_every_n: int = 25,
                 drift_on_explode: bool = True,
                 memory_threshold_x: float = 1.8):
        self.detector = DriftDetector()
        self.manager = RestartManager(...)
        self.probe = HealthProbe()
        self.memory = MemoryMonitor(...)
        self.cp_count = 0
        self.restart_every_n = restart_every_n

    async def run_canonical(self, cp: str) -> dict:
        # Pre-check
        if not await self.probe.is_healthy()[0]:
            await self.manager.restart()
        if self.cp_count >= self.restart_every_n:
            await self.manager.restart()
            self.cp_count = 0
        if self.memory.has_grown(self.memory_threshold_x):
            await self.manager.restart()

        # Run
        result = await kit_rpc.exec_canonical(cp)
        self.cp_count += 1

        # Post-check
        signal = self.detector.classify_result(result)
        if signal.level == "drift":
            logger.warning(f"DRIFT detected after {cp}: {signal.reason}")
            await self.manager.restart()
            self.cp_count = 0
            # Optionally: re-run this CP once on fresh Kit
            if self.retry_on_drift:
                return await kit_rpc.exec_canonical(cp)

        return result
```

---

## 5. Kit-side hooks (optional optimization)

A minimal Kit-side endpoint `/admin/reset_world` would let the supervisor
do a "soft restart" without full process restart. Cheaper than kill+relaunch.

```python
# exts/isaac_5.1/.../routes.py (new endpoint)
@router.post("/admin/reset_world")
async def reset_world(body: ResetRequest):
    """Soft-reset: new_stage + gc.collect + cuRobo cache flush."""
    import gc
    ctx = omni.usd.get_context()
    ctx.new_stage()
    if hasattr(_PLANNER_ATTR, "_curobo_motion_gen_cache"):
        _PLANNER_ATTR._curobo_motion_gen_cache.clear()
    gc.collect()
    return {"ok": True, "stage_reset": True}
```

**Trade-off:** soft-reset works for early drift (cuRobo cache stale),
but does NOT fix accumulated PhysX scene corruption — those need full
process restart. Supervisor strategy:
- Soft-reset every 10 CPs
- Hard-restart every 25 CPs OR on drift detection

---

## 6. Configuration

```python
# scripts/qa/kit_supervisor_config.py (new)
SUPERVISOR_CONFIG = {
    "restart_every_n": 25,           # hard-restart threshold
    "soft_reset_every_n": 10,         # soft-reset threshold
    "drift_on_explode": True,         # restart on PhysX explosion
    "memory_threshold_x": 1.8,        # restart at 1.8x baseline RSS
    "gpu_memory_threshold_x": 1.5,    # restart at 1.5x baseline GPU
    "elapsed_warn_x": 1.5,            # WARN at 1.5x p95
    "elapsed_drift_x": 2.5,           # DRIFT at 2.5x p95
    "retry_on_drift": True,           # re-run failed CP on fresh Kit
    "max_retries_per_cp": 1,          # avoid infinite retry loop
    "health_check_interval_s": 30,    # background health probe
    "launch_script": "/home/anton/projects/robotics_lab/launch_isaac_sim_with_assist.sh",
    "launch_args": ["--headless"],
}
```

---

## 7. Integration with multi_run_regression

Minimal API surface — `multi_run_regression.py` change is 3 lines:

```python
# Before:
result = await kit_rpc.exec_canonical(cp, ...)

# After:
from .kit_supervisor import KitSupervisor
supervisor = KitSupervisor(**SUPERVISOR_CONFIG)
result = await supervisor.run_canonical(cp, ...)
```

Existing scripts and tooling unchanged. Supervisor is opt-in via
`--use-supervisor` flag.

---

## 8. Testing strategy

### 8.1 Unit tests (`tests/test_kit_supervisor.py`)
- DriftDetector classifies known-bad results correctly
- DriftSignal evidence matches input shape
- MemoryMonitor.has_grown returns correct value vs baseline
- RestartManager handles missing launch_script gracefully

### 8.2 Integration tests (slow, opt-in via `pytest -m supervisor`)
- Full kill+relaunch cycle completes within 120s
- Soft-reset endpoint preserves Kit RPC reachability
- 50-CP unattended run completes; restart fires at least once
- Drift-injected scenario triggers restart and retry

### 8.3 End-to-end (manual)
- 109-CP all-templates verify overnight; report success rate, restart count
- Compare with non-supervisor run from earlier batch

---

## 9. Operational metrics (telemetry)

Emit via existing multimodal telemetry events (`telemetry.py`):

```python
EVENT_SUPERVISOR_RESTART = "supervisor_restart"
EVENT_SUPERVISOR_DRIFT_DETECTED = "supervisor_drift_detected"
EVENT_SUPERVISOR_SOFT_RESET = "supervisor_soft_reset"
EVENT_SUPERVISOR_MEMORY_THRESHOLD = "supervisor_memory_threshold"
```

Aggregator dashboard adds:
- Restarts per 109-CP run (target: ≤5)
- Drift-detection precision: % of "drift" classifications that actually
  needed restart vs false positives
- Time-to-restart distribution (kill→/health back to ok)

---

## 10. Open questions

1. **Calibration data needed:** baseline p95 elapsed per CP, baseline
   RSS, baseline GPU memory. Need one clean 109-CP run as reference.
2. **Soft-reset vs hard-restart heuristic:** when does cuRobo cache
   flush suffice vs need full process? Likely empirical — collect
   data across first month of operation.
3. **Should supervisor be in-process or sidecar?** In-process (called
   from regression script) is simpler. Sidecar (separate daemon
   watching Kit) survives multi_run_regression crashes. Default
   in-process; sidecar deferred.
4. **Headless vs GUI:** does headless drift faster or slower than GUI?
   Need data. May affect default config.
5. **Multiple Kit instances:** out of scope; single-tenant stands.

---

## 11. Roll-out

1. Land DriftDetector + tests (low risk; passive observer mode first)
2. Add RestartManager + integration into multi_run_regression
   behind `--use-supervisor` flag
3. Soft-reset Kit endpoint (NEW exts/.../routes.py route)
4. Full unattended overnight test on 109-CP
5. Make supervisor default-on for regression scripts after 1 week
   stable

---

## 12. Upstream issues to file

After collecting empirical data with supervisor in place:
- Kit memory growth profile per N CPs (concrete numbers for NVIDIA)
- cuRobo cache-staleness reproduction (minimal repro case)
- PhysX surface velocity GPU pipeline incompatibility documentation

These complement issues already reported by community.
