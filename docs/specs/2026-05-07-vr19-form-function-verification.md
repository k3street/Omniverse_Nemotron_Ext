# VR-19 Form+Function Verification — spec & post-mortem (2026-05-07)

## What we're building

Isaac Assist is an Omniverse Kit extension that lets a user describe a
robotics task in natural language and have an LLM agent build the scene
+ wire up the mechanism (conveyors, robots, controllers, sensors)
through a typed-tool API.

The agent's tool stack has three classes:

- **Resolvers** translate input strings to structured values
  (`resolve_size_adjective`, `resolve_prim_reference`,
  `resolve_skill_composition`, etc.)
- **Builder tools** mutate the stage (`create_prim`, `robot_wizard`,
  `create_conveyor`, `setup_pick_place_controller`, …)
- **Verifiers** check that a built scene meets the goal
  (`verify_pickplace_pipeline` is the pilot)

We have two verified canonical patterns:

- **CP-01** — single-robot pick-place from belt into a bin. 4/4 cube
  delivery verified, ~90% per-attempt grip rate.
- **CP-02** — multi-station assembly: 2 robots, 2 conveyors, 1 cube
  transit. Cube goes Conv1 → Robot 1 → Conv2 → Robot 2 → Bin. Verified
  end-to-end via deterministic test scripts (`/tmp/run_cp01.py`,
  `/tmp/run_cp02.py`).

Both rely on cuRobo for motion planning, with PhysX surface velocity
on conveyors and rubber friction material for grip.

## What we want to achieve

VR-19 is a hard agent-eval that should fail any agent which:

1. Doesn't reason about what variables the under-specified prompt
   leaves implicit (where exactly are A/B/C? what mechanism transports
   the cube?). Variable-resolver discipline.
2. Doesn't follow the canonical patterns (CP-01, CP-02) when they're
   retrieved as top-3 hits.
3. Builds prims that *look* correct on a static check but don't
   actually function — the "looks-correct, doesn't function" failure.
4. Claims success without calling the verifier.
5. Calls the verifier, gets issues, and either ignores them or
   silently fixes without honest acknowledgement.

The VR-19 success criterion as it stands today (HEAD: c73473e) checks
form-only:

- ≥2 robots, ≥2 conveyors, ≥2 bins, ≥1 cube
- Agent called `verify_pickplace_pipeline` at least once
- pipeline_ok=True AND reply says done, OR pipeline_ok=False AND reply
  surfaces issues honestly

This is the "introspection probe" — does the agent use the verifier?

## What happened in this session (2026-05-06 → 07)

### Diagnosis

We ran VR-19 against Gemini 3 Flash. The agent passed the literal
success criterion but the build was a fake:

- 4 errors on screen from `run_usd_script` with a stale Kimate path
  (`/mnt/shared_data/.../franka.usd`) that doesn't exist on our machine.
- Robots never imported; subsequent `anchor_robot` and
  `setup_pick_place_controller` calls hit invalid prims.
- Conveyors created with surface velocity but cube was placed at z=0
  (no rigid body), couldn't ride them.
- Agent called `verify_pickplace_pipeline` (introspection probe ✓) but
  the verifier only checked reach + handoff distance — both passed
  because reach math doesn't care if a robot is actually a real prim
  with controllers, or if the conveyor moves anything.
- Agent then claimed "build is complete". Honesty test technically
  passed because verify said pipeline_ok=true, but only because the
  verifier was too weak to flag the real issues.

So the introspection mechanic worked. The verifier was too shallow.
And the agent's strategy was thrash-rebuild instead of reason-and-fix.

### What I tried (not committed — see "Reverted" below)

A. **Strengthened `verify_pickplace_pipeline`** with form-checks:
   - `conveyor_active`: between every handoff > 0.30m, look for a prim
     with PhysxSurfaceVelocityAPI applied AND non-zero velocity whose
     bbox xy intersects the segment between place and pick.
   - `controller_installed`: check `builtins` for a per-robot subscription
     (`_curobo_pp_sub_<tag>`, `_native_pp_sub`, etc.). Without it the
     robot is a static prop.
   - `cube_source_bridged`: if `cube_path` given, check the cube is at
     the first pick zone OR on an active conveyor that bridges into it.

B. **Added `simulate_traversal_check`** as a function-gate tool. Stops
   timeline, plays for `duration_s` of sim time, captures cube
   start/end pos+velocity, compares to target bbox. Returns
   success=true ONLY if cube ended inside target xy AND above target
   floor AND came to rest.

C. **Added the new tools to `_ALWAYS_TOOLS`** so they survive
   retrieval pruning.

D. **Updated CP-01.json and CP-02.json** to model the full lifecycle:
   added verify+simulate calls at the end of the `code` field, slimmed
   `thoughts` from 12-13 implementation-detail bullets down to 8-9
   agent-actionable rules.

E. **Added an orchestrator gate** ("anti-build-without-verify") that,
   when an agent that built mechanism prims emits a done-claim
   without having called verify+simulate, injects a system reminder
   and gives the agent another round to verify before the final
   reply.

F. **Rewrote VR-19's success criterion** to require BOTH form
   (active conveyors, controllers installed, source bridged) AND
   function (cube actually arrives in target bbox at t=60s).

### What went wrong

1. The orchestrator gate had a Python scoping bug: I wrote
   `from .tools.tool_executor import execute_tool_call` inside the
   gate's nested try-block. Python bound `execute_tool_call` as a
   local for the entire `handle_message` function, so the earlier
   reference at line 980 hit `UnboundLocalError`. Every chat request
   that reached a tool call returned 500.
2. After fixing (1), VR-19 ran further but the agent's response to
   verifier issues was thrash: when reach failed, it moved everything
   to z=0.8; when robots weren't imported, it re-ran the same
   `run_usd_script` with the same broken `/mnt/shared_data` path, four
   times in a row. The simulate_traversal_check ran four times back
   to back (each blocking 60s of sim) on an increasingly corrupted
   scene. Isaac Sim eventually crashed.
3. The stale `/mnt/shared_data/.../franka.usd` path is somewhere the
   agent keeps drawing from. We previously scrubbed `knowledge_*.jsonl`
   but `workspace/audit.jsonl` still has it from earlier sessions, and
   ChromaDB-indexed contexts may surface it. Worth hunting.
4. I also reported a "big breakthrough" prematurely — the agent was
   calling verify+simulate (the behaviour we asked for) but the build
   itself was garbage. Form of behaviour ≠ quality of result.

## Reverted from this session

Per Anton's instruction, all uncommitted changes from today's VR-19
work were reverted. The repository is back at HEAD = `c73473e` ("chat+ui:
model switcher (M button) + Moonshot/Kimi provider + task browser").

Files reverted:

- `service/isaac_assist_service/chat/tools/tool_executor.py`
  (verify_pickplace_pipeline strengthening + simulate_traversal_check
  handler addition)
- `service/isaac_assist_service/chat/tools/tool_schemas.py`
  (simulate_traversal_check schema + verify desc update)
- `service/isaac_assist_service/chat/context_distiller.py`
  (`simulate_traversal_check` in `_ALWAYS_TOOLS`)
- `service/isaac_assist_service/chat/orchestrator.py`
  (anti-build-without-verify gate + logger.exception in error path)
- `service/isaac_assist_service/chat/routes.py`
  (logger.exception on 500 — defensive only, but reverted for cleanliness)
- `workspace/templates/CP-01.json` (verify+simulate at end of code, slimmed thoughts)
- `workspace/templates/CP-02.json` (same)
- `docs/qa/tasks/VR-19.md` (form+function rewrite, variable-resolver
  discipline section)

CP-01 and CP-02 deterministic test scripts at `/tmp/run_cp01.py` and
`/tmp/run_cp02.py` should still work — none of the reverted changes
affected the production verifier or the cuRobo handler beyond what was
in the working commits c921239 / abb6673.

## What's still in the repo from this session (committed earlier)

- `c73473e` — model switcher "M" button, Moonshot/Kimi provider, task
  browser at port 8090. Untouched by today's revert. PR #89 is still
  open against k3street.

## Where the next session should resume

The hypothesis to test next:

> A strengthened `verify_pickplace_pipeline` (form: active conveyor,
> installed controller, source bridge) plus a separate
> `simulate_traversal_check` (function: 60s sim, cube reaches target)
> are both needed before a build can claim done. The orchestrator
> should hard-gate done-claims through these.

To validate the hypothesis without breaking CP-01/02, the safer order
is:

1. **First**, write smoke tests for both verifiers against a known-good
   scene (replica of CP-01) and a known-broken scene (no conveyor, no
   controller). Both should pass: known-good returns ok; known-broken
   returns specific issues.

2. **Only then**, re-introduce the strengthened verifiers. Test once
   on CP-01 and CP-02 to confirm no regression on the canonicals
   themselves.

3. **Then** re-introduce the orchestrator gate, paying attention to
   Python scoping (no nested function-level imports of names also
   used at module top).

4. **Then** rewrite VR-19 with the form+function dual gate.

5. **Then** chase the `/mnt/shared_data` ghost. Search:
   - `workspace/audit.jsonl` (rename or move out of ChromaDB index path)
   - `workspace/knowledge/*.jsonl`
   - any code-pattern auto-learned entries
   - any v5.1.0 RAG corpus files

6. **Only then** run VR-19 again against Gemini.

## Open architectural questions

- The agent's response-to-failure strategy is thrash-rebuild, not
  reason-and-fix. Even with strong verifiers giving precise issues,
  the agent re-runs the same broken plan. Whether this is fixable in
  the prompt vs. requires a different model is an open question.
- The Form+Function verification pattern is reusable beyond
  pick-place. Static-layout, controller-setup, data-pipeline tasks
  could each grow their own form+function verifier pair.
- `_ALWAYS_TOOLS` is becoming a dumping ground for "tools I want the
  agent to always see." If it grows past ~20 entries we should
  reconsider whether retrieval-only is still the right architecture.

## Status snapshot at end of this session

- HEAD = `c73473e`, working tree clean (re. tracked files)
- uvicorn running on :8000 with current HEAD's code
- Isaac Sim crashed during the last VR-19 run; needs restart in next session
- task browser at :8090 still running, may need restart in next session
- CP-01 and CP-02 NOT re-verified after today's reverts (but reverts
  put us back to a verified commit, so should still work)
- VR-19 still fails its current criterion when run against Gemini —
  agent thrashes on `/mnt/shared_data` path, stale Kimate ghost
- PR #89 still open at https://github.com/k3street/Omniverse_Nemotron_Ext/pull/89
