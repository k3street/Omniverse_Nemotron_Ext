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
from dataclasses import dataclass, field
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
    restart_every_n: int = 25
    soft_reset_every_n: int = 10
    drift_on_explode: bool = True
    memory_threshold_x: float = 1.8
    gpu_memory_threshold_x: float = 1.5
    elapsed_warn_x: float = 1.5
    elapsed_drift_x: float = 2.5
    retry_on_drift: bool = True
    max_retries_per_cp: int = 1
    health_check_interval_s: float = 30.0
    health_timeout_s: float = 2.0
    restart_timeout_s: float = 120.0
    launch_script: Path = field(default_factory=lambda: DEFAULT_LAUNCH_SCRIPT)
    launch_args: list = field(default_factory=lambda: list(DEFAULT_LAUNCH_ARGS))
    kit_host: str = DEFAULT_KIT_HOST
    kit_port: int = DEFAULT_KIT_PORT

    @property
    def health_url(self) -> str:
        return f"http://{self.kit_host}:{self.kit_port}/health"


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

    def __init__(self, health_url: str, timeout_s: float = 2.0):
        self.health_url = health_url
        self.timeout_s = timeout_s

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
    total_restarts: int = 0
    total_drift_events: int = 0
    total_soft_resets: int = 0


class KitSupervisor:
    """Top-level supervisor wrapping Kit RPC for long verify runs."""

    def __init__(self, config: Optional[SupervisorConfig] = None):
        self.config = config or SupervisorConfig()
        self.detector = DriftDetector(
            elapsed_warn_x=self.config.elapsed_warn_x,
            elapsed_drift_x=self.config.elapsed_drift_x,
        )
        self.probe = HealthProbe(
            self.config.health_url, timeout_s=self.config.health_timeout_s
        )
        self.memory = MemoryMonitor()
        self.manager = RestartManager(self.config)
        self.state = SupervisorState()

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
        return True

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
                return (
                    f"rss_grew:{current:.0f}MB vs baseline "
                    f"{self.memory.baseline_mb:.0f}MB"
                )

        return None

    async def run_with_supervision(
        self,
        cp: str,
        runner: Callable[[], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Run a single CP under supervision.

        Args:
            cp: canonical identifier (for tracking elapsed baseline)
            runner: async callable that executes the CP and returns a result dict

        Returns:
            Result dict from runner, possibly with supervisor metadata added.
        """
        # Pre-check
        reason = await self.should_restart_pre()
        if reason:
            logger.info(f"[supervisor] pre-restart for {cp}: {reason}")
            await self._do_restart()

        # Execute
        t0 = time.monotonic()
        try:
            result = await runner()
        except Exception as e:
            logger.error(f"[supervisor] runner exception for {cp}: {e}")
            raise
        elapsed_s = time.monotonic() - t0

        # Classify
        signal = self.detector.classify(cp, result, elapsed_s)
        if signal.level == "drift":
            self.state.total_drift_events += 1
            logger.warning(
                f"[supervisor] DRIFT for {cp}: {signal.reason} {signal.evidence}"
            )
            await self._do_restart()
            if self.config.retry_on_drift:
                logger.info(f"[supervisor] retrying {cp} on fresh Kit")
                t0 = time.monotonic()
                result = await runner()
                elapsed_s = time.monotonic() - t0
                signal = self.detector.classify(cp, result, elapsed_s)

        # Update baselines on non-drift outcomes
        if signal.level != "drift":
            self.detector.record_elapsed(cp, elapsed_s)

        self.state.cp_count_since_restart += 1

        # Attach supervisor metadata to result
        if isinstance(result, dict):
            result.setdefault("_supervisor", {})
            result["_supervisor"]["drift_level"] = signal.level
            result["_supervisor"]["drift_reason"] = signal.reason
            result["_supervisor"]["elapsed_s"] = elapsed_s

        return result

    async def _do_restart(self) -> bool:
        ok = await self.manager.restart(self.probe)
        if ok:
            self.state.total_restarts += 1
            self.state.cp_count_since_restart = 0
            # Re-capture baselines on fresh Kit
            pid = self.memory.find_kit_pid(self.config.kit_port)
            if pid is not None:
                await asyncio.sleep(5.0)  # let Kit settle
                self.memory.baseline_mb = self.memory.current_mb(pid)
                self.memory.baseline_gpu_mb = self.memory.gpu_mb()
        return ok

    def stats(self) -> Dict[str, Any]:
        return {
            "total_restarts": self.state.total_restarts,
            "total_drift_events": self.state.total_drift_events,
            "total_soft_resets": self.state.total_soft_resets,
            "cp_count_since_restart": self.state.cp_count_since_restart,
            "baseline_rss_mb": self.memory.baseline_mb,
            "baseline_gpu_mb": self.memory.baseline_gpu_mb,
            "elapsed_baselines": dict(self.detector._elapsed_baseline),
        }
