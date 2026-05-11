"""
ros_mcp_tools.py
----------------
Async adapter that wraps the ros-mcp package's WebSocketManager
to provide live ROS2 topic/service/node interaction via rosbridge.

Isaac Sim publishes ROS2 topics through OmniGraph nodes.  rosbridge_server
exposes those topics over WebSocket.  This module connects our tool executor
to the running ROS2 system so the LLM can:
  - List / inspect / subscribe to topics  (verify OmniGraph is working)
  - Publish messages                       (drive robots, send commands)
  - Discover and call services             (trigger actions)
  - List nodes                             (health-check the ROS2 graph)

All functions are async and offload blocking WebSocket I/O to a thread pool.

Requires:
  pip install ros-mcp>=3.0.0
  rosbridge_server running on ROSBRIDGE_HOST:ROSBRIDGE_PORT
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

# Verify ros_mcp is installed at import time so the parent registration
# (try/except ImportError in tool_executor.py) can register these handlers
# as None and the audit/runtime can SKIP them with a clear "not installed"
# signal — instead of every handler call hitting a lazy ModuleNotFoundError.
import importlib
importlib.import_module("ros_mcp.utils.websocket")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-initialised WebSocketManager singleton
# ---------------------------------------------------------------------------
_ws_manager = None
_ws_lock = asyncio.Lock()


def _get_ws_manager():
    """Return the cached WebSocketManager, creating it on first call."""
    global _ws_manager
    if _ws_manager is None:
        from ros_mcp.utils.websocket import WebSocketManager
        from ...config import config
        _ws_manager = WebSocketManager(
            config.rosbridge_host,
            config.rosbridge_port,
            default_timeout=5.0,
        )
        logger.info(
            f"[RosMCP] Initialised WebSocketManager → "
            f"ws://{config.rosbridge_host}:{config.rosbridge_port}"
        )
    return _ws_manager


async def _run_sync(fn, *args, **kwargs):
    """Run a blocking function in the default executor (thread pool)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ---------------------------------------------------------------------------
# Helper: safe rosbridge request
# ---------------------------------------------------------------------------

def _safe_get_values(response: Optional[dict]) -> Optional[dict]:
    """Extract 'values' from a rosbridge response."""
    if response is None or not isinstance(response, dict):
        return None
    return response.get("values")


def _check_response(response: Optional[dict]) -> Optional[dict]:
    """Check for common rosbridge error patterns, return error dict or None."""
    if response is None:
        return {"error": "No response from rosbridge (connection failed or timed out)"}
    if not isinstance(response, dict):
        return {"error": f"Unexpected response type: {type(response).__name__}"}
    if "error" in response:
        return {"error": f"Rosbridge error: {response['error']}"}
    if response.get("op") == "status" and response.get("level") == "error":
        return {"error": f"Rosbridge status error: {response.get('msg', 'unknown')}"}
    if "result" in response and response["result"] is False:
        vals = response.get("values", {})
        msg = vals.get("message", "Service call failed") if isinstance(vals, dict) else "Service call failed"
        return {"error": msg}
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  CONNECTION
# ═══════════════════════════════════════════════════════════════════════════

def _connect_sync(ip: str, port: int) -> dict:
    """Configure rosbridge connection and test connectivity."""
    from ros_mcp.utils.network_utils import ping_ip_and_port
    ws = _get_ws_manager()
    ws.set_ip(ip, port)
    result = ping_ip_and_port(ip, port, ping_timeout=2.0, port_timeout=2.0)
    return {
        "message": f"WebSocket target set to {ip}:{port}",
        "connectivity": result,
    }


async def handle_ros2_connect(args: Dict[str, Any]) -> Dict[str, Any]:
    ip = str(args.get("ip", "127.0.0.1")).strip()
    port = int(args.get("port", 9090))
    result = await _run_sync(_connect_sync, ip, port)
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  TOPICS
# ═══════════════════════════════════════════════════════════════════════════

def _list_topics_sync() -> dict:
    ws = _get_ws_manager()
    message = {
        "op": "call_service",
        "service": "/rosapi/topics",
        "type": "rosapi/Topics",
        "args": {},
        "id": "get_topics_1",
    }
    with ws:
        response = ws.request(message)
    err = _check_response(response)
    if err:
        return err
    vals = _safe_get_values(response)
    if vals is not None:
        topics = vals.get("topics", [])
        types = vals.get("types", [])
        return {"topics": topics, "types": types, "topic_count": len(topics)}
    return {"warning": "No topics found"}


async def handle_ros2_list_topics(_args: Dict[str, Any]) -> Dict[str, Any]:
    return await _run_sync(_list_topics_sync)


def _get_topic_type_sync(topic: str) -> dict:
    ws = _get_ws_manager()
    message = {
        "op": "call_service",
        "service": "/rosapi/topic_type",
        "type": "rosapi/TopicType",
        "args": {"topic": topic},
        "id": f"topic_type_{topic.replace('/', '_')}",
    }
    with ws:
        response = ws.request(message)
    err = _check_response(response)
    if err:
        return err
    vals = _safe_get_values(response)
    if vals is not None:
        t = vals.get("type", "")
        if t:
            return {"topic": topic, "type": t}
        return {"error": f"Topic {topic} has no type"}
    return {"error": f"Failed to get type for {topic}"}


async def handle_ros2_get_topic_type(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = args.get("topic", "")
    if not topic:
        return {"error": "topic is required"}
    return await _run_sync(_get_topic_type_sync, topic)


def _get_message_details_sync(msg_type: str) -> dict:
    ws = _get_ws_manager()
    message = {
        "op": "call_service",
        "service": "/rosapi/message_details",
        "type": "rosapi/MessageDetails",
        "args": {"type": msg_type},
        "id": f"msg_details_{msg_type.replace('/', '_')}",
    }
    with ws:
        response = ws.request(message)
    err = _check_response(response)
    if err:
        return err
    vals = _safe_get_values(response)
    if vals is not None:
        typedefs = vals.get("typedefs", [])
        if typedefs:
            structure = {}
            for td in typedefs:
                tname = td.get("type", msg_type)
                fnames = td.get("fieldnames", [])
                ftypes = td.get("fieldtypes", [])
                structure[tname] = {n: t for n, t in zip(fnames, ftypes)}
            return {"message_type": msg_type, "structure": structure}
        return {"error": f"Message type {msg_type} not found"}
    return {"error": f"Failed to get details for {msg_type}"}


async def handle_ros2_get_message_type(args: Dict[str, Any]) -> Dict[str, Any]:
    msg_type = args.get("message_type", "")
    if not msg_type:
        return {"error": "message_type is required"}
    return await _run_sync(_get_message_details_sync, msg_type)


def _subscribe_once_sync(topic: str, msg_type: str, timeout: float) -> dict:
    ws = _get_ws_manager()
    sub_msg = {"op": "subscribe", "topic": topic, "type": msg_type, "queue_length": 1}
    with ws:
        send_err = ws.send(sub_msg)
        if send_err:
            return {"error": f"Failed to subscribe: {send_err}"}
        end_time = time.time() + timeout
        while time.time() < end_time:
            response = ws.receive(timeout=0.5)
            if response is None:
                continue
            try:
                data = json.loads(response) if isinstance(response, str) else response
            except (json.JSONDecodeError, TypeError):
                continue
            if data.get("op") == "status" and data.get("level") == "error":
                ws.send({"op": "unsubscribe", "topic": topic})
                return {"error": f"Rosbridge error: {data.get('msg', 'unknown')}"}
            if data.get("op") == "publish" and data.get("topic") == topic:
                ws.send({"op": "unsubscribe", "topic": topic})
                return {"topic": topic, "msg": data.get("msg", {})}
        ws.send({"op": "unsubscribe", "topic": topic})
    return {"error": f"Timeout ({timeout}s) waiting for message on {topic}"}


async def handle_ros2_subscribe_once(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = args.get("topic", "")
    msg_type = args.get("msg_type", "")
    timeout = float(args.get("timeout", 5.0))
    if not topic or not msg_type:
        return {"error": "topic and msg_type are required"}
    return await _run_sync(_subscribe_once_sync, topic, msg_type, timeout)


def _publish_once_sync(topic: str, msg_type: str, msg: dict) -> dict:
    ws = _get_ws_manager()
    with ws:
        ws.send({"op": "advertise", "topic": topic, "type": msg_type})
        response = ws.receive(timeout=1.0)
        if response:
            try:
                data = json.loads(response) if isinstance(response, str) else response
                if data.get("op") == "status" and data.get("level") == "error":
                    ws.send({"op": "unadvertise", "topic": topic})
                    return {"error": f"Advertise failed: {data.get('msg', 'unknown')}"}
            except (json.JSONDecodeError, TypeError):
                pass
        send_err = ws.send({"op": "publish", "topic": topic, "msg": msg})
        if send_err:
            ws.send({"op": "unadvertise", "topic": topic})
            return {"error": f"Publish failed: {send_err}"}
        ws.send({"op": "unadvertise", "topic": topic})
    return {"success": True, "topic": topic, "msg_type": msg_type}


async def handle_ros2_publish(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = args.get("topic", "")
    msg_type = args.get("msg_type", "")
    data = args.get("data", {})
    if not topic or not msg_type:
        return {"error": "topic and msg_type are required"}
    if not data:
        return {"error": "data (message payload) is required"}
    return await _run_sync(_publish_once_sync, topic, msg_type, data)


def _publish_sequence_sync(
    topic: str, msg_type: str,
    messages: List[dict], durations: List[float],
    rate_hz: float,
) -> dict:
    if len(messages) != len(durations):
        return {"error": "messages and durations must have the same length"}
    if any(d < 0 for d in durations):
        return {"error": "durations must be >= 0"}

    ws = _get_ws_manager()
    published = 0
    errors: List[str] = []

    with ws:
        ws.send({"op": "advertise", "topic": topic, "type": msg_type})
        try:
            for i, (msg, dur) in enumerate(zip(messages, durations)):
                pub_msg = {"op": "publish", "topic": topic, "msg": msg}
                if rate_hz > 0 and dur > 0:
                    interval = 1.0 / rate_hz
                    end = time.time() + dur
                    nxt = time.time() + interval
                    while time.time() < end:
                        err = ws.send(pub_msg)
                        if err:
                            errors.append(f"Msg {i}: {err}")
                            break
                        published += 1
                        sleep_t = nxt - time.time()
                        if sleep_t > 0:
                            time.sleep(sleep_t)
                        nxt += interval
                else:
                    err = ws.send(pub_msg)
                    if err:
                        errors.append(f"Msg {i}: {err}")
                        continue
                    published += 1
                    if dur > 0:
                        time.sleep(dur)
        finally:
            ws.send({"op": "unadvertise", "topic": topic})

    return {
        "success": True,
        "published_count": published,
        "total_messages": len(messages),
        "topic": topic,
        "msg_type": msg_type,
        "rate_hz": rate_hz,
        "errors": errors,
    }


async def handle_ros2_publish_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = args.get("topic", "")
    msg_type = args.get("msg_type", "")
    messages = args.get("messages", [])
    durations = args.get("durations", [])
    rate_hz = float(args.get("rate_hz", 0))
    if not topic or not msg_type:
        return {"error": "topic and msg_type are required"}
    if not messages:
        return {"error": "messages list is required"}
    if rate_hz < 0 or rate_hz > 100:
        return {"error": "rate_hz must be 0-100"}
    return await _run_sync(
        _publish_sequence_sync, topic, msg_type, messages, durations, rate_hz,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  SERVICES
# ═══════════════════════════════════════════════════════════════════════════

def _list_services_sync() -> dict:
    ws = _get_ws_manager()
    message = {
        "op": "call_service",
        "service": "/rosapi/services",
        "type": "rosapi_msgs/srv/Services",
        "args": {},
        "id": "get_services_1",
    }
    with ws:
        response = ws.request(message)
    err = _check_response(response)
    if err:
        return err
    vals = _safe_get_values(response)
    if vals is not None:
        services = vals.get("services", [])
        return {"services": services, "service_count": len(services)}
    return {"warning": "No services found"}


async def handle_ros2_list_services(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _run_sync(_list_services_sync)


def _call_service_sync(
    service_name: str, service_type: str, request: dict, timeout: float,
) -> dict:
    ws = _get_ws_manager()
    message = {
        "op": "call_service",
        "service": service_name,
        "type": service_type,
        "args": request,
        "id": f"call_{service_name.replace('/', '_')}",
    }
    with ws:
        response = ws.request(message, timeout=timeout)
    err = _check_response(response)
    if err:
        return {"service": service_name, "success": False, **err}
    if response.get("op") == "service_response":
        return {
            "service": service_name,
            "service_type": service_type,
            "success": response.get("result", True),
            "result": response.get("values", {}),
        }
    return {"service": service_name, "success": False, "error": "Unexpected response"}


async def handle_ros2_call_service(args: Dict[str, Any]) -> Dict[str, Any]:
    name = args.get("service_name", "")
    stype = args.get("service_type", "")
    request = args.get("request", {})
    timeout = float(args.get("timeout", 5.0))
    if not name or not stype:
        return {"error": "service_name and service_type are required"}
    return await _run_sync(_call_service_sync, name, stype, request, timeout)


# ═══════════════════════════════════════════════════════════════════════════
#  NODES
# ═══════════════════════════════════════════════════════════════════════════

def _list_nodes_sync() -> dict:
    ws = _get_ws_manager()
    message = {
        "op": "call_service",
        "service": "/rosapi/nodes",
        "type": "rosapi/Nodes",
        "args": {},
        "id": "get_nodes_1",
    }
    with ws:
        response = ws.request(message)
    err = _check_response(response)
    if err:
        return err
    vals = _safe_get_values(response)
    if vals is not None:
        nodes = vals.get("nodes", [])
        return {"nodes": nodes, "node_count": len(nodes)}
    return {"warning": "No nodes found"}


async def handle_ros2_list_nodes(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _run_sync(_list_nodes_sync)


def _get_node_details_sync(node: str) -> dict:
    ws = _get_ws_manager()
    message = {
        "op": "call_service",
        "service": "/rosapi/node_details",
        "type": "rosapi/NodeDetails",
        "args": {"node": node},
        "id": f"node_details_{node.replace('/', '_')}",
    }
    with ws:
        response = ws.request(message)
    err = _check_response(response)
    if err:
        return err
    vals = _safe_get_values(response)
    if vals is not None:
        pubs = vals.get("publishing", [])
        subs = vals.get("subscribing", [])
        svcs = vals.get("services", [])
        return {
            "node": node,
            "publishers": pubs,
            "subscribers": subs,
            "services": svcs,
            "publisher_count": len(pubs),
            "subscriber_count": len(subs),
            "service_count": len(svcs),
        }
    return {"error": f"Node {node} not found"}


async def handle_ros2_get_node_details(args: Dict[str, Any]) -> Dict[str, Any]:
    node = args.get("node", "")
    if not node:
        return {"error": "node name is required"}
    return await _run_sync(_get_node_details_sync, node)
