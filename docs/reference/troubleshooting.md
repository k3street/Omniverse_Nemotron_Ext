# Troubleshooting

Common problems and their solutions. If your issue is not listed here, ask Isaac Assist: _"Why is [thing] not working?"_ -- the debugging tools can often diagnose problems automatically.

---

## Service Won't Start

### Port 8000 already in use

```
ERROR: [Errno 98] Address already in use
```

Another process is using port 8000. Find and stop it:

```bash
lsof -i :8000
kill <PID>
```

Or start the service on a different port:

```bash
python -m service.isaac_assist_service.main --port 8080
```

### Missing dependencies

```
ModuleNotFoundError: No module named 'fastapi'
```

Install the project requirements:

```bash
pip install -r requirements.txt
```

### Bad `.env` file

If the service starts but the LLM does not respond, check your `.env` configuration:

```bash
# Verify the service reads your config
curl http://localhost:8000/health
```

The `/health` endpoint returns the active `llm_mode` and model name. If these are wrong, check that your `.env.local` file exists and has the correct values. See [Configuration](configuration.md) for details.

---

## Isaac Sim Won't Connect

### Kit RPC server not running

```
Failed to communicate with service
```

The FastAPI service communicates with Isaac Sim through a Kit RPC server on port 8001. This server is started by the `omni.isaac.assist` extension inside Isaac Sim.

1. Verify the extension is enabled: **Window > Extensions** and search for "isaac assist".
2. Check the Isaac Sim console for errors related to the extension startup.
3. Verify port 8001 is listening:

```bash
curl http://localhost:8001/health
```

### Wrong port

If Isaac Sim's Kit RPC is running on a non-default port, the service cannot find it. The default is `8001`. Check the extension logs in the Isaac Sim console for the actual port number.

---

## "Failed to communicate with service" in Chat

This means the chat UI extension inside Isaac Sim cannot reach the FastAPI service on port 8000.

1. **Is the service running?** Check with `curl http://localhost:8000/health`.
2. **Firewall?** If running the service on a remote machine, ensure port 8000 is open.
3. **Correct host?** The extension defaults to `http://localhost:8000`. If your service is elsewhere, update the extension configuration.

---

## Robot Falls Through Floor

!!! tip "Quick fix"
    Ask Isaac Assist: _"Why is the robot falling through the floor?"_

The most common cause is missing collision geometry on the ground plane or the robot.

**Checklist:**

- [ ] Ground plane exists: _"Create a ground plane"_ or verify `/World/GroundPlane` is present.
- [ ] Ground plane has `PhysicsCollisionAPI` applied.
- [ ] Robot base links have collision meshes. Run: _"Check collisions on /World/Franka"_.
- [ ] If the robot was imported from URDF, collision meshes may need to be regenerated. Try re-importing with collision enabled.

---

## Physics Unstable

Symptoms: objects jitter, explode, or pass through each other at high speed.

### Solver iterations too low

The default PhysX solver may not have enough iterations for complex articulations:

```
"Set solver iterations to 32"
```

### Timestep too large

A large timestep causes inaccurate collision detection:

```
"Set the physics timestep to 1/120"
```

!!! note "Rule of thumb"
    For stable robot simulation, use a timestep of `1/120` (0.00833s) or smaller and at least 16 solver iterations.

### Objects interpenetrating at start

If objects overlap when the simulation starts, PhysX will push them apart violently. Ensure objects have clearance before pressing Play.

---

## LLM Returns Bad Code

The generated code patch does not compile or does the wrong thing.

1. **Reject the patch** -- click Reject in the approval dialog.
2. **Try a different model** -- switch to a more capable model:
   ```
   "Switch to Claude for this"
   ```
3. **Be more specific** -- provide the prim paths, exact values, and context the LLM needs.
4. **Report the issue** -- the service logs every tool invocation to `workspace/finetune_exports/` for later analysis.

---

## ROS2 Topics Don't Appear

### Rosbridge not running

The ROS2 tools communicate through rosbridge WebSocket. Start it:

```bash
ros2 launch rosbridge_server rosbridge_websocket_launch.xml
```

Then verify connectivity:

```
"Connect to rosbridge"
```

### OmniGraph not wired correctly

If you created a ROS2 OmniGraph but topics are not publishing:

1. **Check the simulation is running** -- ROS2 graphs only publish during Play.
2. **Verify node types** -- Isaac Sim uses specific OmniGraph node type IDs (e.g., `omni.isaac.ros2_bridge.ROS2PublishClock`). A wrong node type will silently fail.
3. **List nodes in the graph**: _"List all prims under /World/ActionGraph"_.

---

## GPU Out of Memory

```
CUDA out of memory
```

- **Reduce clone count** -- if using GPU-batched cloning for RL, lower `num_envs`.
- **Lower viewport resolution** -- high-resolution viewports consume significant VRAM.
- **Close other GPU applications** -- browser tabs with WebGL, other simulations, training jobs.
- **Check SDG resolution** -- Replicator output resolution affects VRAM usage. Start with 640x480.

---

## Extension Not Visible in Isaac Sim

The `omni.isaac.assist` extension does not appear in the Extensions window.

1. **Check the extension search path**: In Isaac Sim, go to **Window > Extensions > Settings (gear icon)** and add the path to `exts/` in this repository.
2. **Verify `extension.toml`**: Ensure the `exts/omni.isaac.assist/` directory contains a valid `extension.toml`.
3. **Isaac Sim version mismatch**: The extension targets Isaac Sim 5.1 and 6.0. Older versions are not supported.
4. **Restart Isaac Sim**: Some extension path changes require a full restart to take effect.

---

## Still Stuck?

Run the built-in diagnostics:

```
"Get debug info"
"Check for console errors"
"Show me the scene summary"
```

These tools collect FPS, GPU stats, physics errors, and scene state -- often enough to identify the root cause.
