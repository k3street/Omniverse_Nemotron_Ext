# Phase 7C Addendum — Teleoperation Quality

**For:** The session building Phase 7C (XR teleoperation + HDF5 demo recording)
**Priority:** Add before `start_teleop_session` ships — these are L0 utilities
that keep demo-collection sessions safe, diagnosable, and reproducible.
**Effort:** Small — five tool handlers, no new Kit RPC endpoint.

---

## Motivation

The Phase 7C tools (`start_teleop_session`, `configure_teleop_mapping`,
`record_teleop_demo`) orbit three distinct failure modes that no existing tool
covers:

1. **Silent input drops** — a Quest 3 on Wi-Fi goes idle for 600 ms and the
   robot keeps pushing the last command into physics; the user can't tell
   whether the robot or the link is at fault.
2. **Unreviewable demos** — recorded HDF5 files collect on disk without any
   schema check, so fine-tune jobs in Phase 7G fail 40 minutes in when a
   corrupt episode hits.
3. **Unshareable setups** — retargeting YAML lives inside IsaacTeleop's config
   tree; users can't diff or re-apply the mapping they used yesterday.

All five new tools are pure data / code-gen — no Kit RPC, no subprocess, no
network. They sit in front of the heavy-weight Phase 7C tools the same way
the 7G addendum sits in front of `load_groot_policy`.

---

## Tools

### 7C-A.1 `check_teleop_hardware(device)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Look up the requested `device` (`quest_3`, `vision_pro`, `spacemouse`,
   `keyboard`) in a module-level table of known latency / bandwidth / transport
   requirements.
2. For `quest_3`: probe the local network MTU and loopback RTT. For
   `spacemouse` / `keyboard`: probe `/dev/input` on Linux, fall back to
   platform-default when absent.
3. Return per-device verdicts: `supported`, `latency_budget_ms`,
   `transport` (`webxr`, `cloudxr`, `usb-hid`), plus a `notes` string.

**Returns:**
```python
{
    "device": "quest_3",
    "supported": True,
    "transport": "webxr",
    "latency_budget_ms": 80,
    "known_limitations": ["Vision Pro requires CloudXR native app"],
    "notes": "Quest 3 uses WebXR over Wi-Fi — keep router <= 10 ms from host.",
}
```

**Why DATA, not CODE_GEN:** the LLM needs the numbers before it decides
whether to call `start_teleop_session`. Users routinely ask "will my Vision
Pro work over the browser?" — the answer is a lookup, not code.

### 7C-A.2 `validate_teleop_demo(hdf5_path)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Open the HDF5 file with `h5py` (soft import — missing `h5py` returns
   `{"available": False}`).
2. Walk the robomimic schema: `data/demo_*/actions`, `.../obs/...`,
   `.../states`, `mask/`, plus root attrs `env_args`, `total`.
3. For each demo, check: action shape is rank-2, obs keys aren't empty,
   episode length > 0, no NaN / Inf in actions.
4. Return a per-demo report plus aggregate pass/fail.

**Returns:**
```python
{
    "available": True,
    "path": "/workspace/demos/pick.hdf5",
    "demos_checked": 12,
    "demos_ok": 11,
    "issues": [
        {"demo": "demo_7", "problem": "NaN in actions at step 42"},
    ],
    "total_transitions": 8421,
    "ready_for_training": False,
}
```

**Why DATA:** the result must come back to the LLM in-context so it can
decide whether to trigger `finetune_groot` (Phase 7G) or ask the user to
re-record.

### 7C-A.3 `export_teleop_mapping(session_name, device, joint_map, gains)`

**Type:** CODE_GEN handler (returns a YAML-producing Python script).

**Output:** A standalone script that, when executed, writes a single YAML
file under `workspace/teleop_mappings/<session_name>.yaml`. The YAML follows
the IsaacTeleop / dex-retargeting config shape:

```yaml
robot: franka_panda
device: quest_3
joints:
  - name: panda_joint1
    source: right_thumb_cmc_yaw
    gain: 1.0
    limit_rad: [-2.8, 2.8]
gains:
  position: 400
  velocity: 40
```

The script must `print(f"Wrote mapping to {path}")` on success so the
`tool_result` loop can confirm.

**Why CODE_GEN:** the mapping has to land on disk as a file the user can
diff, commit, and re-feed to `configure_teleop_mapping`. The Kit process
should never write user-owned config — that's architecturally wrong.

### 7C-A.4 `generate_teleop_watchdog_script(timeout_ms, hold_time_ms, robot_path)`

**Type:** CODE_GEN handler (returns a Python script).

**Output:** A runnable script the user can paste into the Isaac Sim Script
Editor (or queue via patch approval). The script:

1. Subscribes to the teleop control WebSocket topic.
2. Timestamps every incoming message.
3. On a timer callback, if `now - last_msg_ts > timeout_ms`, holds the last
   command for `hold_time_ms`, then zeros all joint velocity targets on
   `robot_path`.
4. Prints `[watchdog] armed` / `[watchdog] timeout` / `[watchdog] zeroed`
   to the console for observability.

**Why CODE_GEN:** the watchdog is a long-lived user process, not a one-shot
call. It belongs in the user's committed script, not inside the LLM's
tool-call loop.

### 7C-A.5 `summarize_teleop_session(hdf5_path)`

**Type:** DATA handler (no code gen).

**Logic:**

1. Same soft `h5py` import as 7C-A.2.
2. Per demo, compute: duration (len / fps), mean & max joint-velocity
   magnitude, mean action magnitude, wall-clock range from `timestamps`
   if present.
3. Aggregate across demos: total duration, total transitions, per-joint
   usage histograms (min / max / mean per joint).
4. Return a compact summary the LLM can fold into a user-facing answer
   like "You recorded 12 demos over 18 minutes; joint 4 sees 3× the motion
   of joint 6."

**Returns:**
```python
{
    "available": True,
    "path": "/workspace/demos/pick.hdf5",
    "demos": 12,
    "total_duration_s": 1089.4,
    "total_transitions": 8421,
    "per_joint": [
        {"joint": 0, "vel_mean": 0.12, "vel_max": 0.89, "range_rad": 2.1},
        ...
    ],
    "fps": 30,
}
```

**Why DATA:** pure read-only analysis. The LLM uses it for
human-readable answers and to seed `finetune_groot` decisions.

---

## Code patterns

- `check_teleop_hardware` uses a module-level constant table of devices and
  transports. Probe code (MTU, `/dev/input`) is guarded with `try/except`
  and a 1-second timeout, with sensible defaults for unreachable probes.
- `validate_teleop_demo` / `summarize_teleop_session` share an
  `_open_hdf5_safely(path)` helper that returns `(None, reason)` on import
  failure or missing file, so handlers never raise into the tool loop.
- `export_teleop_mapping` / `generate_teleop_watchdog_script` follow the
  existing code-gen pattern (`_gen_*` returning `str` of Python source).
  Use `repr()` for user-supplied path / string literals to avoid injection.
- Register under `DATA_HANDLERS` / `CODE_GEN_HANDLERS` at the end of
  `tool_executor.py`, mirroring the Phase 7A and 7G addendum layout.

---

## Schemas (tool_schemas.py)

Five entries appended to `ISAAC_SIM_TOOLS`, under a header comment:

```python
# ─── Phase 7C Addendum: Teleoperation Quality ────────────────────────────
```

All five are `type: function` entries with required-args enforcement.

---

## Test Strategy

| Test                                                       | Level | What                                                      |
|-----------------------------------------------------------|-------|-----------------------------------------------------------|
| `check_teleop_hardware` — quest_3                         | L0    | Returns `supported=True`, `transport=webxr`               |
| `check_teleop_hardware` — vision_pro                      | L0    | Returns CloudXR note and latency_budget=60                |
| `check_teleop_hardware` — unknown device                  | L0    | Returns `supported=False`                                 |
| `validate_teleop_demo` — missing h5py                     | L0    | Monkeypatch import — returns `available=False`            |
| `validate_teleop_demo` — missing file                     | L0    | Returns available=True, issues list non-empty             |
| `validate_teleop_demo` — good HDF5 (built via h5py)       | L0    | Returns `ready_for_training=True`                         |
| `validate_teleop_demo` — HDF5 with NaN action             | L0    | Flags the offending demo with NaN message                 |
| `summarize_teleop_session` — missing h5py                 | L0    | Returns `available=False` with reason                     |
| `summarize_teleop_session` — 2 demos, 30 fps              | L0    | Returns correct `total_duration_s` and per-joint stats    |
| `export_teleop_mapping` — compiles                        | L0    | `compile()` success + writes expected YAML fields         |
| `export_teleop_mapping` — path injection safe             | L0    | `repr()` used, path with quote doesn't break syntax       |
| `generate_teleop_watchdog_script` — compiles              | L0    | `compile()` success + references `robot_path`             |
| `generate_teleop_watchdog_script` — custom timings        | L0    | Timeout and hold values appear in the generated code      |

All thirteen tests are L0 — no Kit, no XR device, no network.

---

## Known Limitations

- `validate_teleop_demo` checks schema but not semantic correctness (e.g.
  whether actions drive the intended pose). A Phase 7G follow-up can add
  visual replay verification.
- `check_teleop_hardware` cannot detect a misconfigured Quest Link cable —
  USB-C detection would require platform-specific ioctl access.
- `generate_teleop_watchdog_script` assumes the teleop socket path is
  `/ws/teleop`; if Phase 7C renames the endpoint, bump the default.
