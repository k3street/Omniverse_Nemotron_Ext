# Phase 7F — ZMQ Sensor Streaming (Scoped Down)

**Status:** Not implemented  
**Depends on:** Phase 1 (tool schema)  
**Research:** `research_reports/7F_zmq_bridge.md`, `rev2/defend_7F_zmq.md`

---

## Overview

**Scoped down from original 5-task plan to 1 task.** ZMQ provides a lightweight, high-throughput alternative to ROS2 for streaming sensor data to training scripts without the 500+ MB ROS2 dependency.

**Rationale for keeping (not dropping):**
- < 30% of Isaac Sim users run ROS2 (ML/RL researchers use tensors, not DDS)
- ZMQ C++ mode: 1-4 ms latency vs ROS2 DDS 8-25 ms
- `pip install pyzmq` (2 MB) vs ROS2 system install
- IsaacLab's paradigm (shared memory, raw buffers) is ZMQ-shaped

**Rationale for scoping down:**
- Python-level ZMQ in Kit is dangerous (single-threaded event loop, blocking recv)
- NVIDIA's `OgnIsaacBridgeZMQNode` C++ node already handles the hard part
- ROS2 bridge (4B/8F) covers the same functional surface for ROS2 users

---

## Single Tool

### 7F.1 `configure_zmq_stream(camera_prim, pub_port, resolution, fps)`

**Type:** CODE_GEN handler (generates OmniGraph action graph)

**Implementation:** Wire NVIDIA's existing C++ `OgnIsaacBridgeZMQNode` via `og.Controller.edit()`:

```python
og.Controller.edit(
    {"graph_path": "/ZMQStream", "evaluator_name": "execution"},
    {
        og.Controller.Keys.CREATE_NODES: [
            ("OnTick", "omni.graph.action.OnPlaybackTick"),
            ("ZMQBridge", "isaacsim.bridge.zmq.OgnIsaacBridgeZMQNode"),
            ("CameraHelper", "isaacsim.ros2.bridge.ROS2CameraHelper"),
        ],
        og.Controller.Keys.CONNECT: [...],
        og.Controller.Keys.SET_VALUES: [
            ("ZMQBridge.inputs:address", f"tcp://*:{pub_port}"),
            ...
        ],
    }
)
```

**No Python-level ZMQ sockets in Kit.** All I/O happens in the C++ OmniGraph node.

**Parameters:**
- `camera_prim` (string): Path to camera or lidar prim
- `pub_port` (int, default 5555): ZMQ PUB port
- `resolution` (array, default [640, 480]): Downscale for streaming
- `fps` (int, default 30): Target frame rate
- `compression` (enum: none/jpeg, default jpeg): JPEG for cameras, none for lidar

---

## Dropped Tasks (from original spec)

| Original Task | Why Dropped |
|---------------|-------------|
| 7F.2 `zmq_publish(topic, data)` | Python-level ZMQ publish from Kit = threading risk |
| 7F.3 `zmq_subscribe(topic, callback_script)` | Blocking recv in Kit = sim freeze |
| 7F.4 `zmq_list_connections()` | Covered by OmniGraph node inspection |
| 7F.5 User flow | Simplified to single tool |

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| OmniGraph code generation | L0 | compile(), verify node types |
| Port validation | L0 | Range check, conflict detection |
| Actual streaming | L3 | Requires Kit + subscriber process |

## Known Limitations

- Camera/lidar streaming only — no arbitrary data pub/sub
- Bind to `127.0.0.1` by default — require explicit flag for network exposure
- If ZMQ CURVE auth needed (cross-network), that's a manual configuration step
