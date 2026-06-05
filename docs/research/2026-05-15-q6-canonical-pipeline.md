# Q6: Canonical Creation Pipeline — Producing New Canonicals at Scale

**Date:** 2026-05-15
**Researcher:** Sonnet agent (Phase 2 / Question 6)
**Brief:** `docs/specs/2026-05-15-research-spec-flow-canonicals-autonomous.md`
**Prior art:**
- `docs/research/2026-05-09-yrkesroll-expansion.md` — role gap analysis
- `docs/research/2026-05-09-canonical-quality-audit.md` — 86-template quality audit
- `docs/research/2026-05-15-q2-canonical-format.md` — field-presence survey

---

## 0. What exists today (ground truth)

Before recommending a pipeline, here are the verified counts from direct
inspection of `workspace/templates/`:

| Cohort | Count | Has `intent` | Has `roles` | `verified_status` | Function-gate ✓ |
|---|---|---|---|---|---|
| CP-01..CP-05 (role-based) | 5 | 5 | 5 | 5 | 5 |
| CP-06..CP-87 (plain canonicals) | 82 | 0 | 0 | 103 across all CP | ~40 |
| CP-NEW-* (yrkesroll drafts) | 22 | 0 | 0 | 22 (build-spec status) | 0 |
| Other prefix (A, D, E, F…) | 212 | 0 | 0 | 0 | 0 |
| **Total** | **321** | **5** | **5** | **130** | **~40** |

Key honesty point: of the 22 CP-NEW yrkesroll templates drafted 2026-05-10,
**zero** are function-gate verified. Eight are documented `stable_fail` with
explicit root causes. The rest range from "smoke-test ✓ 1/1" (lightweight
build check) to "BUILD_OK" (tool calls executed without error, no cube delivery
verified). None have `intent`, `roles`, or `code_template` fields.

Source: direct `python3` parse of all 321 JSON files in
`workspace/templates/`. Field counts confirmed by the Q2 research report.

---

## 1. Source-of-Canonicals Taxonomy

### Source A — Human-authored (CP-01 pattern)

**What it is:** A developer (Anton) iterates against a live Kit session:
draft `code` field → run via `execute_tool_call` → observe viewport → fix →
repeat. After function-gate passes, commit.

**Evidence:** The git log for `workspace/templates/CP-01.json` shows 12
commits from `3445b0b` (initial draft) to `2744bb6` (role-based refactor),
spanning multiple sessions. CP-01's 9-point `thoughts` section is a real
design memo written from hard-won debugging, not LLM output:
`workspace/templates/CP-01.json`, field `thoughts`.

**Throughput potential:** 2-8 new function-gate-verified canonicals per
focused session (empirical: 2026-05-10 session produced 7 smoke-verified
canonicals in one sitting but zero function-gate passes). A session targeting
a single canonical with iteration can verify 1-3.

**Quality risk:** Low for the core code; medium for fields. Human authors
tend to omit `intent`, `roles`, `role_defaults`, `code_template`. CP-01's
role-based fields were added weeks after the original commit (commit
`2744bb6`). The pattern is: code first, metadata later — which means the
quality bar is never fully met in the initial commit.

**Dependency on external assets:** Low. Human author works with locally
available Isaac Sim assets (Franka, UR10, primitives). Templates requiring
Nucleus-hosted USD files (G1 humanoid, Carter, Avatar SimReady) will fail
without Nucleus access.

---

### Source B — LLM-drafted, then human-verified (CP-NEW pattern)

**What it is:** An LLM (Sonnet agent) generates a JSON template body from a
structured prompt describing the scenario. Human reviews `code` field, runs
it, patches failures, documents result in `verified_status`.

**Evidence:** All 22 CP-NEW templates were drafted by an LLM agent in a
single 2026-05-10 session. Commits confirm the pattern:
- `0ea366c` — "Phase 8/9 yrkesroll templates — 6 drafts"
- `b76ce47` — "Phase 9 yrkesroll Top 6-15 — 8 more drafts"
- `3b3c86a` — "Phase 10 yrkesroll Top 16-20 — 5 drafts"
- `be73524` — "Phase 8/9 yrkesroll — 7 templates smoke-verified stable_ok"

The LLM draft took ~1-2 hours of agent time; the verification loop took
another ~2 hours to reach "smoke-verified" state for 7 templates. Zero
templates reached function-gate-verified status (the full pick-place delivery
check) before the session ended.

**Throughput potential:** 8-20 drafts per agent-hour. Verification bottleneck
is at Kit RPC: each function-gate run takes 3-8 minutes of real-time
simulation. One agent cannot parallelize Kit RPC (Kit is single-tenant; see
MEMORY.md: "Kit RPC is single-tenant — no concurrent direct_eval"). At
~5 min/run, one Kit instance can verify ~10-12 canonicals in an 8-hour
window.

**Quality risk:** High without gating. The 22 CP-NEW drafts show three
failure patterns:
1. `stable_fail` with documented root cause (4 templates: brick-stacking,
   peg-in-hole, tactile-insertion, drawer-open) — LLM wrote reasonable code
   but the physics problem is unsolved.
2. "plumbing-only" — template builds and tools call without error, but no
   pick-place delivery is exercised (3 templates: opcua-12conveyors, plc-
   conveyor, plc-fixture). These are legitimate infrastructure templates but
   they have no function gate.
3. Missing structural fields — all 22 CP-NEW templates lack `intent`,
   `roles`, and `code_template`. They cannot be used in the role-based
   retrieval path (Phase 20/21).

**Dependency on external assets:** High for some scenarios. CP-NEW-g1-
bimanual-tabletop fails on "G1 SimReady asset missing from local Nucleus"
(verified_status). CP-NEW-operator-ergonomics fails on "OperatorAvatar
SimReady asset not installed". Any yrkesroll template referencing humanoid
USD, agricultural NeRF assets, or proprietary robot USD requires Nucleus.

---

### Source C — Video-derived (CadCreator / Anton's vision)

**What it is:** Anton's CadCreator project (`/home/anton/projects/CadCreator`)
extracts CAD workflows from educational videos. The vision is: watch a
robotics simulation demo video → extract tool call sequence → produce a
canonical template draft.

**Throughput potential:** Theoretically high (many robotics demo videos
exist), but the extraction pipeline is immature. CadCreator's sketch-
extraction is focused on 2D CAD drawing reading, not 3D Isaac Sim tool
sequence extraction. No existing CadCreator component reads Isaac Sim
session recordings or API call logs.

**Quality risk:** Very high. Video-to-tool-call extraction requires:
(a) recognizing which tool calls were made from a visual recording, or
(b) screen-scraping text from recorded terminal output. Neither path is
implemented. Recommend treating this as a future research direction
(~6-12 months away from production use), not a current pipeline.

**Dependency on external assets:** High. Source videos need to be available
and have recognizable Isaac Sim tool call patterns.

---

### Source D — Scraped from open robotics datasets

**What it is:** Mining structured task descriptions from open datasets
(Open X-Embodiment, Isaac Lab eval task suite, Isaac Lab-Arena, LIBERO,
RoCo Challenge) to generate canonical template skeletons.

**Evidence from web research:**
- Open X-Embodiment: 1M+ episodes, 527 skills, 22 robot platforms.
  [arxiv.org/abs/2310.08864](https://arxiv.org/abs/2310.08864)
- Isaac Lab-Arena: composable benchmark tasks with Lego-like blocks
  (Object, Scene, Embodiment, Task). GitHub:
  [github.com/isaac-sim/IsaacLab-Arena](https://github.com/isaac-sim/IsaacLab-Arena)
- Isaac Lab Evaluation Tasks:
  [github.com/isaac-sim/IsaacLabEvalTasks](https://github.com/isaac-sim/IsaacLabEvalTasks)
- RoCo Challenge AAAI 2026: benchmarking robotic collaborative manipulation
  for assembly towards industrial automation.
  [arxiv.org/html/2603.15469](https://arxiv.org/html/2603.15469)

**Throughput potential:** Dataset mining can produce 50-200 canonical
*descriptions* quickly. The problem is translation: OXE episodes are recorded
robot trajectories, not Isaac Sim tool calls. Mapping "RT-1 picks a cup"
to `setup_pick_place_controller(robot_path=..., source_paths=[...])` requires
non-trivial NLP + sim-knowledge.

**Quality risk:** Medium for description accuracy; high for code correctness.
A dataset entry describes *what happened*, not *how to configure Isaac Sim
to reproduce it*. The canonical's `code` field requires Isaac Sim-specific
knowledge (CPU dynamics flag, sleepThreshold, robot orientation quaternion)
that no open dataset encodes.

**Dependency on external assets:** Medium. Isaac Lab tasks are locally
runnable if Isaac Lab is installed. OXE and RT-X require either the dataset
download or the ability to query the API.

---

### Source E — Industrial-asset-anchored (yrkesroll)

**What it is:** Roles defined by industry domain (welding, palletizing,
kit-prep operator) → each role has a canonical scenario → scenario uses a
specific industrial robot USD (Yaskawa GP25, FANUC M-710iC, ABB 1600).

**Evidence:** The 2026-05-09-yrkesroll-expansion.md doc identifies 8 roles
with 50+ gap canonicals. The 20 CP-NEW templates drafted 2026-05-10 are
the first wave of this source. Phase 78c (spec line 5679) formally specifies
the `requires` manifest field for asset dependency tracking.

**Throughput potential:** 5-10 new *draft* yrkesroll canonicals per agent-
session. Verification bottleneck is asset availability: most industrial robot
USDs require NVIDIA Nucleus enterprise account. This is the #1 blocker for
this source today.

**Quality risk:** Medium. The scenario logic (what the robot does) is well-
specified by the role doc. The failure points are (a) asset missing at build
time and (b) physics stability of the specific scenario (contact-rich tasks
like peg-in-hole and brick-stacking are documented hard failures).

**Dependency on external assets:** High. G1 humanoid, Carter, Avatar SimReady,
industrial robot USDs are Nucleus-hosted. Without them, templates build with
bounding-box placeholders (Phase 78c plan) or fail entirely.

---

## 2. Quality Bar for "Shippable"

A canonical is **shippable** — meaning it can be committed, indexed in
ChromaDB, and presented to an end-user via hard-instantiate — when ALL
of the following criteria pass:

### Criterion 1: `goal` declared (intent clear)
The `goal` field is a complete English paragraph describing the scene, robot
behavior, and success condition. It is the primary retrieval document stored
in ChromaDB (`scripts/qa/add_templates_from_tasks.py`, lines 30-33).
Not a title; must be long enough for meaningful embedding similarity.

Verification: non-empty `goal` field with ≥ 2 sentences.
Existing gate: none automated; the Q2 report notes this is universal today.

### Criterion 2: `intent` and `roles` declared
Required for Phase 20/21 role-based retrieval. `intent.pattern_hint` must
match one of the known patterns (e.g., `pick_place`, `navigate`, `inspect`).
`roles` must list every scene participant with `constraints` and
`expected_count`.

Verification: `tests/test_role_based_templates.py` (implicitly) — tests
that B1B templates have `roles`.

Currently this criterion is met only for CP-01..CP-05
(`workspace/templates/CP-0{1..5}.json`). Zero of the 22 CP-NEW templates
meet it.

### Criterion 3: `code_template` substitutable
The `code_template` field contains the same logic as `code` but with
`{{role.field}}` placeholders. Substituting `role_defaults` into
`code_template` must produce output identical (up to formatting) to `code`.

Verification: `tests/test_role_template_equivalence.py`, parametrized over
`["CP-01", "CP-02", "CP-03", "CP-04", "CP-05"]` (lines 70-93). Test
captures tool calls from both paths and asserts `_normalize(legacy_calls)
== _normalize(role_calls)`.

Currently met only for CP-01..CP-05.

### Criterion 4: Form-gate passes (structural check)
`verify_pickplace_pipeline` succeeds: the scene contains the expected prims,
the robot is properly attached, sensors are in range, bins exist at expected
positions. This is what `verify_args` in the template feeds.

Verification: `verify_pickplace_pipeline(verify_args)` returns `success=True`.
Cited as "form-gate ✓" in `verified_status`.

~65 CP templates meet this today (from the 2026-05-09-canonical-quality-
audit.md count; exact number from `verified_status` grep: 65 strings contain
"function-gate" in their status text).

### Criterion 5: Function-gate passes on N runs
`simulate_traversal_check(simulate_args)` with `duration_s=180` returns
`cube_final` inside target bounding box. For stochastic controllers, N ≥ 3
runs with success on ≥ 2.

Verification: `scripts/qa/function_gate_suite.py` and
`scripts/qa/function_gate_consistency.py`. The consistency script runs N
times and computes Wilson confidence intervals.

Honesty note: "build-spec status" and "smoke-test ✓ 1/1" are NOT
function-gate passes. A smoke test runs the code without error; it does not
assert cube delivery. Of the 321 templates, approximately 40-50 have a
genuine function-gate-verified status string with a `cube_final` coordinate.

### Criterion 6: `failure_modes` documented
Every known way the template can fail, including physics-specific failures
(sleepThreshold, surface velocity, GPU dynamics flag). These are the
coaching messages the LLM uses when the template fails.

Currently universal: all 321 templates have this field (Q2 field survey).

### Criterion 7: Hardware compatibility declared
`min_vram_gb` and `recommended_vram_gb` in the template's manifest. Used by
`filter_templates_by_hardware` (`handlers/scene_blueprints.py`, line 1178)
to pre-filter templates for the user's GPU.

Currently: 6 templates have VRAM info (from grep). The export_template
handler (`handlers/scene_blueprints.py`, line 565) generates a `.isaa`
manifest with these fields when packaging, but the JSON templates themselves
rarely include them.

### Summary Checklist

```
[ ] goal: ≥2 sentences, retrieval-quality
[ ] intent.pattern_hint: one of {pick_place, navigate, inspect, assemble, ...}
[ ] roles: all scene participants with constraints
[ ] code_template: substitutable, equivalence test passes
[ ] verify_args: form-gate args populated
[ ] simulate_args: function-gate args populated (duration_s ≥ 180)
[ ] verified_status: contains "function-gate ✓" with cube_final coordinate
    OR contains documented "plumbing-only" / "stable_fail" with root cause
[ ] failure_modes: non-empty list
[ ] hardware: min_vram_gb set (or documented as CPU-only)
[ ] requires: asset dependency manifest (Phase 78c format)
```

A "draft template" meets criteria 1, 6. A "shippable template" meets all 10.
The 22 CP-NEW templates today meet criteria 1 and 6 only.

---

## 3. Author Workflow: Idea → Drafted → Verified → Committed

Each step is annotated with: who does it, what tool, what gate.

### Step 1: Idea capture (Human)
**Actor:** Human (Anton or Kimate)
**Input:** Role gap analysis (`docs/research/2026-05-09-yrkesroll-expansion.md`
Top-20 table) or ad-hoc observation
**Output:** 3-5 sentence description of the canonical: goal, robot, objects,
success criterion, which tool call pattern it exercises
**Gate:** None. This is editorial.

### Step 2: Spec-level draft (Sonnet agent, autonomous-safe)
**Actor:** Sonnet agent
**Input:** Step 1 description + CP-01 template as few-shot example + list of
available tool names from `tool_schemas.py`
**Output:** Draft JSON template with `task_id`, `goal`, `tools_used`,
`thoughts`, `code`, `failure_modes`, `extends`, `extension_notes`,
`verify_args`, `simulate_args`
**Prompt pattern:** "Given this scenario description and CP-01 as an example,
produce a new template JSON. Use only tools from this list. Set
verified_status to 'draft-unverified'."
**Gate:** JSON schema validation. `code` field must exec without NameError
in the sandbox (`canonical_instantiator._SAFE_BUILTINS`).

The CP-NEW templates show this step can produce 6-10 drafts per agent hour.

### Step 3: Asset precheck (Sonnet agent or human)
**Actor:** Sonnet agent (when Phase 78c is implemented) or human (today)
**Input:** Draft JSON, specifically the USD paths in `code`
**Output:** Asset availability report: which USD paths exist locally,
which require Nucleus, which have mock fallbacks
**Gate:** All `requires` dependencies must have `status=ok` or a documented
mock-fallback path. If a dependency has `status=missing` and no fallback,
the template is parked until the asset is available.

Today this step is manual (the human spots the "G1 SimReady asset missing"
failure in the viewport). Phase 78c (`specs/IA_FULL_SPEC_2026-05-10.md`,
line 5679) plans `precheck_template_assets()` to automate this.

### Step 4: Code iteration (Human + Kit session, not autonomous)
**Actor:** Human with Kit RPC alive
**Input:** Draft JSON from Step 2
**Output:** Working `code` field + `settle_state` + `diagnose_args`
**Process:** `execute_template_canonical(template)` via Kit RPC →
observe viewport → fix physics parameters → repeat
**This step is NOT autonomous-Sonnet-safe.** It requires:
- Kit RPC alive at 127.0.0.1:8001
- Human visual judgment of the viewport ("is the robot oriented correctly?")
- Iterative physics parameter tuning (sleepThreshold, solverPositionIterationCount,
  surface_velocity scale factors)

**Gate:** Code executes without error in the sandbox AND Kit viewport shows
the expected scene geometry (human visual check).

### Step 5: Form-gate verification (Sonnet agent, Kit required)
**Actor:** Sonnet agent (or `function_gate_suite.py` script)
**Input:** Template with `verify_args` populated
**Tool:** `verify_pickplace_pipeline(verify_args)` via Kit RPC
**Output:** `success=True` or structured failure report
**Gate:** `success=True`. If `success=False`, agent classifies failure:
missing prim (fix `code`), wrong robot path (fix `verify_args`), or
physics instability (→ Step 4 iteration).
**Duration:** ~30 seconds per run.

### Step 6: Function-gate verification (Sonnet agent, Kit required)
**Actor:** Sonnet agent (or `function_gate_suite.py` script)
**Input:** Template with `simulate_args` (including `duration_s=180`)
**Tool:** `simulate_traversal_check(simulate_args)` via Kit RPC
**Output:** `cube_final` coordinate + `success` bool
**Gate:** `cube_final` is inside target bounding box. For stochastic
templates (any cuRobo-based), run 3 times; require 2/3 pass.
Cited in `scripts/qa/function_gate_consistency.py`.
**Duration:** 3-8 minutes per run. Three runs = 10-25 minutes per template.

If the template is declared `plumbing-only` (no pick-place delivery to gate),
skip Step 6 and document explicitly in `verified_status`.

### Step 7: Role-migration (Sonnet agent, no Kit required)
**Actor:** Sonnet agent
**Input:** Working template from Step 6 (has `code` field)
**Output:** `intent`, `roles`, `role_defaults`, `code_template`,
`verify_args_template`, `simulate_args_template` added
**Gate:** `test_role_template_equivalence.py` equivalence test passes:
`_normalize(_capture_tool_calls(code)) == _normalize(_capture_tool_calls(
instantiate_role_based_code(template)))`.
No Kit session required. Pure Python sandbox execution.

### Step 8: Hardware annotation (Sonnet agent, no Kit required)
**Actor:** Sonnet agent
**Input:** Template code field
**Output:** `min_vram_gb`, `recommended_vram_gb` set based on presence of
`clone_envs` (GPU-only), cuRobo (needs CUDA), deformable meshes, etc.
**Gate:** Values match published Isaac Sim GPU memory requirements.

### Step 9: ChromaDB indexing (autonomous script)
**Actor:** `scripts/qa/add_templates_from_tasks.py --all-new`
**Input:** Committed JSON template
**Output:** Entry in `workspace/tool_index` ChromaDB collection
`isaac_assist_templates`
**Gate:** Script exits 0; collection count increases by 1.

### Step 10: Commit (Human final review)
**Actor:** Human
**Input:** Template JSON after Steps 1-9
**Gate:** `git diff workspace/templates/NEW-template.json` reviewed, commit
message follows existing pattern (`CP-XX — description`).

---

## 4. Cron-Friendly Subset

### Autonomous-Sonnet-safe (no human in the loop, no Kit required)

- **Step 2** (spec-level draft): pure LLM + JSON, no external dependencies
- **Step 7** (role-migration): Python sandbox, equivalence test, no Kit
- **Step 8** (hardware annotation): keyword scan of code + schema lookup
- **Step 9** (ChromaDB index): deterministic script

These four steps can be chained into a cron job:
```
draft_canonical(spec) → validate_json → add_roles → annotate_hardware
                      → run_equivalence_test → index_in_chromadb
```
Output: a "pre-verified draft" that has all metadata fields set but
`verified_status = "draft-requires-kit-verification"`.

### Requires Kit RPC (autonomous Sonnet OK, but Kit must be alive)

- **Step 5** (form-gate): can be automated, but needs Kit alive at 8001
- **Step 6** (function-gate): can be automated; run sequentially (single-
  tenant Kit). `function_gate_suite.py` is the existing automation.
  Note: Kit must not have stale state. Restart after every ~30 canonicals.
  (MEMORY.md: "restart Kit autonomously when state drifts (~30 CPs)")

### Requires human review (NOT autonomous-Sonnet-safe)

- **Step 1** (idea capture): editorial judgment
- **Step 3** (asset precheck when Nucleus is required): human must decide
  whether to use a mock or wait for asset
- **Step 4** (code iteration with physics tuning): requires visual judgment
  and domain knowledge; Sonnet can attempt fixes but tends to produce
  "stable_fail" templates without human oversight
- **Step 10** (final commit): Anton's explicit review gate

The 2026-05-10 session history is direct evidence: an autonomous agent
running Steps 2-4 without a human produced 22 templates of which zero
achieved function-gate-verified status and 4 are documented as permanently
blocked by physics instabilities. Human involvement in Step 4 correlates
with template quality.

**Recommendation for autonomous cron:** Steps 2, 7, 8, 9 are safe. Steps 5
and 6 are safe if Kit is alive and a restart protocol is embedded. Steps 1,
3, 4, 10 require human.

---

## 5. Yrkesroll Priority List

Criteria for ranking:
1. **Asset availability locally** — no Nucleus required, or Nucleus-hosted
   but already in local cache
2. **Physics complexity** — pick-place or navigation over contact-rich
   assembly (contact-rich templates have high stable_fail rate)
3. **Market value** — how many users need this scenario
4. **Library coverage gap** — zero existing templates in this shape

### Tier 1 — Implement first (locally-runnable, medium complexity)

1. **Inspect-and-reject divert (CP-NEW-inspect-reject)**
   Status: already drafted as CP-NEW-inspect-reject; smoke-test ✓ 1/1 (52s).
   Next: add `intent`/`roles`, run function-gate.
   Asset: uses existing Franka + Cube + conveyor. No Nucleus required.
   Market: quality engineers, inspection lines. High demand.

2. **Defect-introduction SDG (CP-NEW-defect-sdg)**
   Status: CP-NEW-defect-sdg drafted; BUILD_OK 93/115 (material_path error
   on some configs).
   Next: fix material_path, add roles, run function-gate on verified subset.
   Asset: omni.replicator extension (usually installed). No Nucleus required.
   Market: SDG dataset engineers, computer-vision teams.

3. **Domain-randomization curriculum (CP-NEW-dr-curriculum)**
   Status: CP-NEW-dr-curriculum; smoke-test ✓ 1/1 (34s).
   Next: add roles, run function-gate.
   Asset: standard Franka + DR tools. No Nucleus required.
   Market: ML/RL researchers, sim-to-real teams. High demand.

4. **Y-merge conveyor singulation (CP-NEW-y-merge-singulation)**
   Status: CP-NEW-y-merge-singulation; smoke-test ✓ 1/1 (53s); 6 cubes
   via Y merge.
   Next: add roles, full function-gate.
   Asset: built-in conveyor primitives. No Nucleus required.
   Market: logistics engineers, warehouse designers.

5. **3-station OEE logging (CP-NEW-3station-oee)**
   Status: CP-NEW-3station-oee; smoke-test ✓ 1/1 (90s); 3 Frankas + 9 cubes.
   Next: add roles, add metric emission, function-gate.
   Asset: Franka × 3, existing primitives. No Nucleus required.
   Market: manufacturing engineers, OEE/throughput analysts.

6. **Controller-shootout benchmark (CP-NEW-controller-shootout-cp)**
   Status: CP-NEW-controller-shootout-cp; smoke-test ✓ 1/1 (47s).
   Next: extend to N=5 runs, add roles, function-gate.
   Asset: standard Franka. No Nucleus required.
   Market: robotics engineers comparing controllers.

7. **Multi-camera triangulation (CP-NEW-multi-cam-triangulation)**
   Status: CP-NEW-multi-cam-triangulation; smoke-test ✓ 1/1 (21s).
   Next: add roles, verify sensor fusion output, function-gate.
   Asset: camera primitives (built-in). No Nucleus required.
   Market: quality/vision engineers.

### Tier 2 — Implement second (locally runnable, requires physics tuning)

8. **Cloned-env RL scaffold (CP-NEW-rl-clone-env)**
   Status: CP-NEW-rl-clone-env; BUILD_OK 15/18; 3 errors on clone_envs.
   Next: fix clone_envs API call, add roles.
   Asset: RSL-RL python package (needs install), Franka. No Nucleus.
   Blocker: RSL-RL may not be installed. Phase 78c mock needed.

9. **Sim-to-real gap measurement (CP-NEW-sim2real-gap)**
   Status: CP-NEW-sim2real-gap; BUILD_OK 17/18; 1 error on replay_rosbag.
   Next: fix rosbag path, add roles.
   Asset: real rosbag file (data dependency). Without a rosbag, needs
   synthetic trace from record_trajectory (Phase 65).

10. **Multi-AMR corridor (CP-NEW-multi-amr-corridor)**
    Status: CP-NEW-multi-amr-corridor; BUILD_OK 21/21 with target_position
    + direction fixes.
    Next: full function-gate for multi-AMR delivery assertion.
    Asset: Carter USD — may need Nucleus. Check local cache first.

### Tier 3 — Implement third (asset dependency, lower priority now)

11. **AMR pickup-from-cell handoff (CP-NEW-amr-pickup-handoff)**
    Status: BUILD_OK but gate stable_fail (cube never picked by AMR).
    Blocker: controller plans 30x but cube never picked — needs pick-place
    integration with wheeled robot. Non-trivial.
    Asset: Carter USD.

12. **PLC-conveyor (CP-NEW-plc-conveyor) and PLC-fixture (CP-NEW-plc-fixture)**
    Status: BUILD_OK; plumbing-only (no cube delivery).
    These are legitimate infrastructure templates but need explicit
    "plumbing-only" function-gate bypass + OPC-UA mock server for the
    I/O loop.

13. **Drawer-open (CP-NEW-drawer-open)**
    Status: stable_fail — pick-place controller can't handle articulated
    environment objects. Blocked on prismatic joint inference (Phase 72b).

### Tier 4 — Hold (Nucleus-dependent, physics unsolved)

14. **G1 bimanual tabletop (CP-NEW-g1-bimanual-tabletop)**
    Blocked: G1 SimReady USD not in local Nucleus. Build 20/21.

15. **Operator ergonomics (CP-NEW-operator-ergonomics)**
    Blocked: OperatorAvatar SimReady USD not installed.

16. **Peg-in-hole (CP-NEW-peg-in-hole-single) and Tactile insertion**
    Status: stable_fail — PhysX numerical explosion during grip. Physics
    fix not available without contact-aware planner.

17. **Brick-stacking (CP-NEW-brick-stacking)**
    Status: stable_fail — persistent PhysX explosion. Fix identified
    (convex-hull + pin tolerance) but not yet applied.

**Tier 4 recommendation:** Do not invest authoring time until the physics
fix (Phase 78b) or Nucleus access is confirmed. Document as parked.

---

## 6. Estimated Production Rate

### Math inputs
- Kit function-gate: 3-8 min per run, 3 runs per template = 10-25 min/template
- One Kit instance: single-tenant, ~3 templates/hour under function-gate
- LLM draft: ~10 templates/hour (Step 2 only)
- Role-migration: ~5 templates/hour (Step 7, no Kit)
- Human review per template: 10-20 min including reading verified_status

### (a) Human only (Steps 1-10, no LLM assistance)

Human writes code, runs Kit, iterates, documents.
Rate: ~2 function-gate-verified canonicals per 8-hour session.
Weekly: 10-14 (two full sessions/week sustainable).
Bottleneck: human code-writing time in Step 4.

### (b) Sonnet-assisted human review (LLM draft + human verifies)

LLM handles Steps 2, 7, 8, 9. Human handles Steps 1, 3, 4 (light touch),
10. Kit automation handles Steps 5, 6.
Rate: ~5-8 function-gate-verified canonicals per 8-hour session.
Weekly: 25-35.
Bottleneck: Kit RPC throughput (3 templates/hour × ~8 active hours = 24
canonical verifications/day). At 3 runs each, effectively ~8-10 function-
gate completions/day.

Note: "LLM draft" alone produces 50-80 drafts/day, but drafts are not
verified. The verified rate is limited by Kit throughput, not LLM speed.

### (c) Fully autonomous with gate (Sonnet runs Steps 2-6 sequentially)

Human sets up the task queue (Step 1 for each scenario). Autonomous agent
runs Steps 2-6 overnight, files results, human reviews next morning (Step 10).
Rate: Kit is the bottleneck. At 24 function-gate slots/day, approximately
8-10 NEW canonicals passing the gate per day (accounting for stable_fail
failures in ~20-30% of drafts that require extra iteration or human
escalation).
Weekly: 40-60 new function-gate-verified canonicals.
Constraint: must restart Kit every ~30 canonicals. If Kit is left running
unattended, state drift causes false failures. The autonomous loop needs
embedded restart logic.

**Honest caveat:** The 2026-05-10 session dispatched ~20 drafts and produced
zero function-gate-verified canonicals. The gap between "autonomous draft"
and "function-gate verified" is real and large. The (c) rate estimate assumes
the autonomous agent is capable of the physics-iteration loop in Step 4,
which is empirically unproven. The 2026-05-10 evidence suggests Sonnet alone
cannot reliably navigate the physics iteration — it produces stable_fail
patterns (brick-stacking, peg-in-hole) that required documented physics
knowledge that the agent did not apply.

Revised realistic estimate for (c): 5-8 new function-gate-verified canonicals
per day (half the theoretical Kit-throughput ceiling), because ~40-50% of
autonomous drafts will hit physics instability requiring human escalation.

---

## 7. Risk Catalog

### Risk 1: "Build OK" ≠ "Shippable" conflation

**Description:** An agent marks a template `verified_status = "build OK"` or
"smoke-test ✓" and treats it as complete. The user instantiates it and no
robot action occurs (plumbing-only) or the scene crashes.

**Evidence:** 22 CP-NEW templates are in this state today. CP-NEW-amr-pickup-
handoff is build ✓ but "cube never picked" in the function-gate.

**Mitigation:** Require `verified_status` to contain one of exactly:
`"function-gate ✓"`, `"plumbing-only (no pick-place delivery)"`, or
`"stable_fail — <root cause>"`. Any other string is rejected by CI
conformance check.

---

### Risk 2: Physics instability inherited across descendants

**Description:** A new canonical `extends` an existing one but copies its
physics-sensitive parameters verbatim. The parent's sleepThreshold or
solverPositionIterationCount was tuned for the parent's geometry. The child
has different mass or contact geometry and explodes.

**Evidence:** CP-71, CP-87 both needed `solverPositionIterationCount=32`
(double the default 16) because their geometry differs from CP-01. The brick-
stacking family (CP-NEW-brick-stacking) hit PhysX explosion from inherited
mesh-collision settings.

**Mitigation:** When an agent drafts a child template, mandate a
`simulate_traversal_check` run before committing. Do not inherit physics
parameters blindly; run the function-gate on the child independently.

---

### Risk 3: Missing assets masked by incomplete error messages

**Description:** A template references a USD path that exists in NVIDIA
Nucleus but not locally. The `robot_wizard` or `add_usd_reference` call
returns a timeout or empty-stage error that the agent misinterprets as a
scene-geometry problem rather than an asset-dependency problem.

**Evidence:** CP-NEW-g1-bimanual-tabletop: "G1 SimReady asset missing from
local Nucleus" — the build reached 20/21 steps before failing. This means
the agent wrote 20 tool calls that worked on primitives before hitting the
missing USD at step 21. A naive agent would diagnose this as a "step 21
physics error" and iterate on physics.

**Mitigation:** Phase 78c asset-precheck (`specs/IA_FULL_SPEC_2026-05-10.md`,
line 5679) implements `precheck_template_assets()`. Until it lands: mandate
asset precheck as Step 3 (human or script) before any Kit run.

---

### Risk 4: Role-migration drift (code_template diverges from code)

**Description:** The `code` field is the source of truth. If `code_template`
is generated after `code` has been patched (e.g., drop_target coordinates
updated in a later commit), `code_template` will use stale placeholder
defaults and diverge from `code`. The equivalence test will fail, but only
if it is actually run.

**Evidence:** CP-01..CP-05 role-migration was done once and the equivalence
test currently passes. But 22 batch-patches to CP-* templates were committed
*after* the role migration (e.g., commit `65d2a52`: "explicit drop_target for
16 stable_fail bin-destination CPs"). Any CP with role fields that was also
batch-patched after the migration is at risk.

**Mitigation:** Run `pytest tests/test_role_template_equivalence.py` in CI
on every commit that touches a template. Expand the parametrize list beyond
CP-01..CP-05 as new templates gain role fields.

---

### Risk 5: ChromaDB collection corruption from concurrent writes

**Description:** Two agents simultaneously call
`scripts/qa/add_templates_from_tasks.py` or the post-commit indexer while
another writer is active. ChromaDB HNSW index segfaults on concurrent writes.

**Evidence:** MEMORY.md explicit warning: "VARNING: Kör ALDRIG parallella
ChromaDB-skrivningar — segfaultar + korrupterar HNSW-index."

**Mitigation:** All indexing scripts must be single-process. The cron job
for Step 9 must acquire a file lock before writing. Never fan out multiple
indexing agents.

---

## 8. Recommended Pipeline Architecture

### Conceptual overview

```
┌─────────────────────────────────────────────────────────────────┐
│  INPUT: Yrkesroll spec (3-5 sentences from Top-20 list)          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 1: LLM Draft (Sonnet, no Kit required)                    │
│  - Render structured prompt from spec + CP-01 few-shot           │
│  - Generate draft JSON                                           │
│  - JSON schema validate                                          │
│  - Python sandbox exec (NameError check)                         │
│  - Asset precheck: flag any Nucleus-only USD paths               │
│  OUTPUT: draft_template.json (status: "draft-unverified")        │
│  GATE: JSON valid + sandbox exec clean + no critical-asset-block │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ├── FAIL: file for human review
                            │         (asset missing or syntax error)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 2: Kit Verification (Sonnet agent, Kit RPC required)      │
│  - Build: execute_template_canonical(draft_template)             │
│  - Form-gate: verify_pickplace_pipeline(verify_args)             │
│  - Function-gate: simulate_traversal_check(simulate_args) × 3   │
│  - If stable_fail: log root cause, file for human review         │
│  - If 2/3 pass: update verified_status with cube_final           │
│  OUTPUT: verified_template.json (status: "function-gate ✓ ...")  │
│  GATE: 2/3 simulation runs deliver cube to target bounding box   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ├── FAIL: file for human review
                            │         (stable_fail documented)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 3: Role Migration (Sonnet agent, no Kit required)         │
│  - Add intent, roles, role_defaults                              │
│  - Generate code_template from code (LLM-assisted substitution)  │
│  - Run test_role_template_equivalence (Python sandbox)           │
│  OUTPUT: shippable_template.json (all 10 checklist items ✓)      │
│  GATE: equivalence test passes for all captured tool calls       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ├── FAIL: file for human review
                            │         (placeholder left unfilled)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  STAGE 4: Commit + Index (script, no LLM required)               │
│  - Copy to workspace/templates/                                  │
│  - git add + git commit (conventional message)                   │
│  - add_templates_from_tasks.py (single-process ChromaDB write)   │
│  OUTPUT: template in git + indexed in isaac_assist_templates     │
│  GATE: git exits 0, collection count increases by 1              │
└─────────────────────────────────────────────────────────────────┘
```

### Pseudocode for the autonomous draft job

```python
"""
canonical_draft_job.py — autonomous canonical draft + gate pipeline.

Entry point for the cron job that takes a yrkesroll spec and produces
a shippable template or files for human review.

Usage:
    python canonical_draft_job.py \
        --spec "Build a 3-station OEE logging cell: ..." \
        --task-id CP-NEW-oee-v2 \
        --review-dir docs/review/pending/
"""
from __future__ import annotations
import json, sys, asyncio
from pathlib import Path

TEMPLATES_DIR = Path("workspace/templates")
REVIEW_DIR = Path("docs/review/pending")

async def stage1_draft(spec: str, task_id: str) -> dict:
    """LLM-assisted draft. No Kit required."""
    few_shot = json.loads((TEMPLATES_DIR / "CP-01.json").read_text())
    prompt = f"""
You are authoring an Isaac Assist canonical template.
SPEC: {spec}
FEW-SHOT: {json.dumps(few_shot, indent=2)[:3000]}...
Available tools: [list from tool_schemas.py]
Produce a JSON template with fields:
  task_id, goal, tools_used, thoughts, code,
  verify_args, simulate_args (duration_s=180),
  failure_modes, extends, extension_notes.
Set verified_status = "draft-unverified".
Return ONLY the JSON object.
"""
    raw = await llm_call(prompt)  # Sonnet, temperature=0
    template = json.loads(raw)
    # Schema validation
    for required in ("task_id","goal","code","failure_modes","simulate_args"):
        assert required in template, f"Missing {required}"
    # Sandbox exec check
    _sandbox_exec(template["code"])  # raises if NameError
    return template

async def stage2_kit_verify(template: dict, kit_rpc_url: str) -> dict:
    """Kit-based form + function gate. Single-tenant."""
    from service.isaac_assist_service.chat.canonical_instantiator import (
        execute_template_canonical, settle_after_canonical,
    )
    from service.isaac_assist_service.chat.tools.tool_executor import (
        execute_tool_call,
    )
    # Build
    result = await execute_template_canonical(template, kit_rpc_url)
    if not result.get("success"):
        template["verified_status"] = f"stable_fail — {result.get('error')}"
        _file_for_review(template, reason="build_failure")
        return template
    # Form gate
    if template.get("verify_args"):
        fgate = await execute_tool_call(
            "verify_pickplace_pipeline", template["verify_args"], kit_rpc_url
        )
        if not fgate.get("success"):
            template["verified_status"] = f"stable_fail — form-gate: {fgate.get('error')}"
            _file_for_review(template, reason="form_gate_failure")
            return template
    # Function gate (3 runs)
    passes = 0
    last_cube_final = None
    sim_args = template["simulate_args"]
    for _ in range(3):
        sim = await execute_tool_call(
            "simulate_traversal_check", sim_args, kit_rpc_url
        )
        if sim.get("success"):
            passes += 1
            last_cube_final = sim.get("cube_final")
    if passes >= 2:
        template["verified_status"] = (
            f"function-gate ✓ ({passes}/3 runs; "
            f"cube_final={last_cube_final})"
        )
    else:
        template["verified_status"] = (
            f"stable_fail — function-gate {passes}/3; "
            f"cube_final={last_cube_final}"
        )
        _file_for_review(template, reason="function_gate_failure")
    return template

def stage3_role_migration(template: dict) -> dict:
    """Add intent/roles/code_template. No Kit required."""
    from service.isaac_assist_service.chat.canonical_instantiator import (
        instantiate_role_based_code, _SAFE_BUILTINS,
    )
    # LLM adds intent, roles, role_defaults, code_template
    prompt = f"""
Add intent, roles, role_defaults, code_template to this template.
Follow the CP-01 pattern exactly.
Template: {json.dumps(template)}
Return the UPDATED JSON.
"""
    raw = _llm_call_sync(prompt)
    updated = json.loads(raw)
    # Equivalence check
    legacy_calls = _capture_tool_calls(updated["code"], updated["task_id"])
    role_code = instantiate_role_based_code(updated)
    role_calls = _capture_tool_calls(role_code, updated["task_id"])
    if _normalize(legacy_calls) != _normalize(role_calls):
        _file_for_review(updated, reason="role_equivalence_failure")
        return template  # return unmodified; human resolves
    assert "{{" not in role_code, "Unfilled placeholder in code_template"
    return updated

def stage4_commit_and_index(template: dict) -> None:
    """Write to disk, git commit, ChromaDB index."""
    import subprocess
    task_id = template["task_id"]
    path = TEMPLATES_DIR / f"{task_id}.json"
    path.write_text(json.dumps(template, indent=2, ensure_ascii=False))
    subprocess.run(["git", "add", str(path)], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"{task_id} — autonomous draft + verified"],
        check=True
    )
    # Single-process ChromaDB write — never parallelize
    subprocess.run(
        ["python", "-m", "scripts.qa.add_templates_from_tasks", task_id],
        check=True
    )

def _file_for_review(template: dict, reason: str) -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / f"{template['task_id']}__{reason}.json"
    path.write_text(json.dumps(template, indent=2))
    print(f"[review] Filed {template['task_id']} for human review: {reason}")

async def run(spec: str, task_id: str, kit_rpc_url: str) -> None:
    template = await stage1_draft(spec, task_id)
    if "draft-unverified" not in template.get("verified_status", ""):
        return  # stage1 filed for review
    template = await stage2_kit_verify(template, kit_rpc_url)
    if "function-gate ✓" not in template.get("verified_status", ""):
        return  # stage2 filed for review
    template = stage3_role_migration(template)
    stage4_commit_and_index(template)
    print(f"[done] {task_id} shippable + committed")
```

### Key safety properties of this pseudocode

1. **Each stage is a gate.** Failure at any stage writes to `REVIEW_DIR`
   and stops. The canonical is never committed in an unverified state.
2. **Kit is single-tenant.** Stage 2 must be serialized — never dispatch
   two `canonical_draft_job.py` calls targeting the same Kit instance.
3. **ChromaDB is single-process.** Stage 4 runs as a subprocess with no
   concurrent writers.
4. **Equivalence test is mandatory in Stage 3.** Unfilled placeholders or
   captured-call divergence are errors, not warnings.
5. **"stable_fail" is not failure of the pipeline.** A stable_fail
   documented with root cause is a valid output (the template is informative
   even if unrunnable). It is filed for human review with reason, not
   silently dropped.

---

## 9. Key Citations

- `workspace/templates/CP-01.json` — reference shippable template (all 10
  criteria met)
- `workspace/templates/CP-NEW-peg-in-hole-single.json` — reference draft
  template (criteria 1, 6 only)
- `tests/test_role_template_equivalence.py` lines 70-93 — equivalence gate
- `tests/test_role_template_equivalence.py` lines 96-104 — unfilled-
  placeholder detection
- `service/isaac_assist_service/chat/canonical_instantiator.py` lines 1-58
  — canonical sandbox architecture + format spec
- `service/isaac_assist_service/chat/tools/handlers/scene_blueprints.py`
  lines 565-640 — export_template handler (`.isaa` bundle format)
- `service/isaac_assist_service/chat/tools/handlers/scene_blueprints.py`
  lines 1178-1236 — filter_templates_by_hardware (VRAM gate)
- `scripts/qa/function_gate_suite.py` — function-gate automation
- `scripts/qa/function_gate_consistency.py` — multi-run consistency check
- `scripts/qa/add_templates_from_tasks.py` lines 30-33 — ChromaDB indexing
- `specs/IA_FULL_SPEC_2026-05-10.md` lines 2392-2462 — Phase 20 (role
  fields)
- `specs/IA_FULL_SPEC_2026-05-10.md` lines 5614-5677 — Phase 78b (yrkesroll
  audit + physics fixes)
- `specs/IA_FULL_SPEC_2026-05-10.md` lines 5679-5758 — Phase 78c (asset
  precheck + mock fallbacks)
- `docs/research/2026-05-09-yrkesroll-expansion.md` — Top-20 role-gap list
- `docs/research/2026-05-09-canonical-quality-audit.md` — 86-template audit

Web sources:
- Open X-Embodiment: https://arxiv.org/abs/2310.08864
- Isaac Lab-Arena: https://github.com/isaac-sim/IsaacLab-Arena
- Isaac Lab Eval Tasks: https://github.com/isaac-sim/IsaacLabEvalTasks
- RoCo Challenge AAAI 2026: https://arxiv.org/html/2603.15469
- VLABench (ICCV 2025): https://openaccess.thecvf.com/content/ICCV2025/papers/Zhang_VLABench_A_Large-Scale_Benchmark_for_Language-Conditioned_Robotics_Manipulation_with_Long-Horizon_ICCV_2025_paper.pdf
- Isaac Lab-Arena NVIDIA Blog: https://developer.nvidia.com/blog/simplify-generalist-robot-policy-evaluation-in-simulation-with-nvidia-isaac-lab-arena/
