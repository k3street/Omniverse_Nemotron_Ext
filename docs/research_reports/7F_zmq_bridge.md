# Phase 7F — IsaacSimZMQ Bridge: Assessment

**Agent:** Research 7F ZMQ Bridge  
**Date:** 2026-04-15  
**Status:** Complete

## Verdict: Drop or Minimize

### Redundancy with ROS2

Near-complete functional overlap with Phase 4B/8F. ROS2 bridge already has working code.

### Threading Problem

Kit runs single-threaded Python. Blocking `recv()` freezes sim. `zmq.asyncio` conflicts with Kit's event loop. The spec does not address this.

### When ZMQ Is Actually Better

Only for high-frequency GPU-accelerated sensor streaming to a local Python training script without ROS2. The C++ `OgnIsaacBridgeZMQNode` does this — but the spec describes Python-level tools.

### Recommendation

**Option A:** Drop entirely. Rely on 4B/8F for external comms.

**Option B:** Narrow to single task: `configure_zmq_camera_stream` using NVIDIA's existing C++ OmniGraph node.

## Sources
- [isaac-sim/IsaacSimZMQ](https://github.com/isaac-sim/IsaacSimZMQ)
- [ZeroMQ vs eProsima Fast DDS](https://www.eprosima.com/index.php/resources-all/performance/zmq-vs-eprosima-fast-rtps)
