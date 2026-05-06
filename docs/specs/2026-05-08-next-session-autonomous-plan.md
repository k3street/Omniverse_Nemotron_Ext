# Next-session autonomous plan (2026-05-08)

You are picking up where the 2026-05-07 session left off. Read
`docs/specs/2026-05-07-vr19-form-function-verification.md` first for
the post-mortem of yesterday's chaos.

The user's ask: do meaningful, isolated work autonomously for ~3-4
hours, without bouncing decisions back. Don't stack changes.
Smoke-test between every phase. Commit per phase. Then report.

## What's already in place (do not redo)

- `scripts/qa/run_cp01.py` and `scripts/qa/run_cp02.py` — persistent
  deterministic test scripts. Build CP-01 / CP-02 scenes via direct
  imports of `execute_tool_call`, no LLM, no orchestrator. Verify
  manually with Stop+Play.
- `workspace/templates/CP-01.json` and `CP-02.json` — canonical
  patterns, code-field has the ground-truth tool-call sequence.
- HEAD = `c73473e` ("chat+ui: model switcher (M button) + Moonshot/Kimi
  provider + task browser") on `feat/live-progress-ui`.
- PR #89 is OPEN against k3street:master. **Do not modify it.** Don't
  push to `fork`. Working branch is `feat/live-progress-ui`, push to
  `anton` only.

## Hard constraints

1. **ONE substantive change per phase.** Smoke-test before the next
   phase begins.
2. **Concrete numbers, not vibes.** "Scripts pass 4/4 cubes-in-bin"
   not "scenes look right."
3. **Stop on failure.** If a phase smoke-test fails, roll back that
   phase's commit, write `docs/specs/2026-05-08-blocker.md` with the
   exact output and what you tried, and wait. Do not proceed to next
   phase. Do not improvise around the failure.
4. **Don't touch the orchestrator.** Yesterday's bug was there. Out
   of scope for this autonomous block.
5. **Don't re-implement strengthened verify_pickplace_pipeline or
   simulate_traversal_check.** Phase 3 builds the smoke-test
   FRAMEWORK so a later session can do that work safely. The
   verifier rebuild is not in scope for THIS session.
6. **Don't run VR-19 against an LLM.** No agent-eval calls. The
   autonomous block is preparation.
7. **Don't switch LLM model or env vars.** Pro+thinking experiment
   is the user's call.
8. **Push to `anton` after each commit.** Never to `fork` or
   `origin`.
9. If Isaac Sim crashes during a smoke-test: stop, do not relaunch,
   write the blocker doc, wait.

---

## Phase 1 — `/mnt/shared_data` ghost hunt (~45 min)

### Why

Yesterday's VR-19 run had the agent calling `run_usd_script` with
`/mnt/shared_data/isaac-sim-assets-complete-5.0.0/.../franka.usd`, a
Kimate-machine path that doesn't exist on our box. The agent kept
re-importing with the same broken path 4 times. The ghost lives
somewhere in our context plumbing and surfaces in retrieval. Until
sanitized, every agent build-task has a chance of hitting it.

### Action

```
grep -c '/mnt/shared_data' workspace/audit.jsonl
grep -rn '/mnt/shared_data' workspace/knowledge/ 2>/dev/null
grep -rn '/mnt/shared_data' workspace/turn_snapshots/ 2>/dev/null | head
```

For `workspace/audit.jsonl` (read-only history): redact in place.
Replace the path with `<sanitized-asset-path>` everywhere. Use a
single `sed -i` pass + a count-after grep to confirm.

For `workspace/knowledge/*.jsonl`: same — these are RAG-indexed by
ChromaDB so the ghost must die there too.

After redaction, rebuild any ChromaDB index that depends on these.
Look for `workspace/tool_index/` or `workspace/knowledge_index/` —
delete and let next service-startup rebuild from scratch.

There are also two existing references inside
`service/isaac_assist_service/chat/tools/tool_executor.py` (in a
comment + as an `_LULA_LIB_PATHS` entry). These are intentional
fallback search paths — leave them alone unless you can confirm they
also leak into agent context.

### Success criterion

```
grep -rn '/mnt/shared_data' workspace/ docs/ scripts/ exts/ | wc -l
```

Should print a small number (≤2 — only the post-mortem doc that
*describes* the past failure). Must NOT include audit.jsonl,
knowledge/*.jsonl, or any indexed source.

### Commit

```
workspace+knowledge: scrub /mnt/shared_data ghost from audit.jsonl + RAG corpus

Yesterday's VR-19 run hit a stale Kimate-machine path
(/mnt/shared_data/isaac-sim-assets-complete-5.0.0/...) that the agent
grabbed from somewhere in our context plumbing — likely the
ChromaDB-indexed audit.jsonl or RAG corpus. Path doesn't exist here,
so every Franka import via run_usd_script failed.

Redacted N occurrences in audit.jsonl, M in knowledge/*.jsonl. Tool
and template-retrieval ChromaDB collections cleared so they rebuild
on next service start.

Two references in tool_executor.py (_LULA_LIB_PATHS fallback + one
comment) are intentional and untouched.
```

---

## Phase 2 — Slim CP-01 + CP-02 `thoughts` (~30 min)

### Why

The user observed that `thoughts` in canonical templates carry
implementation rationale ("cuRobo's _DOWN_Q_BASE constant",
"settling state 8 ticks", "fingertips at 0.94 - 0.105 = 0.835") that
is HOW our code works, not WHAT the agent should do. It dilutes
attention payload. This was bundled into yesterday's verifier-rebuild
and got reverted with everything else. Now do it as an isolated
change with a smoke-test.

### Action

Edit `workspace/templates/CP-01.json` and `CP-02.json` thoughts
fields.

Keep:
- WHAT the agent should do that isn't obvious from `code`
- WHY for the non-obvious choices (one short sentence each)
- Failure-mode-of-this-pattern lessons

Cut:
- Implementation rationale of our internals
- Numerical specifics that come straight from `code`
- Historical context ("verified end-to-end with Anton 2026-05-06" —
  that's what `verified_date` and `verified_metrics` are for)

Aim for ≈50% reduction in word count without losing actionable info.

### Smoke test

```
cd /home/anton/projects/Omniverse_Nemotron_Ext
# Make sure uvicorn + Isaac Sim are up first
python scripts/qa/run_cp01.py
# In Isaac Sim viewport: Stop, then Play.
# Verify: 4 cubes spawn on belt, all 4 land in bin within 60s.
# Then for CP-02:
python scripts/qa/run_cp02.py
# In Isaac Sim: Stop, Play.
# Verify: cube traverses Conv1 → Robot1 → Conv2 → Robot2 → Bin.
```

If you cannot do the visual verification yourself (you usually
can't), that's fine — write the script result + a short note "ran
clean, agent-side scripted output matches CP-01 expectations" in the
commit message. The user will visual-verify in their own pass; if
something regressed they'll roll back.

### Commit

```
templates: slim CP-01 + CP-02 thoughts to agent-actionable rules

Per Anton's 2026-05-07 observation: thoughts field carried
implementation rationale (cuRobo internals, finger geometry,
settling-tick counts) that dilutes attention payload. Sliced to:
WHAT the agent should do, WHY for non-obvious choices, and
failure-mode-of-this-pattern lessons. ~50% word reduction.

scripts/qa/run_cp01.py + run_cp02.py ran clean post-edit (build
phase). Visual cube-delivery verification deferred to user.
```

---

## Phase 3 — Verifier smoke-test framework (~1.5 h)

### Why

Yesterday's verifier work failed because we wired strengthened
checks into `verify_pickplace_pipeline` without first having a
known-good and known-broken scene to validate against. Build the
framework now. Future verifier changes can run against it before
touching production.

This phase does NOT change the production verifier. It only adds
test infrastructure.

### Action

Create `scripts/qa/verifier_smoke_tests.py`:

1. Imports `execute_tool_call` and `kit_tools` like
   `scripts/qa/run_cp01.py` does.
2. Defines two scene-fixture functions:
   - `build_known_good_cp01()` — invokes CP-01's tool-call sequence
     by running scripts/qa/run_cp01.py as a subprocess, OR by
     importing and calling its main coroutine.
   - `build_known_broken_no_conveyor_velocity()` — same as above
     but immediately after, sets the conveyor's `surfaceVelocity`
     attribute to (0,0,0) via a `run_usd_script`-style direct kit
     call. This simulates the "agent built a Cube prim and called it
     conveyor without applying surface velocity" failure mode.
   - `build_known_broken_no_controller()` — builds CP-01 minus the
     final `setup_pick_place_controller` call.
3. For each fixture, calls `execute_tool_call("verify_pickplace_pipeline", {...})` with the right stages
   args and captures the result.
4. Asserts:
   - known_good → `pipeline_ok=true`, `issues=[]` (or empty)
   - known_broken_no_velocity → `pipeline_ok=false` AND issues
     mention either "reach" or "handoff" depending on what the
     current verify checks. Note: production verify doesn't yet
     check conveyor activity — so for a no-velocity scene, current
     verify might still return ok. Document this as expected; the
     framework still asserts that BUILDING the broken scene was
     successful, even if the assertion on issues will be added when
     the strengthened verifier lands.
   - known_broken_no_controller → same nuance.
5. Prints concrete PASS/FAIL summary per fixture with the exact
   issues list.

Important: this framework should EXPOSE that the current verifier
is form-shallow — i.e., known-broken scenes return ok on shallow
checks. That's the case the next verifier-rebuild session needs to
fix. The smoke-test then becomes the regression boundary.

### Success criterion

```
python scripts/qa/verifier_smoke_tests.py
```

Runs to completion (no Python exceptions, no Kit crashes), prints a
table of fixture-name × pipeline_ok × len(issues), and exits 0.

The output should make it OBVIOUS to a future reader that
known-broken scenes pass the current shallow verifier (which is the
known weakness). That's intentional — this is the framework that
proves it.

### Commit

```
scripts: verifier smoke-test framework (known-good CP-01 + 2 known-broken)

Adds scripts/qa/verifier_smoke_tests.py. Three scene fixtures:
- known_good_cp01 (full CP-01 build)
- known_broken_no_conveyor_velocity (CP-01 with surfaceVelocity zero)
- known_broken_no_controller (CP-01 minus setup_pick_place_controller)

Each runs verify_pickplace_pipeline against the built scene and
captures the result. Future verifier-strengthening changes (form
checks for conveyor activity, controller installation, cube-source
bridge) can be regression-tested against these fixtures before
touching production.

Demonstrates that current production verifier is form-shallow —
known-broken scenes pass. Intentional surface — this is the
boundary the next verifier-rebuild session targets.
```

---

## Phase 4 — VR-19 v2 success criterion (doc only, ~30 min)

### Why

Yesterday's VR-19.md was rewritten to require form + function dual
gate. Got reverted with everything else. Re-introduce as
documentation only. No code changes. The spec serves as design
intent for the eventual verifier+simulate work.

### Action

Edit `docs/qa/tasks/VR-19.md`. Replace the success-criterion section
with:

```markdown
**Success criterion — form AND function, both required.**

A scene that passes one but not the other fails the task. Both
gates below must pass.

### FORM (necessary conditions on the built scene)

1. ≥2 robots, ≥2 conveyors, ≥3 stations, ≥1 cube
2. Path from A to C is topologically bridged: every gap between
   source-of-cube and destination is either covered by a conveyor
   with active surface velocity (PhysxSurfaceVelocityAPI applied
   AND non-zero velocity) OR within reach of a robot doing
   pick-place between stages.
3. Each robot reachable to BOTH its pick AND its place target
   (≤ 0.855m horizontal for Franka).
4. Each robot has a controller installed (setup_pick_place_controller
   call). Without it the robot is a static prop.

### FUNCTION (operational test)

The cube's world position at t = 60s after Stop+Play must satisfy:

- cube.x ∈ Station_C.bbox.x_range
- cube.y ∈ Station_C.bbox.y_range
- cube.z ≥ Station_C.bbox.z_min - 0.10m (didn't fall through)
- |cube.velocity| < 0.05 m/s (came to rest)

Form-pass without function-pass = "looks correct, doesn't function"
— the canonical failure VR-19 catches.

Function-pass without form-pass = teleport hack — also fail.

### Agent-discipline note

If you build something that fails verify, surface the issues
honestly. Do NOT silently rebuild the scene from scratch — that's
thrash, not reasoning. Acknowledge the specific issue, fix the
specific cause, re-verify.
```

Keep the rest of VR-19.md as-is. This is a doc-only edit.

### No smoke-test required

It's a markdown file. Confirm with `git diff docs/qa/tasks/VR-19.md`
that the change is what you intended.

### Commit

```
docs: VR-19 success criterion — form + function dual gate (spec, no code)

Re-introduces yesterday's reverted form+function dual-gate design
as documentation only. No code or tool changes. The spec serves as
target design for the eventual verifier+simulate rebuild.

Both gates must pass: form (≥counts, topological bridge, reach,
controller installed) AND function (cube ends in Station_C bbox at
t=60s, came to rest, didn't fall through).

Adds an explicit agent-discipline note: if verify fails, surface
issues honestly — don't rebuild from scratch.
```

---

## After autonomous block: report back

When all four phases land cleanly, write a single message to the
user (≤ 200 words) covering:

1. **Phases done** — 4 commit hashes, smoke-test results per phase.
2. **`/mnt/shared_data` cleanup specifics** — count before/after,
   files touched, what was sanitized vs left alone.
3. **Thoughts-slim subjective notes** — anything you cut and
   weren't 100% sure was safe to remove (so user can spot-check).
4. **Smoke-test framework usage** — one-liner for the user to use it
   next time.
5. **What next session should decide on** — e.g., "Pro+thinking
   experiment? Verifier rebuild now that smoke-tests exist? Move to
   step 2 of the 5-step plan (sortering)?"

Don't push to fork. Don't open PR. Just commit + push to anton.

## What to do if the autonomous block can't be completed

If Phase 1 fails (audit.jsonl scrub broke something or path is in
too many places to safely batch-redact):
- Stop. Don't continue with Phase 2-4.
- Write `docs/specs/2026-05-08-blocker.md` with concrete output.
- Wait.

If any phase's smoke-test fails:
- `git reset --hard HEAD~1` (un-commit that phase)
- Stop. Don't continue.
- Write the blocker doc.
- Wait.

If you hit a model timeout or service crash:
- Stop. Don't relaunch services aggressively. Wait.

## Success looks like

- 4 commits on `feat/live-progress-ui`, pushed to `anton`
- All 4 commits' smoke-tests passed concretely (with numbers in
  commit messages)
- One short report to user
- Repo state strictly better than starting state
- No regressions to CP-01 or CP-02 (their scripts still pass 4/4
  cubes-in-bin)
- The user picks up next session knowing exactly where to go
