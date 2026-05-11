# Session notebook — slash commands for working-day flow

*Built 2026-04-19 to stop insights scrolling away mid-session.*

When you're in the middle of a task, you want to capture things without stopping. These slash commands run locally (no LLM, no latency, no cost) and write to a per-session trace you can turn into a report later.

## Commands

### `/note <observation>`
Attach a free-form note to the session trace.

```
/note PhysX determinism is hardware-dependent — flag for PM in safety case
```

Use for facts worth remembering, insights, gotchas. Returns an ack; the agent doesn't try to "help" with your note.

### `/block <reason>`
Mark the session as blocked. Use when you're stuck and want to come back — or share with the team.

```
/block rmw_init fails without AMENT_PREFIX_PATH — can't test ROS2 until Isaac Sim is relaunched from a sourced terminal
```

Sessions with blockers are flagged so `/stuck` and `/report` can surface them.

### `/pin` / `/pin <text>`
Pin an artifact. With no argument, pins the most recent assistant reply (useful right after you got a good cite or code snippet). With text, pins that exact text.

```
/pin
/pin enable_deterministic_mode(seed=42, physics_dt=1/60)
```

Pinned content survives to `/report` and shows in the session trace summary.

### `/cite <topic>`
Pull a ready-to-paste paragraph from the deprecations corpus. The agent quotes the correct API names verbatim — no hallucinated migration paths.

```
/cite deterministic
/cite ros2 namespace
/cite urdf importer
/cite replicator randomizer
/cite isaac core namespace
```

Topics in the corpus today: `deterministic_replay`, `ros2_bridge_namespace`, `urdf_import_api`, `articulation_tensor_view`, `isaac_core_namespace`, `replicator_dr_api`, `articulation_move_off_root`.

Missing a topic? Add a row to `service/isaac_assist_service/knowledge/deprecations.jsonl`.

### `/help`
Print the command list inline.

## What gets traced

Every session (whether you use slash commands or not) writes a JSONL trace to `workspace/session_traces/{session_id}.jsonl`. Event types:

| Type | When |
|---|---|
| `user_msg` | every message you send |
| `agent_reply` | every assistant reply (truncated to 500 chars in the trace) |
| `tool_call` | each tool the agent invoked + success/fail |
| `note` | `/note` |
| `block` | `/block` |
| `pin` | `/pin` |
| `cite_returned` / `cite_miss` | `/cite` outcomes |

Read a session's trace programmatically:

```python
from service.isaac_assist_service.chat.session_trace import read_trace, trace_summary
events = read_trace("my_session_id")
summary = trace_summary("my_session_id")
# summary includes: event_count, notes, blocks, pins, has_blockers, duration_s
```

## Not built yet (planned)

- `/stuck` — auto-generate a structured help-request: last N turns + errors + blockers + agent's best guess at root cause, copy-pasteable to Slack / Jira.
- `/report` — end-of-day Markdown rollup across all your sessions.
- `/resume <session_id>` — reload yesterday's session context.
- `/find <query>` — search across all your trace files.

These are planned but intentionally deferred until real usage surfaces which shape they need.

## Design note

All slash commands bypass the LLM entirely. They write to disk, inspect session state, and return a shaped reply. They are:
- **Free** — no tokens spent
- **Fast** — sub-second
- **Deterministic** — same input, same output, same trace
- **Inspectable** — the JSONL trace is your audit log

If a command has a typo or unknown keyword (e.g. `/unknown foo`), it falls through to the normal LLM pipeline — no silent swallow.
