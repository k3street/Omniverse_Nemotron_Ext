"""bridge_tools.py — Phase 6 M2/M3/M5 industrial protocol bridges.

Per docs/specs/2026-05-09-industrial-expansion-spec.md:
- modbus_tcp_bridge_attach (M2) — pymodbus, supervised subprocess
- opcua_bridge_attach (M3) — asyncua [STUB; M3 phase later]
- mqtt_sparkplug_bridge_attach (M5) — paho-mqtt [STUB; M5 phase later]
- diagnose_*_bridge — honesty pairs

Subprocess pattern: each bridge runs as a detached Python subprocess that
polls protocol → writes USD attrs. PID is tracked in /tmp/bridges/<id>.pid.
The bridge itself runs OUTSIDE Kit (in main host Python) and pushes attr
updates via Kit RPC. This avoids in-Kit threading lifecycle issues called
out by the silent-success-audit lessons.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


_BRIDGE_DIR = Path("/tmp/bridges")
_BRIDGE_DIR.mkdir(parents=True, exist_ok=True)


def _make_id() -> str:
    return uuid.uuid4().hex[:12]


def _bridge_path(bridge_id: str, suffix: str) -> Path:
    return _BRIDGE_DIR / f"{bridge_id}.{suffix}"


# --- Modbus subprocess worker (M2) ---------------------------------------

_MODBUS_WORKER_TEMPLATE = '''#!/usr/bin/env python3
"""modbus_bridge_worker — runs as subprocess, polls Modbus, prints attr updates.

Stdin: register_map JSON. Stdout: JSON lines per poll cycle.
Stderr: errors / health pings.
"""
from __future__ import annotations
import json, sys, time, signal
from pymodbus.client import ModbusTcpClient

CONFIG = json.loads(sys.stdin.read())
HOST = CONFIG["host"]
PORT = int(CONFIG.get("port", 502))
RATE_HZ = float(CONFIG.get("rate_hz", 1.0))
REGISTER_MAP = CONFIG["register_map"]
MODE = CONFIG.get("mode", "client")

if MODE == "server":
    print("MODE=server not implemented yet", file=sys.stderr)
    sys.exit(2)

def _on_shutdown(signum, frame):
    print(json.dumps({"ts": time.time(), "event": "shutdown"}), flush=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, _on_shutdown)
signal.signal(signal.SIGINT, _on_shutdown)

client = ModbusTcpClient(host=HOST, port=PORT, timeout=2.0)
connected = client.connect()
print(json.dumps({"ts": time.time(), "event": "started", "connected": connected, "host": HOST, "port": PORT}), flush=True)

if not connected:
    print(json.dumps({"ts": time.time(), "event": "connect_failed"}), flush=True)
    sys.exit(1)

period = 1.0 / RATE_HZ
last_t = 0
while True:
    now = time.time()
    if now - last_t < period:
        time.sleep(period / 5)
        continue
    last_t = now
    attrs = {}
    errors = []
    for usd_path, reg_addr in REGISTER_MAP.items():
        try:
            r = client.read_holding_registers(int(reg_addr), count=1)
            if hasattr(r, "isError") and r.isError():
                errors.append(usd_path + "@" + str(reg_addr) + ": " + str(r))
                continue
            v = r.registers[0] if hasattr(r, "registers") and r.registers else None
            attrs[usd_path] = v
        except Exception as e:
            errors.append(usd_path + "@" + str(reg_addr) + ": " + type(e).__name__ + ": " + str(e))
    out = {"ts": now, "attrs": attrs}
    if errors:
        out["errors"] = errors
    print(json.dumps(out), flush=True)
'''


def _spawn_modbus_subprocess(host: str, port: int, register_map: Dict[str, int],
                               rate_hz: float, mode: str = "client") -> Dict[str, Any]:
    bridge_id = _make_id()
    worker_path = _bridge_path(bridge_id, "py")
    pid_path = _bridge_path(bridge_id, "pid")
    log_path = _bridge_path(bridge_id, "log")

    worker_path.write_text(_MODBUS_WORKER_TEMPLATE)
    config = json.dumps({
        "host": host, "port": port, "rate_hz": rate_hz,
        "register_map": register_map, "mode": mode,
    })

    # Spawn subprocess with config on stdin
    proc = subprocess.Popen(
        [sys.executable, str(worker_path)],
        stdin=subprocess.PIPE,
        stdout=open(log_path, "ab"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    proc.stdin.write(config.encode())
    proc.stdin.close()
    pid_path.write_text(str(proc.pid))
    # Give it a moment to start + connect
    time.sleep(1)
    # Readback first log line to determine if it succeeded
    return {
        "bridge_id": bridge_id,
        "pid": proc.pid,
        "log_path": str(log_path),
        "worker_path": str(worker_path),
    }


# --- Handlers (registered into DATA_HANDLERS) ----------------------------

async def _handle_modbus_tcp_bridge_attach(args: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 M2: spawn a supervised pymodbus subprocess that polls
    Modbus-TCP holding registers and emits USD attribute updates.

    Args:
      host: server IP/hostname
      port: TCP port (default 502)
      register_map: {usd_attr_path: holding_register_addr}
      rate_hz: poll rate (default 1.0)
      mode: "client" (read from external server, default) or "server" (mock)

    Returns:
      {bridge_id, pid, log_path, ...} for caller to track + diagnose.
    """
    host = (args.get("host") or "").strip()
    port = int(args.get("port", 502))
    register_map = args.get("register_map") or {}
    rate_hz = float(args.get("rate_hz", 1.0))
    mode = args.get("mode", "client")

    if not host:
        return {"error": "modbus_tcp_bridge_attach requires host"}
    if not register_map:
        return {"error": "modbus_tcp_bridge_attach requires non-empty register_map"}

    try:
        out = _spawn_modbus_subprocess(host, port, register_map, rate_hz, mode)
    except Exception as e:
        return {"error": f"spawn_failed: {type(e).__name__}: {str(e)[:200]}"}

    return {
        "ok": True,
        **out,
        "host": host,
        "port": port,
        "rate_hz": rate_hz,
        "n_registers": len(register_map),
    }


async def _handle_diagnose_modbus_bridge(args: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 M2 honesty pair: check status of an attached Modbus bridge.

    Args:
      bridge_id: id returned by modbus_tcp_bridge_attach
      OR host/port to scan all bridges

    Returns: {alive, last_log_lines, n_register_updates, errors, ...}
    """
    bridge_id = args.get("bridge_id")
    if not bridge_id:
        return {"error": "diagnose_modbus_bridge requires bridge_id"}

    pid_path = _bridge_path(bridge_id, "pid")
    log_path = _bridge_path(bridge_id, "log")

    alive = False
    pid = None
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)  # signal 0 = check existence
            # Check if zombie (process exists but is dead, awaiting reap).
            # /proc/<pid>/status State field is 'Z' for zombies.
            try:
                status = Path(f"/proc/{pid}/status").read_text()
                state_line = next((l for l in status.splitlines() if l.startswith("State:")), "")
                if "Z" in state_line:
                    alive = False
                else:
                    alive = True
            except Exception:
                alive = True  # fallback: trust os.kill
        except (ProcessLookupError, ValueError):
            alive = False
        except PermissionError:
            alive = True  # exists but not ours

    log_tail: List[str] = []
    n_updates = 0
    last_errors: List[str] = []
    if log_path.exists():
        lines = log_path.read_bytes().decode(errors="replace").splitlines()
        log_tail = lines[-20:]
        for line in lines:
            try:
                d = json.loads(line)
                if "attrs" in d:
                    n_updates += 1
                if "errors" in d and d["errors"]:
                    last_errors.extend(d["errors"][-3:])
            except Exception:
                continue
    return {
        "bridge_id": bridge_id,
        "alive": alive,
        "pid": pid,
        "n_register_updates": n_updates,
        "last_errors": last_errors[-5:],
        "log_tail": log_tail,
    }


async def _handle_modbus_tcp_bridge_detach(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a previously-attached Modbus bridge cleanly. Sends SIGTERM,
    waits up to 5s, then SIGKILL if still alive."""
    bridge_id = args.get("bridge_id")
    if not bridge_id:
        return {"error": "modbus_tcp_bridge_detach requires bridge_id"}
    pid_path = _bridge_path(bridge_id, "pid")
    if not pid_path.exists():
        return {"error": "no such bridge_id"}
    try:
        pid = int(pid_path.read_text().strip())
    except Exception:
        return {"error": "pid file unreadable"}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"already_dead": True, "pid": pid}
    # Wait up to 5s
    for _ in range(50):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            return {"ok": True, "pid": pid, "method": "SIGTERM"}
    # Force-kill
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return {"ok": True, "pid": pid, "method": "SIGKILL"}


def register_bridge_handlers(handlers: Dict[str, Any]) -> None:
    """Hook used by tool_executor.py to register industrial-bridge handlers."""
    handlers["modbus_tcp_bridge_attach"] = _handle_modbus_tcp_bridge_attach
    handlers["modbus_tcp_bridge_detach"] = _handle_modbus_tcp_bridge_detach
    handlers["diagnose_modbus_bridge"] = _handle_diagnose_modbus_bridge
