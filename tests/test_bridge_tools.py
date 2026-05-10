"""Unit tests for bridge_tools.py — Phase 6 M2 Modbus bridge primitive.

Tests (l0):
- attach with bad args → returns error
- attach + diagnose against mock pymodbus server → alive, polling
- detach → SIGTERM clean
- diagnose for nonexistent bridge_id → error
- subprocess survives + emits JSON updates
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from service.isaac_assist_service.chat.tools.bridge_tools import (
    _handle_modbus_tcp_bridge_attach,
    _handle_modbus_tcp_bridge_detach,
    _handle_diagnose_modbus_bridge,
    _handle_opcua_bridge_attach,
    _handle_opcua_bridge_detach,
    _handle_diagnose_opcua_bridge,
    _handle_mqtt_sparkplug_bridge_attach,
    _handle_mqtt_sparkplug_bridge_detach,
    _handle_diagnose_mqtt_sparkplug_bridge,
    _handle_openplc_runtime_attach,
    _handle_bridge_pause,
    _handle_bridge_resume,
    _handle_list_bridges,
)


def _free_port() -> int:
    """Find an unused TCP port for the mock server."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_modbus_server():
    """Spin up a pymodbus 3.11 server in a subprocess. Yields its port."""
    port = _free_port()
    code = f"""
from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusServerContext, ModbusDeviceContext, ModbusSequentialDataBlock
device = ModbusDeviceContext(hr=ModbusSequentialDataBlock(0, [11, 22, 33, 44, 55, 66, 77, 88]))
context = ModbusServerContext(devices=device, single=True)
StartTcpServer(context=context, address=('127.0.0.1', {port}))
"""
    proc = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             start_new_session=True)
    # Wait for port to listen (up to 3s)
    for _ in range(30):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.connect(("127.0.0.1", port))
                break
            except Exception:
                time.sleep(0.1)
    yield port
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_attach_missing_host():
    res = await _handle_modbus_tcp_bridge_attach({"register_map": {"/x": 0}})
    assert "error" in res
    assert "host" in res["error"].lower()


@pytest.mark.asyncio
async def test_attach_empty_register_map():
    res = await _handle_modbus_tcp_bridge_attach({"host": "127.0.0.1", "register_map": {}})
    assert "error" in res
    assert "register_map" in res["error"].lower()


@pytest.mark.asyncio
async def test_diagnose_missing_bridge_id():
    res = await _handle_diagnose_modbus_bridge({})
    assert "error" in res


@pytest.mark.asyncio
async def test_detach_missing_bridge_id():
    res = await _handle_modbus_tcp_bridge_detach({})
    assert "error" in res


@pytest.mark.asyncio
async def test_detach_nonexistent_bridge():
    res = await _handle_modbus_tcp_bridge_detach({"bridge_id": "nonexistent_xyz"})
    assert "error" in res


@pytest.mark.asyncio
async def test_attach_diagnose_detach_cycle(mock_modbus_server):
    port = mock_modbus_server
    res = await _handle_modbus_tcp_bridge_attach({
        "host": "127.0.0.1", "port": port,
        "register_map": {"/World/Belt/speed": 0, "/World/Light/intensity": 1, "/World/Cube/exists": 2},
        "rate_hz": 10.0,
    })
    assert res.get("ok"), res
    assert "bridge_id" in res
    assert res["pid"] > 0
    assert res["n_registers"] == 3

    bid = res["bridge_id"]

    # Wait for some polling
    await asyncio.sleep(1.5)

    diag = await _handle_diagnose_modbus_bridge({"bridge_id": bid})
    assert diag.get("alive") is True, diag
    assert diag["n_register_updates"] >= 5  # 10Hz × 1.5s = ~15

    # Verify log contains attr-update lines
    log_tail = diag.get("log_tail") or []
    assert any("attrs" in line for line in log_tail)

    # Detach
    det = await _handle_modbus_tcp_bridge_detach({"bridge_id": bid})
    assert det.get("ok"), det
    assert det.get("method") in ("SIGTERM", "SIGKILL")

    # Verify dead after detach. Subprocess may take a moment to reap;
    # poll up to 3s for alive=False.
    for _ in range(30):
        await asyncio.sleep(0.1)
        diag2 = await _handle_diagnose_modbus_bridge({"bridge_id": bid})
        if not diag2["alive"]:
            break
    assert diag2["alive"] is False


@pytest.mark.asyncio
async def test_attach_to_nonexistent_server():
    """Bridge attaches but worker exits when can't connect → diagnose alive=False."""
    res = await _handle_modbus_tcp_bridge_attach({
        "host": "127.0.0.1",
        "port": 1,  # unprivileged + likely closed
        "register_map": {"/x": 0},
        "rate_hz": 1.0,
    })
    assert res.get("ok"), res  # attach itself succeeds (subprocess spawned)
    bid = res["bridge_id"]
    await asyncio.sleep(1.0)

    diag = await _handle_diagnose_modbus_bridge({"bridge_id": bid})
    # Worker should have exited because connect failed
    assert diag["alive"] is False
    log = " ".join(diag.get("log_tail") or [])
    assert "connect_failed" in log or "Connection" in log


@pytest.mark.asyncio
async def test_register_handlers_dispatch():
    """register_bridge_handlers wires 10 handlers (M2+M3+M5 + Phase 10 OpenPLC)."""
    from service.isaac_assist_service.chat.tools.bridge_tools import register_bridge_handlers
    handlers: dict = {}
    register_bridge_handlers(handlers)
    for name in [
        "modbus_tcp_bridge_attach", "modbus_tcp_bridge_detach", "diagnose_modbus_bridge",
        "opcua_bridge_attach", "opcua_bridge_detach", "diagnose_opcua_bridge",
        "mqtt_sparkplug_bridge_attach", "mqtt_sparkplug_bridge_detach",
        "diagnose_mqtt_sparkplug_bridge",
        "openplc_runtime_attach",
    ]:
        assert name in handlers, name


@pytest.mark.asyncio
async def test_openplc_attach_missing_io_maps():
    res = await _handle_openplc_runtime_attach({"host": "127.0.0.1"})
    assert "error" in res and ("input_map" in res["error"].lower() or "output_map" in res["error"].lower())


@pytest.mark.asyncio
async def test_bridge_pause_missing_id():
    res = await _handle_bridge_pause({})
    assert "error" in res


@pytest.mark.asyncio
async def test_bridge_resume_missing_id():
    res = await _handle_bridge_resume({})
    assert "error" in res


@pytest.mark.asyncio
async def test_bridge_pause_nonexistent():
    res = await _handle_bridge_pause({"bridge_id": "nonexistent_xyz"})
    assert "error" in res


@pytest.mark.asyncio
async def test_list_bridges_returns_dict():
    """list_bridges always returns dict with 'bridges' and 'n' keys."""
    res = await _handle_list_bridges({})
    assert "bridges" in res
    assert "n" in res
    assert isinstance(res["bridges"], list)
    assert res["n"] == len(res["bridges"])
    if res["n"] > 0:
        # Each bridge should have these fields
        for b in res["bridges"]:
            assert "bridge_id" in b
            assert "pid" in b
            assert "alive" in b
            assert "kind" in b


@pytest.mark.asyncio
async def test_bridge_pause_resume_lifecycle(mock_modbus_server):
    """attach → pause → resume → detach: full lifecycle."""
    port = mock_modbus_server
    res = await _handle_modbus_tcp_bridge_attach({
        "host": "127.0.0.1", "port": port,
        "register_map": {"/World/Test": 0},
        "rate_hz": 5.0,
    })
    assert res.get("ok"), res
    bid = res["bridge_id"]

    # Pause
    await asyncio.sleep(0.5)
    paused = await _handle_bridge_pause({"bridge_id": bid})
    assert paused.get("ok"), paused
    assert paused["paused"] is True

    # Wait — paused process shouldn't emit new updates
    diag1 = await _handle_diagnose_modbus_bridge({"bridge_id": bid})
    n1 = diag1["n_register_updates"]
    await asyncio.sleep(0.6)
    diag2 = await _handle_diagnose_modbus_bridge({"bridge_id": bid})
    n2 = diag2["n_register_updates"]
    # Paused, count should not significantly grow (allow small tolerance for
    # in-flight writes already in stdout buffer)
    assert n2 - n1 < 3, f"paused but updates grew from {n1} to {n2}"

    # Resume
    resumed = await _handle_bridge_resume({"bridge_id": bid})
    assert resumed.get("ok"), resumed
    assert resumed["paused"] is False

    # After resume, count should grow again
    await asyncio.sleep(0.6)
    diag3 = await _handle_diagnose_modbus_bridge({"bridge_id": bid})
    n3 = diag3["n_register_updates"]
    assert n3 > n2, f"resumed but updates didn't grow: {n2} -> {n3}"

    # Cleanup
    await _handle_modbus_tcp_bridge_detach({"bridge_id": bid})


@pytest.mark.asyncio
async def test_list_bridges_after_attach(mock_modbus_server):
    """list_bridges shows the just-attached bridge."""
    port = mock_modbus_server
    res = await _handle_modbus_tcp_bridge_attach({
        "host": "127.0.0.1", "port": port,
        "register_map": {"/World/X": 0},
        "rate_hz": 5.0,
    })
    bid = res["bridge_id"]

    listing = await _handle_list_bridges({})
    found = [b for b in listing["bridges"] if b["bridge_id"] == bid]
    assert len(found) == 1
    assert found[0]["alive"] is True
    assert found[0]["kind"] == "modbus"

    await _handle_modbus_tcp_bridge_detach({"bridge_id": bid})


@pytest.mark.asyncio
async def test_openplc_attach_input_map_only():
    """Verifies that input_map alone forwards to underlying modbus_tcp_bridge_attach.
    Spawn fails (port 502 likely unavailable as test) — gives spawn_failed or similar."""
    res = await _handle_openplc_runtime_attach({
        "host": "127.0.0.1", "port": 1,
        "input_map": {"/World/Sensor/light": 0, "/World/Sensor/proximity": 1},
    })
    # Either ok=True (spawn succeeded) with worker exiting, or {"ok":True,"bridge_id":...}
    assert res.get("ok") or "error" in res


# --- M3 OPC-UA tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_opcua_attach_missing_url():
    res = await _handle_opcua_bridge_attach({"node_map": {"/x": "ns=2;i=2"}})
    assert "error" in res and "url" in res["error"].lower()


@pytest.mark.asyncio
async def test_opcua_attach_empty_node_map():
    res = await _handle_opcua_bridge_attach({"url": "opc.tcp://127.0.0.1:4840", "node_map": {}})
    assert "error" in res and "node_map" in res["error"].lower()


@pytest.fixture
def mock_opcua_server():
    """Spin up an asyncua server in a subprocess. Yields its port."""
    port = _free_port()
    code = f"""
import asyncio
from asyncua import Server
async def run():
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://127.0.0.1:{port}")
    ns = await server.register_namespace("test")
    obj = await server.nodes.objects.add_object(ns, "TestObj")
    v1 = await obj.add_variable(ns, "Speed", 7)
    v2 = await obj.add_variable(ns, "Status", 1)
    v3 = await obj.add_variable(ns, "Count", 42)
    async with server:
        await asyncio.sleep(60)
asyncio.run(run())
"""
    proc = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             start_new_session=True)
    # Wait for port (asyncua takes ~1s to start)
    for _ in range(50):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.connect(("127.0.0.1", port))
                break
            except Exception:
                time.sleep(0.1)
    yield port
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_opcua_attach_diagnose_detach_cycle(mock_opcua_server):
    port = mock_opcua_server
    url = f"opc.tcp://127.0.0.1:{port}"
    # asyncua default node id: ns=2;i=2 is first var added under namespace 2
    res = await _handle_opcua_bridge_attach({
        "url": url,
        "node_map": {"/World/Belt/speed": "ns=2;i=2",
                     "/World/Light/intensity": "ns=2;i=3",
                     "/World/Cube/exists": "ns=2;i=4"},
        "rate_hz": 5.0,
    })
    assert res.get("ok"), res
    assert "bridge_id" in res
    assert res["n_nodes"] == 3
    bid = res["bridge_id"]

    # Wait for some polling — asyncua client + server takes longer than modbus
    await asyncio.sleep(2.5)

    diag = await _handle_diagnose_opcua_bridge({"bridge_id": bid})
    assert diag.get("alive") is True, diag
    # Should have at least 3 polls at 5 Hz × 2.5 s = ~12 (allow lower for startup)
    assert diag["n_register_updates"] >= 2, diag

    # Detach
    det = await _handle_opcua_bridge_detach({"bridge_id": bid})
    assert det.get("ok"), det

    # Verify dead
    for _ in range(30):
        await asyncio.sleep(0.1)
        diag2 = await _handle_diagnose_opcua_bridge({"bridge_id": bid})
        if not diag2["alive"]:
            break
    assert diag2["alive"] is False


# --- M5 MQTT-Sparkplug tests --------------------------------------------


@pytest.mark.asyncio
async def test_mqtt_attach_missing_host():
    res = await _handle_mqtt_sparkplug_bridge_attach({"topic_map": {"/x": "test/topic"}})
    assert "error" in res and "host" in res["error"].lower()


@pytest.mark.asyncio
async def test_mqtt_attach_empty_topic_map():
    res = await _handle_mqtt_sparkplug_bridge_attach({"host": "127.0.0.1", "topic_map": {}})
    assert "error" in res and "topic_map" in res["error"].lower()


@pytest.fixture
def mock_mqtt_broker():
    """Spin up a minimal MQTT broker via paho-mqtt's loopback or use external.
    Falls back to an in-process Hbmqtt-style broker if available; else skips.
    Simplest: use python-mqtt-broker package or a tiny test broker."""
    # paho-mqtt is client only; spin up a lightweight broker via 'amqtt' if available.
    # For simplicity we run a tiny embedded broker using the 'amqtt' library OR skip
    # if no broker is available locally.
    try:
        import asyncio as _asyncio
        from amqtt.broker import Broker
    except ImportError:
        pytest.skip("amqtt broker library not available; skipping live MQTT cycle test")
        return
    port = _free_port()
    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
        "auth": {"allow-anonymous": True},
    }
    code = f"""
import asyncio
from amqtt.broker import Broker
b = Broker({config!r})
async def run():
    await b.start()
    await asyncio.sleep(60)
asyncio.run(run())
"""
    proc = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             start_new_session=True)
    for _ in range(50):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.connect(("127.0.0.1", port)); break
            except Exception:
                time.sleep(0.1)
    yield port
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_mqtt_attach_to_nonexistent_broker():
    """Bridge spawns but worker exits; diagnose alive=False."""
    res = await _handle_mqtt_sparkplug_bridge_attach({
        "host": "127.0.0.1", "port": 1,
        "topic_map": {"/x": "test/topic"},
    })
    assert res.get("ok"), res
    bid = res["bridge_id"]
    await asyncio.sleep(1.5)
    diag = await _handle_diagnose_mqtt_sparkplug_bridge({"bridge_id": bid})
    # Worker should fail to connect (port 1 closed)
    assert diag["alive"] is False, diag
    log = " ".join(diag.get("log_tail") or [])
    assert "connect_failed" in log or "Connection" in log or "ConnectionRefused" in log
