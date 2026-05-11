"""
kit_supervisor.py — auto-restart wrapper for Kit RPC during long verify runs.

Per spec: docs/specs/2026-05-11-kit-supervisor-spec.md

Wraps Kit RPC such that long-running verify batches (e.g. 109 CPs in
one go) survive Kit-state-drift via:
- DriftDetector: classify result by cube_final / speed / timing
- RestartManager: kill+launch Kit, wait for /health
- HealthProbe: liveness + responsiveness check
- MemoryMonitor: RSS/GPU growth detection
- KitSupervisor: orchestrator; pre/post-check around each CP

Usage:
    from scripts.qa.kit_supervisor import KitSupervisor
    sup = KitSupervisor()
    await sup.start()  # initial health check, baseline capture

    for cp in canonicals:
        result = await sup.run_with_supervision(
            lambda: kit_rpc_exec_canonical(cp)
        )

    await sup.stop()
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_LAUNCH_SCRIPT = Path(
    "/home/anton/projects/robotics_lab/launch_isaac_sim_with_assist.sh"
)
DEFAULT_LAUNCH_ARGS = ["--headless"]
DEFAULT_KIT_PORT = 8001
DEFAULT_KIT_HOST = "localhost"


@dataclass
class SupervisorConfig:
    """All tunables for the supervisor. Loaded from kwargs or env."""
    # Cadence
    restart_every_n: int = 25
    soft_reset_every_n: int = 10
    enable_soft_reset: bool = True

    # Drift thresholds
    drift_on_explode: bool = True
    memory_threshold_x: float = 1.8
    gpu_memory_threshold_x: float = 1.5
    elapsed_warn_x: float = 1.5
    elapsed_drift_x: float = 2.5

    # Retry
    retry_on_drift: bool = True
    max_retries_per_cp: int = 1
    abort_after_failed_restarts: int = 2

    # Timeouts (seconds)
    health_check_interval_s: float = 30.0
    health_timeout_s: float = 2.0
    restart_timeout_s: float = 120.0
    soft_reset_timeout_s: float = 30.0

    # Launch
    launch_script: Path = field(default_factory=lambda: DEFAULT_LAUNCH_SCRIPT)
    launch_args: list = field(default_factory=lambda: list(DEFAULT_LAUNCH_ARGS))
    kit_host: str = DEFAULT_KIT_HOST
    kit_port: int = DEFAULT_KIT_PORT

    # Telemetry (opt-in; emits via MultimodalStore if available)
    telemetry_emit: bool = True
    telemetry_session_id: str = field(
        default_factory=lambda: f"sup-{os.urandom(4).hex()}"
    )

    @property
    def health_url(self) -> str:
        return f"http://{self.kit_host}:{self.kit_port}/health"

    @property
    def reset_url(self) -> str:
        return f"http://{self.kit_host}:{self.kit_port}/admin/reset_world"

    @classmethod
    def from_env(cls, **overrides) -> "SupervisorConfig":
        """Build config with SUPERVISOR_* env-var overrides."""
        kwargs = dict(overrides)
        for f in fields(cls):  # type: ignore  # imported lazily below
            env_key = f"SUPERVISOR_{f.name.upper()}"
            if env_key in os.environ and f.name not in kwargs:
                raw = os.environ[env_key]
                # Coerce to field type
                if f.type is bool or "bool" in str(f.type):
                    kwargs[f.name] = raw.lower() in ("1", "true", "yes", "on")
                elif f.type is int or "int" in str(f.type):
                    kwargs[f.name] = int(raw)
                elif f.type is float or "float" in str(f.type):
                    kwargs[f.name] = float(raw)
                else:
                    kwargs[f.name] = raw
        return cls(**kwargs)


class SupervisorAbortError(RuntimeError):
    """Raised when Kit cannot be recovered after retry-limit hit."""
    def __init__(self, message: str, stats: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.stats = stats or {}


# ---------------------------------------------------------------------------
# DriftDetector — pure classification
# ---------------------------------------------------------------------------

DriftLevel = Literal["ok", "warn", "drift"]


@dataclass
class DriftSignal:
    """Classification of a single CP result."""
    level: DriftLevel
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


class DriftDetector:
    """Classify Kit state drift from CP result + timing.

    DRIFT: clear signal Kit is corrupted (PhysX explosion, speed runaway,
        elapsed >> baseline). Trigger immediate restart.
    WARN: elevated but not yet drift (1.5x p95 elapsed, growing memory).
        Log but don't restart.
    OK: nominal.
    """

    POSITION_ABSURD_THRESHOLD = 100.0  # meters from origin
    SPEED_ABSURD_THRESHOLD = 1000.0    # m/s

    def __init__(self, elapsed_warn_x: float = 1.5, elapsed_drift_x: float = 2.5):
        self.elapsed_warn_x = elapsed_warn_x
        self.elapsed_drift_x = elapsed_drift_x
        # Per-CP elapsed baselines (p50), populated as data arrives
        self._elapsed_baseline: Dict[str, float] = {}

    def record_elapsed(self, cp: str, elapsed_s: float) -> None:
        """Record a successful (non-drift) elapsed for this CP."""
        if cp in self._elapsed_baseline:
            # Exponential moving average to smooth jitter
            prev = self._elapsed_baseline[cp]
            self._elapsed_baseline[cp] = prev * 0.7 + elapsed_s * 0.3
        else:
            self._elapsed_baseline[cp] = elapsed_s

    def classify(self, cp: str, result: Dict[str, Any], elapsed_s: float) -> DriftSignal:
        """Classify a result. cp is the canonical id; elapsed_s the wall time."""
        # 1) Explosion check: absurd cube position / speed
        pr_list = result.get("per_run") or []
        if pr_list:
            pr = pr_list[0]
            cube = pr.get("cube_final") or [0, 0, 0]
            speed = float(pr.get("speed", 0) or 0)

            if any(abs(float(c)) > self.POSITION_ABSURD_THRESHOLD for c in cube[:3]):
                return DriftSignal(
                    "drift",
                    "cube_position_absurd",
                    {"cube": cube, "threshold_m": self.POSITION_ABSURD_THRESHOLD},
                )
            if speed > self.SPEED_ABSURD_THRESHOLD:
                return DriftSignal(
                    "drift",
                    "speed_absurd",
                    {"speed": speed, "threshold_mps": self.SPEED_ABSURD_THRESHOLD},
                )

        # 2) Elapsed check vs baseline
        baseline = self._elapsed_baseline.get(cp)
        if baseline is not None and baseline > 0:
            ratio = elapsed_s / baseline
            if ratio >= self.elapsed_drift_x:
                return DriftSignal(
                    "drift",
                    "elapsed_far_above_baseline",
                    {"elapsed_s": elapsed_s, "baseline_s": baseline, "ratio": ratio},
                )
            if ratio >= self.elapsed_warn_x:
                return DriftSignal(
                    "warn",
                    "elapsed_above_baseline",
                    {"elapsed_s": elapsed_s, "baseline_s": baseline, "ratio": ratio},
                )

        return DriftSignal("ok", "", {})


# ---------------------------------------------------------------------------
# HealthProbe — liveness check via /health
# ---------------------------------------------------------------------------

class HealthProbe:
    """Probe Kit RPC /health endpoint."""

    def __init__(
        self,
        health_url: str,
        timeout_s: float = 2.0,
        reset_url: Optional[str] = None,
    ):
        self.health_url = health_url
        self.timeout_s = timeout_s
        # Soft-reset endpoint (optional; supervisor calls when configured)
        if reset_url is None:
            from urllib.parse import urlparse
            u = urlparse(health_url)
            reset_url = f"{u.scheme}://{u.netloc}/admin/reset_world"
        self.reset_url = reset_url

    async def is_healthy(self) -> Tuple[bool, str]:
        """Returns (ok, reason). reason is empty on success."""
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self.timeout_s)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(self.health_url) as resp:
                    if resp.status != 200:
                        return False, f"http_{resp.status}"
                    body = await resp.json()
                    if not body.get("ok"):
                        return False, f"body_not_ok: {body}"
                    return True, ""
        except asyncio.TimeoutError:
            return False, "timeout"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    async def is_responsive(
        self, max_latency_ms: float = 500.0
    ) -> Tuple[bool, float]:
        """Probe responds within latency budget. Returns (ok, observed_ms).

        Used to detect slow-degradation (GIL contention, GC pauses)
        not visible to is_healthy. Note: is_healthy returns ok|fail;
        this returns measurement of how long it took.
        """
        t0 = time.monotonic()
        ok, _ = await self.is_healthy()
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if not ok:
            return False, elapsed_ms
        return elapsed_ms <= max_latency_ms, elapsed_ms

    async def soft_reset(
        self,
        flush_curobo: bool = True,
        new_stage: bool = True,
        gc_collect: bool = True,
        timeout_s: float = 30.0,
    ) -> Tuple[bool, Dict[str, Any]]:
        """POST /admin/reset_world. Returns (ok, response_body).

        Falls back gracefully if endpoint doesn't exist (older Kit).
        """
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=timeout_s)
            body = {
                "flush_curobo": flush_curobo,
                "new_stage": new_stage,
                "gc_collect": gc_collect,
                "timeout_s": timeout_s,
            }
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(self.reset_url, json=body) as resp:
                    if resp.status == 404:
                        return False, {"ok": False, "reason": "endpoint_not_found"}
                    if resp.status != 200:
                        return False, {"ok": False, "reason": f"http_{resp.status}"}
                    return True, await resp.json()
        except asyncio.TimeoutError:
            return False, {"ok": False, "reason": "timeout"}
        except Exception as e:
            return False, {"ok": False, "reason": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# MemoryMonitor — RSS + GPU growth
# ---------------------------------------------------------------------------

class MemoryMonitor:
    """Monitor Kit process RSS + GPU memory growth."""

    def __init__(self, baseline_mb: float = 0.0, baseline_gpu_mb: float = 0.0):
        self.baseline_mb = baseline_mb
        self.baseline_gpu_mb = baseline_gpu_mb

    def find_kit_pid(self, port: int = DEFAULT_KIT_PORT) -> Optional[int]:
        """Locate Kit process by port via `ss` (Linux-only fallback to lsof)."""
        try:
            out = subprocess.run(
                ["ss", "-ltnp"],
                capture_output=True, text=True, timeout=2,
            ).stdout
            for line in out.splitlines():
                if f":{port} " in line and "pid=" in line:
                    # Format: ...users:(("python",pid=12345,fd=216))
                    idx = line.find("pid=")
                    pid_str = line[idx + 4:].split(",")[0]
                    try:
                        return int(pid_str)
                    except ValueError:
                        pass
        except Exception:
            pass
        try:
            out = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if out:
                return int(out.splitlines()[0])
        except Exception:
            pass
        return None

    def current_mb(self, pid: int) -> float:
        """Sum RSS of process + children in MB (via psutil if available)."""
        try:
            import psutil  # type: ignore
            p = psutil.Process(pid)
            total = p.memory_info().rss
            for child in p.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except Exception:
                    pass
            return total / (1024 * 1024)
        except Exception:
            # Fallback: read /proc/{pid}/status VmRSS
            try:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            kb = int(line.split()[1])
                            return kb / 1024.0
            except Exception:
                pass
        return 0.0

    def gpu_mb(self) -> float:
        """Query nvidia-smi for python process GPU memory. Best-effort."""
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-compute-apps=process_name,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=2,
            ).stdout
            total = 0.0
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2 and "python" in parts[0].lower():
                    try:
                        total += float(parts[1])
                    except ValueError:
                        pass
            return total
        except Exception:
            return 0.0

    def has_grown_rss(self, current_mb: float, threshold_x: float = 1.8) -> bool:
        return self.baseline_mb > 0 and current_mb > self.baseline_mb * threshold_x

    def has_grown_gpu(self, current_gpu_mb: float, threshold_x: float = 1.5) -> bool:
        return (
            self.baseline_gpu_mb > 0
            and current_gpu_mb > self.baseline_gpu_mb * threshold_x
        )


# ---------------------------------------------------------------------------
# RestartManager — kill + relaunch Kit
# ---------------------------------------------------------------------------

class RestartManager:
    """Kill the running Kit process and relaunch via the launch script."""

    def __init__(self, config: SupervisorConfig):
        self.config = config

    def _find_kit_pid(self) -> Optional[int]:
        mm = MemoryMonitor()
        return mm.find_kit_pid(self.config.kit_port)

    def _kill(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(f"kill {pid} failed: {e}")

    def _launch(self) -> Optional[int]:
        if not self.config.launch_script.exists():
            logger.error(f"launch_script does not exist: {self.config.launch_script}")
            return None
        cmd = ["bash", str(self.config.launch_script), *self.config.launch_args]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return proc.pid
        except Exception as e:
            logger.error(f"launch failed: {e}")
            return None

    async def _wait_for_health(
        self, probe: HealthProbe, timeout_s: float
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            ok, _ = await probe.is_healthy()
            if ok:
                return True
            await asyncio.sleep(3.0)
        return False

    async def restart(self, probe: HealthProbe) -> bool:
        """Kill Kit and relaunch; wait for /health to return ok.

        Returns True on success.
        """
        pid = self._find_kit_pid()
        if pid is not None:
            logger.info(f"[supervisor] killing Kit pid={pid}")
            self._kill(pid)
            await asyncio.sleep(3.0)
        else:
            logger.warning("[supervisor] no Kit pid found; launching fresh")

        launch_pid = self._launch()
        if launch_pid is None:
            return False
        logger.info(f"[supervisor] Kit relaunch initiated (launch pid={launch_pid})")

        ok = await self._wait_for_health(probe, self.config.restart_timeout_s)
        if ok:
            logger.info("[supervisor] Kit healthy after restart")
        else:
            logger.error(
                f"[supervisor] Kit failed to become healthy within "
                f"{self.config.restart_timeout_s}s"
            )
        return ok


# ---------------------------------------------------------------------------
# KitSupervisor — orchestrator
# ---------------------------------------------------------------------------

@dataclass
class SupervisorState:
    """Mutable runtime state."""
    cp_count_since_restart: int = 0
    total_cp_count: int = 0
    total_restarts: int = 0
    total_drift_events: int = 0
    total_soft_resets: int = 0
    consecutive_restart_failures: int = 0


class KitSupervisor:
    """Top-level supervisor wrapping Kit RPC for long verify runs.

    Per spec v2 §4.5. State machine:
        INIT → HEALTHY ↔ DRIFTED → RESTARTING → HEALTHY ↔ DEGRADED → ABORT

    Telemetry emission is best-effort: failures are logged but never raised.
    """

    def __init__(
        self,
        config: Optional[SupervisorConfig] = None,
        store: Optional[Any] = None,  # MultimodalStore; type-erased to avoid hard dep
    ):
        self.config = config or SupervisorConfig()
        self.detector = DriftDetector(
            elapsed_warn_x=self.config.elapsed_warn_x,
            elapsed_drift_x=self.config.elapsed_drift_x,
        )
        self.probe = HealthProbe(
            self.config.health_url,
            timeout_s=self.config.health_timeout_s,
            reset_url=self.config.reset_url,
        )
        self.memory = MemoryMonitor()
        self.manager = RestartManager(self.config)
        self.state = SupervisorState()
        self.store = store

    # ------------------------------------------------------------------ #
    # Telemetry emission (best-effort)
    # ------------------------------------------------------------------ #

    def _emit(self, event_type: str, **payload: Any) -> None:
        """Forward to multimodal telemetry. Silent on missing store."""
        if not self.config.telemetry_emit or self.store is None:
            return
        try:
            from service.isaac_assist_service.multimodal import telemetry as tel
            tel.emit(self.store, self.config.telemetry_session_id,
                     event_type, **payload)
        except Exception as e:
            logger.debug(f"[supervisor] telemetry emit {event_type} failed: {e}")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> bool:
        """Initial health check + baseline capture. Returns True if Kit live."""
        ok, reason = await self.probe.is_healthy()
        if not ok:
            logger.warning(f"[supervisor] Kit not healthy at start: {reason}")
            return False
        pid = self.memory.find_kit_pid(self.config.kit_port)
        if pid is not None:
            self.memory.baseline_mb = self.memory.current_mb(pid)
            self.memory.baseline_gpu_mb = self.memory.gpu_mb()
            logger.info(
                f"[supervisor] baseline RSS={self.memory.baseline_mb:.0f}MB "
                f"GPU={self.memory.baseline_gpu_mb:.0f}MB pid={pid}"
            )
            self._emit(
                "supervisor_started",
                baseline_rss_mb=self.memory.baseline_mb,
                baseline_gpu_mb=self.memory.baseline_gpu_mb,
                kit_pid=pid,
                config_dict={
                    "restart_every_n": self.config.restart_every_n,
                    "soft_reset_every_n": self.config.soft_reset_every_n,
                    "elapsed_drift_x": self.config.elapsed_drift_x,
                    "memory_threshold_x": self.config.memory_threshold_x,
                },
            )
        return True

    async def stop(self) -> Dict[str, Any]:
        """Emit final summary; return stats. Idempotent."""
        s = self.stats()
        self._emit("supervisor_stopped", **{k: v for k, v in s.items()
                                            if not isinstance(v, dict)})
        return s

    # ------------------------------------------------------------------ #
    # Decisions
    # ------------------------------------------------------------------ #

    async def should_restart_pre(self) -> Optional[str]:
        """Pre-CP restart decision. Returns reason if restart needed."""
        if self.state.cp_count_since_restart >= self.config.restart_every_n:
            return f"restart_every_n={self.config.restart_every_n}"

        ok, reason = await self.probe.is_healthy()
        if not ok:
            return f"health_failed:{reason}"

        pid = self.memory.find_kit_pid(self.config.kit_port)
        if pid is not None:
            current = self.memory.current_mb(pid)
            if self.memory.has_grown_rss(current, self.config.memory_threshold_x):
                self._emit(
                    "supervisor_memory_growth",
                    current_mb=current,
                    baseline_mb=self.memory.baseline_mb,
                    threshold_x=self.config.memory_threshold_x,
                )
                return (
                    f"rss_grew:{current:.0f}MB vs baseline "
                    f"{self.memory.baseline_mb:.0f}MB"
                )

        return None

    def should_soft_reset(self) -> bool:
        """Soft-reset cadence check (between hard-restart intervals)."""
        if not self.config.enable_soft_reset:
            return False
        if self.config.soft_reset_every_n <= 0:
            return False
        cnt = self.state.cp_count_since_restart
        # Trigger at every nth boundary, but NOT at 0 (just-restarted)
        return cnt > 0 and cnt % self.config.soft_reset_every_n == 0

    # ------------------------------------------------------------------ #
    # Main supervision entry
    # ------------------------------------------------------------------ #

    async def run_with_supervision(
        self,
        cp: str,
        runner: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Run a single CP under supervision. Per spec §4.5 algorithm.

        Raises:
            SupervisorAbortError: when Kit cannot recover after configured
                consecutive restart failures.
        """
        # Pre-check (hard-restart)
        reason = await self.should_restart_pre()
        if reason:
            logger.info(f"[supervisor] pre-restart for {cp}: {reason}")
            self._emit(
                "supervisor_restart_decision",
                cp=cp, reason=reason, phase="pre",
                cp_count=self.state.cp_count_since_restart,
            )
            await self._do_hard_restart(cp_context=cp)
        elif self.should_soft_reset():
            logger.info(f"[supervisor] soft-reset before {cp}")
            await self._do_soft_reset(cp_context=cp)

        # Execute
        t0 = time.monotonic()
        try:
            result = await runner()
        except Exception as e:
            logger.error(f"[supervisor] runner exception for {cp}: {e}")
            self._emit(
                "supervisor_runner_exception",
                cp=cp, exc=str(e)[:300], exc_type=type(e).__name__,
            )
            raise
        elapsed_s = time.monotonic() - t0

        # Classify
        signal = self.detector.classify(cp, result, elapsed_s)
        baseline = self.detector._elapsed_baseline.get(cp)
        self._emit(
            "supervisor_drift_classification",
            cp=cp, level=signal.level, reason=signal.reason,
            elapsed_s=round(elapsed_s, 3),
            baseline_elapsed_s=round(baseline, 3) if baseline else None,
        )

        if signal.level == "drift":
            self.state.total_drift_events += 1
            logger.warning(
                f"[supervisor] DRIFT for {cp}: {signal.reason} {signal.evidence}"
            )
            self._emit(
                "supervisor_drift_detected",
                cp=cp, reason=signal.reason, evidence=signal.evidence,
            )
            await self._do_hard_restart(cp_context=cp)
            if self.config.retry_on_drift:
                logger.info(f"[supervisor] retrying {cp} on fresh Kit")
                t0 = time.monotonic()
                try:
                    result = await runner()
                except Exception as e:
                    self._emit(
                        "supervisor_runner_exception",
                        cp=cp, exc=str(e)[:300], retry=True,
                    )
                    raise
                elapsed_s = time.monotonic() - t0
                signal = self.detector.classify(cp, result, elapsed_s)
                self._emit(
                    "supervisor_drift_classification",
                    cp=cp, level=signal.level, reason=signal.reason,
                    elapsed_s=round(elapsed_s, 3),
                    retry=True,
                )

        # Update baseline on non-drift outcomes only
        if signal.level != "drift":
            self.detector.record_elapsed(cp, elapsed_s)

        self.state.cp_count_since_restart += 1
        self.state.total_cp_count += 1

        # Attach supervisor metadata
        if isinstance(result, dict):
            result.setdefault("_supervisor", {})
            result["_supervisor"]["drift_level"] = signal.level
            result["_supervisor"]["drift_reason"] = signal.reason
            result["_supervisor"]["elapsed_s"] = round(elapsed_s, 3)

        return result

    # ------------------------------------------------------------------ #
    # Restart paths
    # ------------------------------------------------------------------ #

    async def _do_hard_restart(self, cp_context: Optional[str] = None) -> bool:
        """Kill + relaunch Kit; re-capture baselines. Aborts on repeated failure."""
        self._emit("supervisor_restart_started", cp=cp_context, kind="hard")
        t0 = time.monotonic()
        ok = await self.manager.restart(self.probe)
        dur_ms = (time.monotonic() - t0) * 1000.0

        if ok:
            self.state.total_restarts += 1
            self.state.cp_count_since_restart = 0
            self.state.consecutive_restart_failures = 0
            # Re-capture baselines on fresh Kit
            pid = self.memory.find_kit_pid(self.config.kit_port)
            if pid is not None:
                await asyncio.sleep(5.0)  # let Kit settle
                self.memory.baseline_mb = self.memory.current_mb(pid)
                self.memory.baseline_gpu_mb = self.memory.gpu_mb()
            self._emit(
                "supervisor_restart_completed",
                duration_ms=round(dur_ms, 1),
                new_baseline_rss_mb=self.memory.baseline_mb,
                new_baseline_gpu_mb=self.memory.baseline_gpu_mb,
            )
            return True

        # Restart failed
        self.state.consecutive_restart_failures += 1
        will_retry = (
            self.state.consecutive_restart_failures
            < self.config.abort_after_failed_restarts
        )
        self._emit(
            "supervisor_restart_failed",
            duration_ms=round(dur_ms, 1),
            attempt=self.state.consecutive_restart_failures,
            will_retry=will_retry,
        )
        if not will_retry:
            stats = self.stats()
            self._emit(
                "supervisor_abort",
                total_restarts=self.state.total_restarts,
                total_drift_events=self.state.total_drift_events,
                last_error="restart_failed",
            )
            raise SupervisorAbortError(
                f"Kit unrecoverable after "
                f"{self.state.consecutive_restart_failures} restart attempts",
                stats=stats,
            )
        # Single failure — retry once
        logger.warning("[supervisor] first restart failed; retrying once")
        return await self._do_hard_restart(cp_context=cp_context)

    async def _do_soft_reset(self, cp_context: Optional[str] = None) -> bool:
        """POST /admin/reset_world. Falls back to no-op if endpoint missing."""
        t0 = time.monotonic()
        ok, body = await self.probe.soft_reset(
            timeout_s=self.config.soft_reset_timeout_s
        )
        dur_ms = (time.monotonic() - t0) * 1000.0
        if ok:
            self.state.total_soft_resets += 1
            self._emit(
                "supervisor_soft_reset",
                cp=cp_context,
                actions=body.get("actions_performed", []),
                duration_ms=round(dur_ms, 1),
                errors=body.get("errors", []),
            )
            return True
        # Soft-reset failed — log but don't escalate; supervisor proceeds
        logger.info(
            f"[supervisor] soft-reset unavailable: "
            f"{body.get('reason', 'unknown')} (continuing)"
        )
        return False

    def stats(self) -> Dict[str, Any]:
        return {
            "total_restarts": self.state.total_restarts,
            "total_drift_events": self.state.total_drift_events,
            "total_soft_resets": self.state.total_soft_resets,
            "total_cp_count": self.state.total_cp_count,
            "cp_count_since_restart": self.state.cp_count_since_restart,
            "consecutive_restart_failures": self.state.consecutive_restart_failures,
            "baseline_rss_mb": self.memory.baseline_mb,
            "baseline_gpu_mb": self.memory.baseline_gpu_mb,
            "elapsed_baselines": dict(self.detector._elapsed_baseline),
        }
