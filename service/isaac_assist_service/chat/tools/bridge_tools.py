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


async def _handle_list_bridges(args: Dict[str, Any]) -> Dict[str, Any]:
    """Enumerate all known bridges (alive + dead) by scanning /tmp/bridges/.

    Returns: {bridges: [{bridge_id, pid, alive, log_path, kind}, ...]}
    where kind is inferred from worker_path filename suffix.
    """
    bridges: List[Dict[str, Any]] = []
    if not _BRIDGE_DIR.exists():
        return {"bridges": [], "n": 0}
    pid_files = sorted(_BRIDGE_DIR.glob("*.pid"))
    for pf in pid_files:
        bridge_id = pf.stem
        log_path = _bridge_path(bridge_id, "log")
        worker_path = _bridge_path(bridge_id, "py")
        try:
            pid = int(pf.read_text().strip())
        except Exception:
            continue
        alive = False
        try:
            os.kill(pid, 0)
            try:
                status = Path(f"/proc/{pid}/status").read_text()
                state_line = next((l for l in status.splitlines() if l.startswith("State:")), "")
                if "Z" in state_line:
                    alive = False
                else:
                    alive = True
            except Exception:
                alive = True
        except ProcessLookupError:
            alive = False
        # Infer kind from worker code
        kind = "unknown"
        if worker_path.exists():
            txt = worker_path.read_text()[:500]
            if "ModbusTcpClient" in txt or "pymodbus" in txt:
                kind = "modbus"
            elif "asyncua" in txt:
                kind = "opcua"
            elif "paho.mqtt" in txt or "mqtt.Client" in txt:
                kind = "mqtt_sparkplug"
        bridges.append({
            "bridge_id": bridge_id,
            "pid": pid,
            "alive": alive,
            "log_path": str(log_path),
            "worker_path": str(worker_path),
            "kind": kind,
        })
    return {"bridges": bridges, "n": len(bridges),
            "n_alive": sum(1 for b in bridges if b["alive"])}


async def _handle_bridge_pause(args: Dict[str, Any]) -> Dict[str, Any]:
    """Pause a running bridge subprocess by sending SIGSTOP. The subprocess
    stops emitting attribute updates but stays alive. Use _handle_bridge_resume
    to restart it.

    Args:
      bridge_id: id returned by attach handler

    Returns: {ok, pid, paused: True}
    """
    bridge_id = args.get("bridge_id")
    if not bridge_id:
        return {"error": "bridge_pause requires bridge_id"}
    pid_path = _bridge_path(bridge_id, "pid")
    if not pid_path.exists():
        return {"error": "no such bridge_id"}
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGSTOP)
    except (ProcessLookupError, ValueError, OSError) as e:
        return {"error": f"pause failed: {type(e).__name__}: {e}"}
    return {"ok": True, "pid": pid, "paused": True}


async def _handle_bridge_resume(args: Dict[str, Any]) -> Dict[str, Any]:
    """Resume a paused bridge subprocess by sending SIGCONT.

    Args:
      bridge_id: id returned by attach handler

    Returns: {ok, pid, paused: False}
    """
    bridge_id = args.get("bridge_id")
    if not bridge_id:
        return {"error": "bridge_resume requires bridge_id"}
    pid_path = _bridge_path(bridge_id, "pid")
    if not pid_path.exists():
        return {"error": "no such bridge_id"}
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGCONT)
    except (ProcessLookupError, ValueError, OSError) as e:
        return {"error": f"resume failed: {type(e).__name__}: {e}"}
    return {"ok": True, "pid": pid, "paused": False}


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
    # Wait up to 5s for the SIGTERM to take effect. Use asyncio.sleep so
    # the event loop isn't blocked while other handlers run.
    for _ in range(50):
        try:
            os.kill(pid, 0)
            await asyncio.sleep(0.1)
        except ProcessLookupError:
            return {"ok": True, "pid": pid, "method": "SIGTERM"}
    # Force-kill
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return {"ok": True, "pid": pid, "method": "SIGKILL"}


# --- OPC-UA subprocess worker (M3) ---------------------------------------

_OPCUA_WORKER_TEMPLATE = '''#!/usr/bin/env python3
"""opcua_bridge_worker — runs as subprocess, polls OPC-UA nodes via asyncua."""
from __future__ import annotations
import asyncio, json, sys, time, signal
from asyncua import Client

CONFIG = json.loads(sys.stdin.read())
URL = CONFIG["url"]
RATE_HZ = float(CONFIG.get("rate_hz", 1.0))
NODE_MAP = CONFIG["node_map"]   # {usd_attr_path: "ns=2;i=2" or browse path}

_running = True

def _on_shutdown(signum, frame):
    global _running
    print(json.dumps({"ts": time.time(), "event": "shutdown_signal"}), flush=True)
    _running = False

signal.signal(signal.SIGTERM, _on_shutdown)
signal.signal(signal.SIGINT, _on_shutdown)


async def _run():
    global _running
    period = 1.0 / RATE_HZ
    try:
        async with Client(url=URL) as client:
            print(json.dumps({"ts": time.time(), "event": "connected", "url": URL}), flush=True)
            # Resolve nodes once
            nodes = {}
            for usd_path, node_id in NODE_MAP.items():
                try:
                    nodes[usd_path] = client.get_node(node_id)
                except Exception as e:
                    print(json.dumps({"ts": time.time(), "event": "resolve_failed",
                                      "node_id": node_id, "err": str(e)[:200]}), flush=True)
            last_t = 0.0
            while _running:
                now = time.time()
                if now - last_t < period:
                    await asyncio.sleep(period / 5)
                    continue
                last_t = now
                attrs = {}
                errors = []
                for usd_path, node in nodes.items():
                    try:
                        v = await node.read_value()
                        attrs[usd_path] = v
                    except Exception as e:
                        errors.append(usd_path + ": " + type(e).__name__ + ": " + str(e)[:120])
                out = {"ts": now, "attrs": attrs}
                if errors: out["errors"] = errors
                print(json.dumps(out, default=str), flush=True)
    except Exception as e:
        print(json.dumps({"ts": time.time(), "event": "client_error",
                          "err": type(e).__name__ + ": " + str(e)[:200]}), flush=True)
        sys.exit(1)


asyncio.run(_run())
'''


def _spawn_opcua_subprocess(url: str, node_map: Dict[str, str], rate_hz: float) -> Dict[str, Any]:
    bridge_id = _make_id()
    worker_path = _bridge_path(bridge_id, "py")
    pid_path = _bridge_path(bridge_id, "pid")
    log_path = _bridge_path(bridge_id, "log")

    worker_path.write_text(_OPCUA_WORKER_TEMPLATE)
    config = json.dumps({"url": url, "rate_hz": rate_hz, "node_map": node_map})
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
    time.sleep(1)
    return {
        "bridge_id": bridge_id,
        "pid": proc.pid,
        "log_path": str(log_path),
        "worker_path": str(worker_path),
    }


async def _handle_opcua_bridge_attach(args: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 M3: spawn supervised asyncua subprocess that polls OPC-UA
    nodes and emits attribute updates.

    Args:
      url: opc.tcp://host:4840
      node_map: {usd_attr_path: "ns=2;i=2"} — string node identifiers
      rate_hz: poll rate (default 1.0)

    Returns: {bridge_id, pid, log_path, ...}
    """
    url = (args.get("url") or "").strip()
    node_map = args.get("node_map") or {}
    rate_hz = float(args.get("rate_hz", 1.0))

    if not url:
        return {"error": "opcua_bridge_attach requires url"}
    if not node_map:
        return {"error": "opcua_bridge_attach requires non-empty node_map"}

    try:
        out = _spawn_opcua_subprocess(url, node_map, rate_hz)
    except Exception as e:
        return {"error": f"spawn_failed: {type(e).__name__}: {str(e)[:200]}"}

    return {
        "ok": True,
        **out,
        "url": url,
        "rate_hz": rate_hz,
        "n_nodes": len(node_map),
    }


async def _handle_diagnose_opcua_bridge(args: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 M3 honesty pair — same shape as diagnose_modbus_bridge."""
    # Reuse the modbus-diagnose code path; identical log + pid file format.
    return await _handle_diagnose_modbus_bridge(args)


async def _handle_opcua_bridge_detach(args: Dict[str, Any]) -> Dict[str, Any]:
    """Reuses modbus detach (PID file shape is identical)."""
    return await _handle_modbus_tcp_bridge_detach(args)


# --- MQTT-Sparkplug subprocess worker (M5) -------------------------------

_MQTT_WORKER_TEMPLATE = '''#!/usr/bin/env python3
"""mqtt_bridge_worker — runs as subprocess, subscribes to MQTT topics."""
from __future__ import annotations
import json, sys, time, signal
import paho.mqtt.client as mqtt

CONFIG = json.loads(sys.stdin.read())
HOST = CONFIG["host"]
PORT = int(CONFIG.get("port", 1883))
TOPIC_MAP = CONFIG["topic_map"]   # {usd_attr_path: mqtt_topic}
USERNAME = CONFIG.get("username")
PASSWORD = CONFIG.get("password")
KEEPALIVE = int(CONFIG.get("keepalive", 30))

_running = True
def _on_shutdown(signum, frame):
    global _running
    print(json.dumps({"ts": time.time(), "event": "shutdown_signal"}), flush=True)
    _running = False

signal.signal(signal.SIGTERM, _on_shutdown)
signal.signal(signal.SIGINT, _on_shutdown)


# Topic → list of usd_paths (multiple attrs can subscribe to same topic)
_topic_to_usd = {}
for usd_path, topic in TOPIC_MAP.items():
    _topic_to_usd.setdefault(topic, []).append(usd_path)


def on_connect(client, userdata, flags, rc, properties=None):
    print(json.dumps({"ts": time.time(), "event": "connected", "rc": int(rc),
                      "host": HOST, "port": PORT}), flush=True)
    for topic in _topic_to_usd:
        client.subscribe(topic, qos=1)


def on_message(client, userdata, msg):
    payload = msg.payload
    try:
        # Try JSON first
        v = json.loads(payload.decode())
    except Exception:
        # Fallback: try numeric, otherwise raw string
        try:
            v = float(payload.decode())
        except Exception:
            v = payload.decode(errors="replace")
    attrs = {}
    for usd_path in _topic_to_usd.get(msg.topic, []):
        attrs[usd_path] = v
    if attrs:
        out = {"ts": time.time(), "topic": msg.topic, "attrs": attrs}
        print(json.dumps(out, default=str), flush=True)


def on_disconnect(client, userdata, rc, properties=None):
    print(json.dumps({"ts": time.time(), "event": "disconnected", "rc": int(rc)}), flush=True)


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect
if USERNAME:
    client.username_pw_set(USERNAME, PASSWORD or "")

try:
    client.connect(HOST, PORT, keepalive=KEEPALIVE)
except Exception as e:
    print(json.dumps({"ts": time.time(), "event": "connect_failed",
                      "err": type(e).__name__ + ": " + str(e)[:200]}), flush=True)
    sys.exit(1)

client.loop_start()
while _running:
    time.sleep(0.1)
client.loop_stop()
client.disconnect()
'''


def _spawn_mqtt_subprocess(host: str, port: int, topic_map: Dict[str, str],
                             username: Optional[str], password: Optional[str]) -> Dict[str, Any]:
    bridge_id = _make_id()
    worker_path = _bridge_path(bridge_id, "py")
    pid_path = _bridge_path(bridge_id, "pid")
    log_path = _bridge_path(bridge_id, "log")

    worker_path.write_text(_MQTT_WORKER_TEMPLATE)
    config = json.dumps({
        "host": host, "port": port, "topic_map": topic_map,
        "username": username, "password": password,
    })
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
    time.sleep(1)
    return {
        "bridge_id": bridge_id, "pid": proc.pid,
        "log_path": str(log_path), "worker_path": str(worker_path),
    }


async def _handle_mqtt_sparkplug_bridge_attach(args: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 M5: spawn supervised paho-mqtt subprocess that subscribes
    to MQTT topics (Sparkplug-compatible) and emits attribute updates.

    Args:
      host: MQTT broker host
      port: broker port (default 1883)
      topic_map: {usd_attr_path: mqtt_topic}
      username/password: optional broker auth

    Returns: {bridge_id, pid, log_path, ...}
    """
    host = (args.get("host") or "").strip()
    port = int(args.get("port", 1883))
    topic_map = args.get("topic_map") or {}
    username = args.get("username")
    password = args.get("password")

    if not host:
        return {"error": "mqtt_sparkplug_bridge_attach requires host"}
    if not topic_map:
        return {"error": "mqtt_sparkplug_bridge_attach requires non-empty topic_map"}

    try:
        out = _spawn_mqtt_subprocess(host, port, topic_map, username, password)
    except Exception as e:
        return {"error": f"spawn_failed: {type(e).__name__}: {str(e)[:200]}"}

    return {
        "ok": True, **out,
        "host": host, "port": port,
        "n_topics": len(topic_map),
    }


async def _handle_diagnose_mqtt_sparkplug_bridge(args: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 6 M5 honesty pair (reuses modbus diagnose code path)."""
    return await _handle_diagnose_modbus_bridge(args)


async def _handle_mqtt_sparkplug_bridge_detach(args: Dict[str, Any]) -> Dict[str, Any]:
    """Reuses modbus detach (PID file shape is identical)."""
    return await _handle_modbus_tcp_bridge_detach(args)


async def _handle_openplc_runtime_attach(args: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 10 M5 P3: convenience wrapper around modbus_tcp_bridge_attach
    for OpenPLC Runtime connections.

    OpenPLC Runtime exposes its I/O via standard Modbus-TCP holding registers
    on default port 502. This wrapper just rewrites a more PLC-friendly
    interface (input_address, output_address, coil_address) into the
    register_map format used by modbus_tcp_bridge_attach.

    Args:
      host: OpenPLC Runtime host (default 127.0.0.1)
      port: Modbus TCP port (default 502)
      input_map: {usd_attr_path: input_register_addr}
      output_map: {usd_attr_path: output_register_addr}
      rate_hz: poll rate (default 10.0 — typical PLC scan rate)

    Returns: same shape as modbus_tcp_bridge_attach. ~50 LOC convenience.
    """
    host = (args.get("host") or "127.0.0.1").strip()
    port = int(args.get("port", 502))
    input_map = args.get("input_map") or {}
    output_map = args.get("output_map") or {}
    rate_hz = float(args.get("rate_hz", 10.0))

    if not input_map and not output_map:
        return {"error": "openplc_runtime_attach requires input_map or output_map"}

    # Merge into single register_map for the underlying Modbus bridge.
    register_map = {}
    register_map.update(input_map)
    # Output addresses get prefixed offset 1000 to disambiguate from inputs in
    # the bridge's holding-register read; OpenPLC Runtime maps QX0.0=1000+.
    for usd_path, addr in output_map.items():
        register_map[usd_path] = int(addr) + 1000

    if not register_map:
        return {"error": "openplc_runtime_attach merged register_map empty"}

    return await _handle_modbus_tcp_bridge_attach({
        "host": host, "port": port,
        "register_map": register_map,
        "rate_hz": rate_hz, "mode": "client",
    })


def register_bridge_handlers(handlers: Dict[str, Any]) -> None:
    """Hook used by tool_executor.py to register industrial-bridge handlers."""
    handlers["modbus_tcp_bridge_attach"] = _handle_modbus_tcp_bridge_attach
    handlers["modbus_tcp_bridge_detach"] = _handle_modbus_tcp_bridge_detach
    handlers["diagnose_modbus_bridge"] = _handle_diagnose_modbus_bridge
    handlers["opcua_bridge_attach"] = _handle_opcua_bridge_attach
    handlers["opcua_bridge_detach"] = _handle_opcua_bridge_detach
    handlers["diagnose_opcua_bridge"] = _handle_diagnose_opcua_bridge
    handlers["mqtt_sparkplug_bridge_attach"] = _handle_mqtt_sparkplug_bridge_attach
    handlers["mqtt_sparkplug_bridge_detach"] = _handle_mqtt_sparkplug_bridge_detach
    handlers["diagnose_mqtt_sparkplug_bridge"] = _handle_diagnose_mqtt_sparkplug_bridge
    handlers["openplc_runtime_attach"] = _handle_openplc_runtime_attach
    handlers["bridge_pause"] = _handle_bridge_pause
    handlers["bridge_resume"] = _handle_bridge_resume
    handlers["list_bridges"] = _handle_list_bridges
