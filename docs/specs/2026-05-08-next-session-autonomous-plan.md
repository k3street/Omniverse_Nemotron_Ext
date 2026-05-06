# Next-session plan — 5-step roadmap with form+function dual gate

You are picking up from the 2026-05-07 session. Read
`docs/specs/2026-05-07-vr19-form-function-verification.md` first for
the post-mortem of yesterday's chaos.

---

## What the user wants

Anton's vision: type a free-form prompt into Isaac Assist
("bygg en sorteringsstation, röda kuber i röd bin, blå i blå") and
have the agent build a working scene. The 5-step QA roadmap (VR-19,
sortering, constraint, re-orient, multi-modal) is the
generalization-test — does the same form+function architecture
handle every typed-variable category we care about?

End-state worth aiming for: five verified canonical patterns
(CP-01..05), four task specs (VR-19, SORT-01, CONSTRAINT-01,
REORIENT-01) with goal-aligned success criteria, an agent that
follows them or surfaces issues honestly when it can't.

That's the legacy.

---

## What's already in place

- `scripts/qa/run_cp01.py` and `run_cp02.py` — persistent
  deterministic test scripts. Build the verified canonical scenes
  via direct `execute_tool_call` imports. No LLM, no orchestrator.
- `workspace/templates/CP-01.json` and `CP-02.json` — canonical
  patterns. The `code` field is the ground-truth tool-call sequence.
- `verify_pickplace_pipeline` tool exists, but is form-shallow (only
  reach + handoff distance — passes scenes that are mechanically
  broken).
- HEAD = `c73473e` on `feat/live-progress-ui`. PR #89 is open against
  k3street:master, do NOT modify it. Working branch only. Push to
  `anton` remote.

---

## What yesterday taught us — embedded in this plan

1. **Procedural compliance is not task success.** Counting tool calls
   ("agent called verify") doesn't probe whether the cube actually
   arrived. Every task spec in this plan uses a form + function dual
   gate.
2. **Agentic iteration on verify-feedback is brittle.** When the
   agent gets "reach failed" it thrashes (rebuild from scratch)
   instead of reasoning (move robot 0.5m). This may be a
   model-behavior limit, not a tool-design fix.
3. **For task shapes that match a canonical, plan-then-execute
   beats agentic-iteration.** Build the canonical first
   (deterministic). Validate. THEN run as agent-eval. Canonical
   proves the BUILD is achievable. Agent-eval tests if the AGENT can
   do it. Two separate things; don't conflate.
4. **Stale data poisons retrieval.** `/mnt/shared_data` ghost in
   `audit.jsonl` and `workspace/knowledge/*.jsonl` surfaces in every
   agent run. Sanitize before any agent-eval.
5. **One change at a time.** Yesterday's cascade was four
   simultaneous edits. Root-cause isolation became impossible.
   Smoke-test between every change.

---

## The 5-step roadmap (improved)

Each step has the same shape:
- A canonical template (CP-N) — built first via deterministic
  script. Proves the BUILD is achievable.
- A task spec (VR-19, SORT-01, …) — form + function success criteria.
- An agent-eval run against the spec — only after canonical is
  verified.
- Honest analysis — pass/fail with concrete numbers.

### Step 1 — VR-19: assembly line, cube traverses A → C

Probes: introspection (does the agent verify?) + form+function dual
gate at the simplest geometry.

- **Canonical:** CP-02 (already verified — cube traverses
  Conv1 → Robot1 → Conv2 → Robot2 → Bin).
- **Form gate:** ≥2 robots, ≥2 conveyors, ≥3 stations, ≥1 cube;
  topological bridge from cube source to destination; reach OK
  (≤0.855m for Franka); each robot has a controller installed.
- **Function gate:** at t=60s after Stop+Play —
  - cube xy ∈ Station_C bbox
  - cube z ≥ Station_C floor − 0.10m
  - |cube velocity| < 0.05 m/s
- **Tooling needed:** strengthened `verify_pickplace_pipeline` +
  new `simulate_traversal_check`.

### Step 2 — SORT-01: color-routed sorting

Probes: typed-resolver for color, conditional logic, multi-cube
scheduling.

- **Canonical:** CP-03 (NEW). One robot, one belt, two bins (red and
  blue). Two cubes (red, blue) on belt. Robot picks each, routes by
  color.
- **Form gate:** ≥1 robot, ≥1 conveyor, ≥2 bins (each with a
  distinct visual + physics material binding), ≥2 cubes (one of
  each color); controllers installed; bins reach-OK.
- **Function gate:** at t=90s — every red cube in red_bin xy, every
  blue cube in blue_bin xy, all at rest.
- **Open architectural question for canonical design:** is colour
  routing a controller-arg (`color_routing={"red": "/World/RedBin",
  "blue": "/World/BlueBin"}`) or a separate
  `resolve_color_routing` typed-resolver the agent calls and uses to
  parameterise two `setup_pick_place_controller` calls? Discuss
  with user before implementing.

### Step 3 — CONSTRAINT-01: bounded footprint

Probes: spatial reasoning under hard constraint, reach-analysis,
honest-asking-when-impossible.

- **Canonical:** CP-04 (NEW). Pick-place cell that fits in 2×2m
  footprint, 4 cubes.
- **Form gate:** every prim's xy bbox within [−1, 1] × [−1, 1];
  ≥1 robot, ≥4 cubes, ≥1 bin; reach OK; controller installed.
- **Function gate:** at t=120s — ≥3/4 cubes delivered to bin AND at
  rest.
- **The interesting probe:** does the agent ASK the user when the
  footprint is too tight ("a 1.5m belt won't fit; do you want
  fewer cubes or smaller cubes?") or silently force-fit a broken
  layout? The honesty signal matters.

### Step 4 — REORIENT-01: pose transformation

Probes: sequential motion, intermediate-state design, pose-aware
grip.

- **Canonical:** CP-05 (NEW). Cube arrives lying on its side. An
  intermediate flip-station (passive ramp or active actuator) flips
  it upright. Two-robot or two-pick sequence: Robot 1 (or first
  pick) places on flip-station; second pick takes the upright cube
  to destination.
- **Form gate:** intermediate flip-station present; ≥2 picks;
  cube starts non-upright (orientation explicitly set).
- **Function gate:** at t=120s — cube in destination AND
  |cube.up_vector · world_up| > 0.95 (~within 18° of upright) AND
  at rest.
- **Hardest of the four canonicals.** May need new tooling for
  the flip-station physics. Design discussion needed before
  implementing.

### Step 5 — MULTIMODAL-01: sketch input

Probes: input-pipeline breadth, modality-parser.

- **Acknowledged not ready** — modality-parser doesn't exist.
- When the parser ships: it takes a 2D sketch (PNG/JPG), produces a
  structured spec (rooms, walls, robot positions, intent). Spec
  pipes into the existing canonical+task pattern.
- Out of scope for this roadmap. Build steps 1-4 first; multi-modal
  is purely additive.

---

## Execution order across sessions

The roadmap is multi-session work. The next session should do
Phase 0 autonomously, then check in.

### Phase 0 — Foundation (AUTONOMOUS)

Four sub-phases. Each ends with a commit + smoke-test pass + push to
`anton`. STOP between sub-phases if any smoke-test fails. Do not
combine.

#### 0.1 — Scrub `/mnt/shared_data` ghost

**Where it lives:**
```
grep -c '/mnt/shared_data' workspace/audit.jsonl
grep -rn '/mnt/shared_data' workspace/knowledge/ 2>/dev/null
grep -rn '/mnt/shared_data' workspace/turn_snapshots/ 2>/dev/null | head
```

**Action:** in-place redact path → `<sanitized-asset-path>` in
audit.jsonl + knowledge/*.jsonl. Delete any ChromaDB index
directories under `workspace/` that depend on these (typically
`workspace/tool_index/`, `workspace/knowledge_index/` —
service rebuilds them on next startup from sanitized sources).

**Leave alone:** two intentional references in `tool_executor.py`
(`_LULA_LIB_PATHS` fallback + comment). They are robotics-search
paths, not asset paths the agent emits.

**Success criterion:** after redaction,
```
grep -rn '/mnt/shared_data' workspace/ docs/ scripts/ exts/ | wc -l
```
prints ≤2 (only post-mortem doc that *describes* the past failure).

**Commit + push to `anton`.** STOP if hits exceed ~500 in any one
file (write a blocker doc, ask).

#### 0.2 — Slim CP-01 + CP-02 `thoughts`

**Why:** thoughts carry implementation rationale ("cuRobo's
_DOWN_Q_BASE constant", "settling state 8 ticks") that's HOW our
code works, not WHAT the agent should do. Dilutes attention.

**Action:** edit `workspace/templates/CP-01.json` and `CP-02.json`
thoughts field. Keep WHAT-to-do, WHY for non-obvious choices,
failure-modes-of-this-pattern. Cut implementation rationale,
code-derivable numbers, historical context. Aim ≈50% word reduction.

**Smoke test:**
```
cd /home/anton/projects/Omniverse_Nemotron_Ext
python scripts/qa/run_cp01.py
python scripts/qa/run_cp02.py
```
Both must run clean (build phase). Visual cube-delivery verification
deferred to user; note that explicitly in commit message.

**Commit + push.** STOP if either script regresses.

#### 0.3 — Verifier smoke-test framework

**Why:** yesterday's verifier work failed because we wired changes
in without isolation. Build the test scaffolding FIRST, before any
verifier change. Future strengthening can regression-test against
this.

**Action:** create `scripts/qa/verifier_smoke_tests.py`. Three
fixtures:
- `known_good_cp01` — full CP-01 build via the existing script's
  main coroutine.
- `known_broken_no_velocity` — CP-01 build, then zero out
  conveyor's `physxSurfaceVelocity:surfaceVelocity` attribute.
- `known_broken_no_controller` — CP-01 build minus the final
  `setup_pick_place_controller` call.

For each: call `execute_tool_call("verify_pickplace_pipeline", {
stages: [...], cube_path: "/World/Cube_1" })`, capture result,
print fixture × pipeline_ok × len(issues) table.

This phase EXPOSES that current production verifier is form-shallow
— known-broken scenes pass `pipeline_ok=true` because the verifier
doesn't yet check conveyor activity or controller presence. That's
intentional — the smoke-test framework becomes the regression
boundary the next verifier-strengthening session targets.

**Success criterion:** `python scripts/qa/verifier_smoke_tests.py`
exits 0 with the table printed.

**Commit + push.** STOP on Python exception or Kit crash.

#### 0.4 — VR-19 v2 success criterion (doc only)

**Why:** yesterday's reverted dual-gate is the design intent.
Re-introduce as documentation now; the code work follows in Phase 1.

**Action:** edit `docs/qa/tasks/VR-19.md`. Replace the success
criterion section with form (≥counts, topological bridge, reach,
controller installed) AND function (cube xy ∈ Station_C bbox at
t=60s, z within tolerance, at rest). Both required. Add an
agent-discipline note: "if verify fails, surface issues honestly —
do not silently rebuild from scratch."

**No smoke test.** It's a markdown file. Confirm with `git diff`
that the change is what you intended.

**Commit + push.** No code changes.

#### After Phase 0: report and check in

Compose ≤200 words to the user covering:
- 4 sub-phase commit hashes + smoke-test outcomes
- `/mnt/shared_data` count before/after, files touched
- thoughts-slim subjective notes — anything cut where you weren't
  sure, so user can spot-check
- smoke-test framework fixture results
- proposed next direction (Phase 1.1 verifier strengthening?
  Pro+thinking experiment first? Move to Step 2?)

Then STOP. Wait for user.

---

### Phase 1 — VR-19 verifier rebuild + agent-eval (CHECK IN BEFORE STARTING)

**Why check-in:** verifier rebuild touches production code.
Yesterday's mistakes were here. Confirm direction with user before
this work.

#### 1.1 — Strengthen `verify_pickplace_pipeline`

Add three form-checks to the existing handler, ONE AT A TIME, each
smoke-tested via `verifier_smoke_tests.py` before the next:

1. **`conveyor_active`** — for every handoff_gap > 0.30m, look for a
   prim with `PhysxSurfaceVelocityAPI` applied AND non-zero velocity
   whose bbox xy intersects the segment between place and pick.
   Without it the cube is stranded.
2. **`controller_installed`** — for each unique robot in stages,
   check `builtins` for a per-robot pick-place subscription
   (`_curobo_pp_sub_<tag>`, etc.). Without it the robot is a static
   prop.
3. **`cube_source_bridged`** — if `cube_path` given, the cube must
   either be at the first pick zone xy (within 0.20m) OR be on an
   active conveyor that bridges into it.

After each check is added, re-run smoke tests. Expected:
- known_good: `pipeline_ok=true`, issues=[]
- known_broken_no_velocity: `pipeline_ok=false` with a
  conveyor_active issue (after check 1 lands)
- known_broken_no_controller: `pipeline_ok=false` with a
  controller_installed issue (after check 2 lands)

**One commit per check.** STOP on any smoke-test regression.

#### 1.2 — `simulate_traversal_check` (new tool)

Add as new handler. Schema in `tool_schemas.py`. Add to
`_ALWAYS_TOOLS`. Action: stop timeline, capture cube initial pos,
play for `duration_s` of sim time (default 60), capture final
pos+velocity, compare to target bbox. Return success=true only if
inside target xy AND above target floor minus tolerance AND at rest.

Extend smoke-test framework with:
- `known_good_cp01` → simulate_traversal_check from cube to bin
  → success=True
- `known_broken_no_controller` → success=False (cube doesn't move)

**Important:** in smoke tests use `duration_s=30` to halve sim
time. Default 60 stays for production calls.

**STOP** if any smoke-test fails or Kit crashes.

#### 1.3 — Run VR-19 v2 (Flash, thinking-off — current config)

Now we have working verifiers + sanitized data + slimmed templates.
Re-run VR-19 against the same Gemini config that failed yesterday.
Capture concretely:
- Did the agent call `verify_pickplace_pipeline`? (yes/no, with
  args)
- Did it call `simulate_traversal_check`? (yes/no, with args)
- Did it use `/mnt/shared_data`? Should be no after Phase 0.1.
- Did it use `robot_wizard` or `run_usd_script` for Franka import?
- Form gate result (pipeline_ok)
- Function gate result (success from simulate)

**Two outcomes:**
- **Both gates pass** → form+function architecture works. Move to
  Step 2.
- **Either gate fails** → user decides whether to try
  Pro+thinking-on (Phase 1.4) before declaring agent-reasoning is
  the bottleneck.

#### 1.4 — Pro+thinking-on experiment (only if 1.3 fails)

Set:
- `LLM_MODE=cloud`
- `CLOUD_MODEL_NAME=gemini-3-pro-preview`
- `GEMINI_EXPOSE_THOUGHTS=1`

Re-run VR-19. Capture the same data as 1.3.

If Pro+thinking passes where Flash failed: bottleneck was reasoning
budget, not architecture. Plan accordingly. If Pro+thinking still
fails: agentic-iteration over verify-feedback is structurally bad.
Pivot to template-instantiation pattern (see post-mortem).

---

### Phase 2 — SORT-01 (CHECK IN BEFORE STARTING)

#### 2.1 — Design discussion

Decide with user: is colour routing a controller-arg
(`color_routing={"red": "/World/RedBin", "blue": "/World/BlueBin"}`
extending `setup_pick_place_controller`) or a typed-resolver
(`resolve_color_routing` that the agent uses to parameterise two
controller calls)?

Either is defensible. The arg is simpler; the resolver is more
composable but adds complexity.

#### 2.2 — Build CP-03 deterministic canonical

NEW canonical template + script. Pattern from CP-02 + add:
- Two cubes with distinct colours (apply different visual + physics
  materials).
- Two bins.
- Per-cube destination based on colour.

**Smoke test:** `python scripts/qa/run_cp03.py` builds the scene;
user visual-verifies red→red, blue→blue.

**Commit + push.**

#### 2.3 — Write `docs/qa/tasks/SORT-01.md`

Form gate: ≥2 differently-coloured cubes, ≥2 bins (one per colour),
≥1 robot, ≥1 conveyor; controllers installed; bins reach-OK.
Function gate: at t=90s, every red cube in red_bin xy, every blue
cube in blue_bin xy, all at rest.

#### 2.4 — Run SORT-01 agent-eval + analyse

Same approach as VR-19. Capture concrete data. Honest pass/fail.

---

### Phase 3 — CONSTRAINT-01 (CHECK IN BEFORE STARTING)

Same shape as Phase 2. Build CP-04 first (compact 2×2m cell).
Smoke-test deterministically. Write CONSTRAINT-01 task spec with
form (footprint bound + counts) + function (≥3/4 delivered).
Run agent-eval. Probe: does the agent ask when constraints are
infeasible, or silently force-fit?

---

### Phase 4 — REORIENT-01 (CHECK IN BEFORE STARTING)

Hardest. Build CP-05 (intermediate flip-station). May need new
tooling for the flip-station mechanism. Design discussion with user
first. Then deterministic, smoke-test, agent-eval.

Function gate: cube in destination AND
|cube.up_vector · world_up| > 0.95 AND at rest.

---

### Phase 5 — MULTIMODAL-01 (deferred)

Add when modality-parser exists.

---

## Hard constraints (apply to ALL phases)

- ONE substantive change per commit. Smoke-test between.
- Concrete numbers in commit messages ("CP-01 4/4 cube delivery"
  not "looks good").
- Stop on smoke-test failure. `git reset --hard HEAD~1` that
  phase. Write `docs/specs/<date>-blocker.md` with concrete output
  + what was tried. Wait.
- Don't push to `fork` or k3street. Working branch + `anton` only.
  PR #89 is untouchable.
- Don't touch the orchestrator outside Phase 1.x scope.
- After every phase that has a check-in marked: write ≤200 word
  report, STOP, wait.
- If Kit crashes, services hang, or data is in unsafe shape: STOP.
  Don't relaunch aggressively. Don't improvise. Write blocker.
  Wait.

---

## Done conditions

**Per-step done:** the canonical CP-N is verified deterministically
AND the task spec exists AND at least one agent-eval has been run
with honest pass/fail recorded.

**Roadmap done:** all four agent-evals (VR-19, SORT-01,
CONSTRAINT-01, REORIENT-01) have been run with results captured.
The form+function dual-gate architecture either works (>50% pass
rate across the four) or has been honestly declared
model-limited and pivoted to template-instantiation.

**The user-visible outcome:** Anton can type a free-form
single-paragraph prompt for any of the four task shapes and get a
working scene from Isaac Assist. That's the test.

---

## Failure handling

- Phase 0 sub-phase smoke-test fails → roll back the commit, write
  blocker doc, wait.
- Phase 1.1 strengthening fails CP-01 verification → the new check
  is too strict for the canonical. Revert it. Tighten the check
  rule (e.g. raise threshold) before re-trying.
- Phase 1.3 VR-19 fails on agent run with stale `/mnt/shared_data`
  appearing → Phase 0.1 was incomplete. Stop. Hunt the remaining
  source. Don't run agent-evals again until clean.
- Phase 2-4 canonical fails to deliver via deterministic script →
  the canonical itself has a bug (not the agent). Fix in CP-N
  before writing any task spec.
- Any phase produces results that contradict prior runs → don't
  paper over. Write the contradiction in the report. The user
  resolves.

---

## Notes for the agent picking this up

You are Claude. You have these tendencies the user has called out:
- declaring success early (count tool calls = "it worked")
- lumping multiple changes together (yesterday's cascade)
- skipping smoke-tests under time pressure
- talking while doing instead of doing then talking

The plan above is structured to make those harder. The smoke-tests,
one-thing-per-commit rule, and STOP-on-failure conditions are not
process theatre — they exist because they prevent the failure modes
that actually happened yesterday. Respect them.

When in doubt: stop and ask. Costs less than rolling back a broken
PR or crashing Isaac Sim.
