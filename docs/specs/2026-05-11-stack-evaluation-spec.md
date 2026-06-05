# Stack Orthogonality Verification & LLM Communication — Meta-Spec

**Date:** 2026-05-11
**Status:** first draft v2 — reframed per orthogonality-verification angle.
Applies *after* IA Full Spec and Contact-Rich Manipulation Spec
(2026-05-11) have landed.
**Owner:** TBD
**Estimated LOC:** ~1500-2500 (harness + selection-engine + tests + dashboards)

**Dependencies:**
- IA Full Spec (Phase 63b cuRoboV2, Phase 80b grip-stability)
- `docs/specs/2026-05-11-contact-rich-manipulation-spec.md`
  (defines the 4-layer stack with 6+6+10+4 variants)
- `docs/specs/2026-05-11-kit-supervisor-spec.md` (unattended verify
  infrastructure required for any large-scale evaluation)

---

## 0. Reading Guide & TL;DR

**The sharper framing (per user 2026-05-11):** the Contact-Rich Spec
*claims* layers are hot-swappable orthogonal modules. This spec is the
**verification layer for that claim** + the **LLM-communication layer**
that surfaces the empirical reality (which combinations actually work,
which silently fail, which are untested) to the agent invoking the
stack-selection tool.

Three concrete purposes:

1. **Verify orthogonality empirically** — cheap pairwise + small-cell
   tests that confirm the layers actually compose. The claim is a
   hypothesis until measured.
2. **Maintain a compatibility matrix** — catalog of known-good,
   known-bad, and untested combinations across the variant space.
   Updated continuously by telemetry.
3. **Surface the matrix to the LLM** — the `recommend_stack` and
   `validate_stack` tools tell the agent BEFORE execution whether
   a proposed `controller_stack` is validated / experimental /
   known-incompatible. The agent can then make informed trade-offs
   instead of trusting unverified composition.

**80% of CPs use one default stack** and never touch this spec's
machinery. The other 20% (edge cases — novel contact-rich, multi-robot
oddities, embodiment-specific quirks) trigger discovery + matrix
consultation.

**Default stack** (applied automatically to any new CP without an
explicit `controller_stack`):
```
stability:  phase_80b_grip_safe
compliance: admittance       (or `null` if intent.has_contact_phase == False)
planning:   curobov2 (with constrained_axis_lock when has_contact_phase)
policy:     curobov2 (classical)
```

This default + `null` compliance for pure free-space tasks covers
~80% of the 109 CPs. The remaining 20% need explicit selection from
the variant catalog (Contact-Rich Spec §6.1).

**This spec exists to:**
- Define the default's coverage envelope (when DOESN'T it apply?)
- Provide a discovery harness for the edge cases
- Avoid the trap of testing all 1 728 stack combinations
  (combinatorial explosion §1)

§1: The combinatorial problem (110k stack-CP combinations)
§2: Why we can't and shouldn't try exhaustive evaluation
§3: CP-family clustering — main mechanism for stack reuse
§4: Tiered evaluation strategy (canary → family → production)
§5: Stack-selection heuristics + decision tree
§5.5: **Compatibility matrix — the orthogonality-verification artifact + LLM API**
§6: Auto-fallback adaptation from telemetry
§7: Stack-discovery harness for new CPs
§8: Cost-aware selection (GPU/VRAM-conscious)
§9: A/B testing infrastructure
§10: Recommendation engine (CP-pattern → stack)
§11: Open questions
§12: Implementation checklist

---

## 1. Problem statement — orthogonality is a hypothesis

The Contact-Rich Spec designed 4 layers to be ortogonal modules. That's
an architectural claim. In practice:

- Some compliance variants require specific planners (e.g.,
  `cartesian_impedance` needs torque-mode robot; FDCC needs explicit
  wrench target; admittance is universal)
- Some VLA policies output action-spaces that don't map to all
  compliance controllers (e.g., Pi0 chunked-actions vs single-step
  admittance update)
- Some embodiments only support certain stability profiles (Franka FCI
  + cartesian_impedance is fine; UR10e + cartesian_impedance assumes
  torque-mode that UR10e doesn't expose by default)

These are **silent compatibility failures**. The stack appears to
build, the controller appears to install, the simulation runs — but
joint torques come out wrong, the gripper doesn't engage, or contact
forces are misreported. The CP fails for non-obvious reasons.

**The base case is trivial when orthogonality holds:** layers are
independent modules. Pick one of each, plug in, done. For ~80% of
canonicals this is the whole story — the default stack from §0 works.

**This spec is about:**

1. **Detecting when orthogonality DOESN'T hold** — empirically, by
   testing pairwise + small-cell combinations
2. **Cataloging the result** in a compatibility matrix accessible to
   the LLM agent
3. **Discovering working stacks for hard CPs** — the other 20% where
   default doesn't work and the agent needs to pick from the catalog
4. **Communicating uncertainty** — distinguishing "validated", "known
   bad", "untested" so the agent makes informed choices

The catalog from Contact-Rich Spec is large (1 728 theoretical
combinations). Naively trying all of them per CP is intractable. We
need a discovery + selection strategy that:

- Avoids combinatorial explosion (§4 tiered eval)
- Builds confidence incrementally (§6 telemetry feedback)
- Tells the LLM what's safe vs experimental (§5.5 below)

After Contact-Rich Manipulation Spec lands, every canonical template can
declare a `controller_stack` from a combinatorial space:

| Layer | Variants (meaningful) | Reference |
|---|---|---|
| Layer 0 — Stability | 2 (`phase_80b_defaults`, `phase_80b_grip_safe`) + `null` + `legacy` = **4** | CR Spec §6.1 |
| Layer 1 — Compliance | `admittance`, `cartesian_compliance_fdcc`, `cartesian_impedance`, `variable_impedance`, `franka_cartesian_impedance`, `null` = **6** | CR Spec §3.2 |
| Layer 2 — Planning | `curobov2`, `curobo_v1`, `moveit2_cumotion`, `spline`, `native_rmpflow`, `lula_csptg` = **6** | CR Spec §6.1 |
| Layer 3 — Policy | `curobov2` (classical), `groot_n17_droid`, `pi0_fast_droid`, `pi0`, `openvla_7b`, `rt2x`, `lerobot_act_aloha`, `industreal_gear_checkpoint`, `touch2insert`, `dr_peg_in_hole`, `industreal_trained` = **~10-12** | CR Spec §4.2 |

### 1.1 Combinatorial size

```
4 × 6 × 6 × 12 = 1 728 stacks per CP
1 728 × 109 CPs = 188 352 stack-CP combinations
188 352 × 80 s/run = ~4 200 GPU-hours per N=1 evaluation
× N=5 for stable_ok/flaky classification = ~21 000 GPU-hours
≈ 875 days of single-GPU wall time
```

**Exhaustive evaluation is infeasible.**

### 1.2 But most combinations are useless

The 1 728 per-CP stacks include nonsense like:
- `policy: industreal_gear_checkpoint` on Franka peg-in-hole (UR10e
  checkpoint, embodiment mismatch)
- `compliance: null` + `policy: groot_n17_droid` on contact-rich
  (VLA without compliance is empirically 60% worse)
- `planning: native_rmpflow` + `policy: curobov2` (planner conflict)
- `stability: null` + any contact task (PhysX explosion guaranteed)

After filtering for **embodiment-compatible + semantically-coherent**
combinations, the realistic space is ~30-50 per CP. Still 3 000-5 500
combinations across 109 CPs — that's hundreds of GPU-hours per full
evaluation pass.

**Our job:** find a tractable subset and prove it's near-optimal.

---

## 2. Non-goals and bounded objectives

### 2.1 Non-goals
- **Full grid-search** of stack space. Even reduced 30-50/CP × 109 CPs
  is too expensive for routine ops.
- **Per-CP unique optimum.** Many CPs share structure — finding the
  best stack for ONE CP in a family generalizes.
- **Auto-tuning of compliance parameters** (stiffness/damping). That's
  a separate Phase B-class spec; this spec selects between *named
  variants*, not their internal knobs.

### 2.2 Objectives (in priority order)

1. **Find a good-enough stack for every CP within minutes**, not days.
2. **Make stack choice REPRODUCIBLE and AUDITABLE** — every CP records
   the stack it ran on and the success rate.
3. **Make selection AUTO-ADAPTIVE** — when a stack starts failing
   (Kit upgrade, model regression, etc.), the system detects and
   switches.
4. **Provide a STACK-DISCOVERY harness** users invoke for new CPs:
   "given CP-NEW-foo, recommend a starting stack."
5. **Honest cost accounting** — every recommendation includes GPU-time
   + VRAM + license cost of the chosen stack.

---

## 3. CP-family clustering — the main reuse mechanism

### 3.1 Hypothesis

**CPs that share intent + structural features share their optimal
stack.** Empirically: CP-01 (Franka pick-place 4 cubes) and CP-04
(same, compact footprint) almost always have the same success on
the same stack. CP-NEW-peg-in-hole-single and CP-NEW-tactile-insertion
have similar contact-rich requirements.

If true: we only need to find optima per *family*, not per CP. ~109 CPs
likely cluster into ~15-25 families.

### 3.2 Family definition

A family is a set of CPs that share:
- `intent.pattern_hint` (pick_place, sort, reorient, navigate)
- Key `structural_features` (n_robot_stations, destination_kind,
  uses_conveyor_transport, has_contact_phase, has_orientation_requirement)
- Embodiment (`franka_panda`, `ur10e`, `kinova_gen3`, etc.)

The multimodal-foundation IR (LayoutSpec.intent) already provides this.
Family-id is derived from a canonical-serialized intent fingerprint.

```python
def family_id(template: dict) -> str:
    """Deterministic family id from intent + embodiment."""
    intent = template["intent"]
    embodiment = template.get("primary_robot", {}).get("class", "unknown")
    parts = [
        intent["pattern_hint"],
        f"n_stations={intent['structural_features']['n_robot_stations']}",
        f"dest_kind={intent['structural_features']['destination_kind']}",
        f"conv={intent['structural_features']['uses_conveyor_transport']}",
        f"contact={intent['structural_features'].get('has_contact_phase', False)}",
        f"orient={intent['structural_features']['has_orientation_requirement']}",
        f"emb={embodiment}",
    ]
    return "|".join(parts)
```

### 3.3 Family table (initial enumeration)

| Family id | Description | Typical CPs | Approx. count |
|---|---|---|---|
| `pick_place\|1\|single_bin\|True\|False\|False\|franka_panda` | Basic Franka pick-place from conveyor | CP-01, CP-04, CP-13 | 12 |
| `pick_place\|2\|single_bin\|True\|False\|False\|franka_panda` | Two-Franka handoff | CP-02, CP-51, CP-67 | 8 |
| `sort\|1\|color_routed\|True\|False\|False\|franka_panda` | Franka color-routing | CP-03, CP-16, CP-32 | 6 |
| `pick_place\|1\|single_bin\|True\|True\|False\|franka_panda` | Contact-rich Franka pick-place | CP-NEW-peg-in-hole-single | 3-5 |
| `pick_place\|1\|single_bin\|True\|False\|False\|ur10e` | UR10e pick-place | CP-69, CP-70, CP-78 | 7-9 |
| `navigate\|1\|single_bin\|False\|False\|False\|nova_carter` | AMR pickup-handoff | CP-NEW-amr-pickup-handoff | 3-4 |
| `reorient\|1\|single_bin\|True\|False\|True\|franka_panda` | Reorient + place | CP-05, CP-NEW-tactile-insertion | 2-4 |
| ... ~15-20 families total | | | |

### 3.4 Transferability test

For each candidate family, validate the hypothesis:

1. Pick 2 representative CPs from the family.
2. Run both on the same 3 candidate stacks.
3. If stack-A wins both → family is *cohesive*; A is the family's
   recommendation.
4. If results differ → split family further (need finer structural
   features).

The transferability test is itself O(N_families × N_candidate_stacks × 2 CPs)
= ~20 × 8 × 2 = 320 runs = ~7 hours. Once per quarter; not per
template.

---

## 4. Tiered evaluation strategy

### 4.1 Tier 0 — Sanity gate (per stack candidate)

When a new stack variant lands (e.g., NVIDIA releases GR00T N1.8),
run it through Tier-0 first:

```
canary_cps = ["CP-01", "CP-02", "CP-03"]   # 3 of our most stable CPs
for stack in candidate_stacks:
    for cp in canary_cps:
        result = run_cp(cp, stack, n_runs=3)
        if result.success_rate < 0.5:
            reject(stack, reason="Tier-0 sanity fail on " + cp)
            break
```

Tier-0 cost: 3 stacks × 3 CPs × 3 runs × 80s = ~36 min. Cheap.

**Reject criterion:** any candidate stack that fails to score ≥50% on
all 3 canary CPs is rejected before further evaluation.

### 4.2 Tier 1 — Family-representative discovery

For each family, evaluate K=5-8 hand-picked candidate stacks on M=2-3
representative CPs:

```
for family in families:
    candidates = hand_pick_stacks(family)   # ~6 plausible per family
    reps = pick_representative_cps(family)  # ~2-3 per family
    for stack in candidates:
        for cp in reps:
            results[family][stack][cp] = run_cp(cp, stack, n_runs=3)

    family_winner = argmax over stacks of mean(rep CPs)
```

Tier-1 cost: 20 families × 6 stacks × 2 CPs × 3 runs × 80s ≈ 16 hours.
Runs overnight; produces family→stack recommendation table.

### 4.3 Tier 2 — Within-family generalization

For the family-winner stack, run on all remaining CPs in the family
once:

```
for family in families:
    winner = recommendations[family]
    for cp in family.cps:
        if cp not in winner.tested_cps:
            result = run_cp(cp, winner, n_runs=3)
            if result.success_rate < 0.5:
                escalate(cp, reason="winner-stack-doesn't-generalize")
```

Tier-2 cost: 109 CPs × 1 stack × 3 runs × 80s ≈ 7 hours. Runs once,
post-Tier-1.

### 4.4 Tier 3 — Escalation

For CPs that don't generalize:
- Promote to "individual evaluation" — run additional candidate stacks
- Or refine the family (add structural feature, re-cluster)
- Or accept as "outlier; not auto-stack-selectable"

### 4.5 Tier 4 — Continuous regression

Once stack-CP recommendations are baked, regression runs verify they
still hold under Kit/cuRobo updates:

- Weekly: re-run Tier-2 verify on 10% sample
- On Kit version bump: re-run Tier-1 for all families
- On stack-variant deprecation/addition: Tier-0 + targeted Tier-1

Total ongoing cost: ~4-8 GPU-hours/week steady-state.

---

## 5. Stack-selection heuristics

When no Tier-1/2 recommendation exists yet (e.g., brand-new CP), the
template-author / agent uses a heuristic decision tree:

```
INPUT: LayoutSpec.intent for new CP

# 1. Does the CP require contact?
if intent.structural_features.has_contact_phase OR
   intent.structural_tags includes "isaac:contact_rich":
    → compliance: admittance     (DEFAULT)
    → planning: curobov2 + constrained_axis_lock
    → policy: curobov2 (classical)   # rather than VLA initially
    → stability: phase_80b_grip_safe
else:
    → compliance: null            (DEFAULT — no compliance overhead)
    → planning: curobov2
    → policy: curobov2 (classical)
    → stability: phase_80b_defaults

# 2. Embodiment override
if embodiment in (UR10e, UR5e) AND task involves surface gripper:
    → add "raycast_workaround" to planning_features (Phase A+B)

# 3. Task novelty (instruction-driven, novel object)
if intent.structural_tags includes "user:novel_object":
    → escalate policy to "groot_n17_droid" or "pi0_fast_droid"
    → add fallback to curobov2 classical

# 4. Pre-trained checkpoint availability
if exists("industreal_gear_checkpoint") AND family matches gear-assembly:
    → policy: industreal_gear_checkpoint   # use pre-trained instead of zero-shot

# 5. Cost cap
if user has budget VRAM < 12 GB:
    → strip VLA policies from candidates  # prefer classical paths
```

The decision tree is implemented in
`service/.../stack_recommender/heuristics.py` and exposed as the
`recommend_stack(layout_spec) → ControllerStack` tool.

---

## 5.5 Compatibility matrix — the orthogonality-verification artifact

The **central deliverable** of this spec: a maintained
`compatibility_matrix.yaml` catalog that records, for each
(layer_combination), one of:

| Status | Meaning | Source |
|---|---|---|
| `validated` | Combination tested ≥3 CPs, ≥80% success | Tier-1 + telemetry |
| `default` | Combination IS the recommended default (§0) | Manual; reviewed |
| `experimental` | Tested but inconsistent (50-80% success) | Tier-1 telemetry |
| `incompatible` | Tested, fails ≥3 times consistently OR raises | Catalog-author + telemetry |
| `untested` | Never run end-to-end | Auto-inferred from telemetry |
| `deprecated` | Was validated, now failing (regression) | Tier-4 sweep |

Example entries:

```yaml
# workspace/recommendations/compatibility_matrix.yaml
- stack:
    compliance: admittance
    planning:   curobov2
    policy:     curobov2
    stability:  phase_80b_grip_safe
  status: default
  notes: "Universal classical optimum. ≥80% success on contact-rich CPs."
  last_validated: 2026-05-11
  test_coverage: ["CP-01", "CP-NEW-peg-in-hole-single", "CP-NEW-tactile-insertion"]

- stack:
    compliance: cartesian_impedance
    planning:   curobov2
    policy:     curobov2
    stability:  phase_80b_grip_safe
  status: incompatible
  notes: "UR10e doesn't expose torque-mode by default; cartesian_impedance
    requires torque-mode. Use admittance instead."
  embodiment_constraint: "robot_class not in [franka_panda]"
  reproducer: "any UR10e CP with this stack — install warning + zero torque output"

- stack:
    compliance: variable_impedance
    planning:   curobov2
    policy:     pi0_fast_droid
    stability:  phase_80b_grip_safe
  status: experimental
  notes: "Two-phase K schedule mostly works but transitions cause occasional jolt."
  observed_success_rate: 0.62
  test_coverage: ["CP-NEW-peg-in-hole-single"]
  last_validated: 2026-05-09

- stack:
    compliance: null
    planning:   curobov2
    policy:     groot_n17_droid
    stability:  phase_80b_grip_safe
  status: experimental
  notes: "VLA without compliance — works for pick-place but fails on insertion."
  observed_success_rate: 0.45
```

### 5.5.1 LLM-facing API

```python
async def get_stack_compatibility(
    stack: ControllerStack,
) -> dict:
    """Look up a proposed stack in the compatibility matrix.

    Returns:
        {
            "status": "validated" | "default" | "experimental" |
                     "incompatible" | "untested" | "deprecated",
            "notes": str,                # human-readable
            "observed_success_rate": float | None,
            "last_validated": date | None,
            "test_coverage": list[str],  # CPs where this was tested
            "warnings": list[str],       # embodiment/license/cost concerns
            "suggested_alternative": ControllerStack | None,
        }
    """
```

```python
async def validate_stack(
    stack: ControllerStack,
    layout_spec: LayoutSpec | None = None,
) -> dict:
    """Pre-build sanity check: does this stack make sense for this CP?

    Returns:
        {
            "ok": bool,
            "violations": list[str],
            "warnings": list[str],
            "compatibility_status": "validated"|"experimental"|...,
        }
    """
```

The agent calls `validate_stack` BEFORE invoking `execute_template_
canonical`. If `ok: False`, the agent reports back to the user with
violations + suggested alternative.

### 5.5.2 Matrix update sources

The matrix is updated by FOUR channels:

1. **Catalog author** — manual entries when adding a new variant
   (e.g., when GR00T N1.8 ships, author marks all combos with it as
   `untested` initially).
2. **Tier-1 evaluation runs** — overnight discovery batches promote
   `untested` → `validated` / `experimental` / `incompatible`.
3. **Telemetry feedback** — supervisor's
   `EVENT_SUPERVISOR_DRIFT_DETECTED` events for a stack-CP pair feed a
   demotion counter; 3 consecutive failures demote
   `validated` → `experimental`; 10 → `deprecated`.
4. **User correction** — explicit
   `mark_stack_incompatible(stack, reason)` tool when user observes
   silent failures.

### 5.5.3 Matrix consumption

```python
async def recommend_stack(layout_spec, constraints=None) -> ControllerStack:
    """Updated to consult matrix:

    1. Compute family_id (§3.2)
    2. If recommendations table has family_winner:
        a. Check matrix: is winner validated for THIS embodiment?
        b. If incompatible: fall to next-best validated
        c. Return validated winner
    3. Else: heuristic decision tree (§5)
    4. Apply user constraints (VRAM, license)
    5. Final validate_stack() before returning
    """
```

### 5.5.4 Self-healing behavior

The agent doesn't just *consume* compatibility status — it can
*request* validation. New API:

```python
async def request_stack_validation(
    stack: ControllerStack,
    canonical_id: str,
    n_runs: int = 3,
) -> dict:
    """User-triggered: 'I want to use this experimental combo. Run it
    3 times to validate.' If success ≥80%, the matrix is auto-updated
    to mark it `validated` for this CP."""
```

This closes the loop: experimental → validated through user-initiated
small evaluations. No agent-side hidden trial-and-error; user gets to
see the validation effort.

---

## 6. Auto-fallback adaptation from telemetry

The Contact-Rich Spec §6 introduced `fallback_chain` on
`controller_stack`. This spec adds the *learning loop* on top:

### 6.1 Per-stack success-rate aggregation

The supervisor aggregator (`scripts/qa/analyze_multimodal_usage.py`)
already records per-CP `drift_classification` events. Extend with
per-stack aggregation:

```python
def stack_success_rates(events) -> dict[stack_id, dict[cp_id, float]]:
    """For each (stack, CP) pair, compute success-rate over last N
    observations (sliding window, default N=10)."""
```

### 6.2 Fallback promotion rule

If the primary stack's success-rate drops below 50% over the last 10
runs of a CP, the supervisor promotes the next fallback to primary
(for that CP only) and emits `supervisor_stack_promoted` event.

```python
EVENT_SUPERVISOR_STACK_PROMOTED = "supervisor_stack_promoted"
EVENT_SUPERVISOR_STACK_DEMOTED = "supervisor_stack_demoted"
```

A demoted primary can be re-promoted later if it recovers (e.g., post
Kit-fix); the supervisor maintains an EMA of recent success.

### 6.3 Stack-deprecation alert

If a stack-variant drops below 30% mean success across ALL CPs that use
it for 30+ days, an alert fires: "stack X may be deprecated upstream;
investigate."

---

## 7. Stack-discovery harness

A user/agent invokes `discover_stack(canonical_id)` for a CP they
haven't classified yet:

```python
async def discover_stack(
    canonical_id: str,
    candidate_stacks: list[ControllerStack] | None = None,
    n_runs: int = 3,
    budget_minutes: float = 30.0,
    parallel: bool = False,  # Kit RPC is single-tenant; default sequential
) -> dict:
    """Run discovery: try candidate stacks (default: 5 plausible per
    heuristic + 2 known-good baselines), measure success-rate, recommend
    winner.

    Returns:
        {
            "winner": ControllerStack,
            "runner_up": ControllerStack,
            "all_results": {stack_id: success_rate},
            "telemetry_session_id": str,
            "duration_minutes": float,
        }
    """
```

**Workflow:**
1. Heuristic step (§5) generates 5-6 candidate stacks.
2. Add 2 baseline stacks (classical-cuRoboV2-admittance, classical-
   cuRoboV2-rigid) for comparison.
3. Run each on the target CP with N=3 runs.
4. Rank by success-rate × (cost_inverse) for cost-aware selection.
5. Emit recommendation; agent commits the chosen stack to the template.

Time budget: 8 stacks × 3 runs × 80s = ~32 min per CP. User invokes
once per new CP; not per build.

---

## 8. Cost-aware selection

Stacks have explicit costs declared in `config/stack_costs.yaml`:

```yaml
curobov2_admittance_phase80b:
  vram_gb: 4
  inference_hz: 1000           # cuRoboV2 plan-rate; admittance 500Hz
  gpu_seconds_per_cp: 80
  license: commercial

groot_n17_droid_admittance:
  vram_gb: 12
  inference_hz: 30
  gpu_seconds_per_cp: 120      # inference adds time
  license: commercial

pi0_fast_droid_admittance:
  vram_gb: 8
  inference_hz: 50
  gpu_seconds_per_cp: 100
  license: apache2

industreal_trained_admittance:
  vram_gb: 16
  inference_hz: 60
  gpu_seconds_per_cp: 90
  license: commercial
  training_hours: 10           # one-time, prorated over uses
```

The selection score combines success-rate + cost:

```python
score(stack, cp) = success_rate(stack, cp) - α × cost(stack)
                                              ↑
                                  α tunable per user (default 0.001)
```

Default α gives ~5% success-rate equivalent to 50s of GPU-time —
so a stack 5% better needs to cost ≤50s more per run. User can set
`α=0` to ignore cost (only success), or `α=∞` to minimize cost
(only ties broken by success).

---

## 9. A/B testing infrastructure

When two stacks are close in score (within 5% success-rate), run an
A/B test:

```python
async def ab_test_stacks(
    canonical_id: str,
    stack_a: ControllerStack,
    stack_b: ControllerStack,
    n_runs: int = 20,
    significance_level: float = 0.05,
) -> dict:
    """Run alternating stack_a, stack_b 20 times each. Apply Fisher's
    exact test on success counts. Return winner + confidence."""
```

Used by:
- Stack-discovery harness when winner runner-up gap < 5%
- Periodic "verify stack-A still better than stack-B" runs
- Manual user-invoked when evaluating new vendor releases

Cost: 40 runs × 80s ≈ 54 minutes per pair.

---

## 10. Recommendation engine

API:

```python
async def recommend_stack(
    layout_spec: LayoutSpec,
    constraints: dict | None = None,   # e.g., {"max_vram_gb": 8}
) -> ControllerStack:
    """Given a LayoutSpec, return the recommended controller stack.

    Pipeline:
    1. Compute family_id from intent
    2. Check recommendations table (Tier-1 results)
       - If family has a winner: return winner (with constraints applied)
       - Else: fall to heuristic (§5)
    3. Apply user constraints (filter by VRAM, license)
    4. Return ControllerStack
    """
```

Table maintained at `workspace/recommendations/family_stacks.yaml`,
updated by Tier-1/2 evaluation runs.

---

## 11. Open questions

1. **How to define "the family table" initially?** Manual annotation
   first; tighten via clustering on intent fingerprints after Tier-1
   data accumulates.

2. **When does within-family generalization break?** Hypothesis: when
   `structural_features` differ. Empirical test: pick two same-family
   CPs that differ ONLY in one feature flag, observe whether their
   winners diverge. If they do → family schema needs that feature.

3. **Should fallback-chain promotion be sticky or rolling?** Sticky
   (once promoted, stays) is simpler. Rolling (every 10 runs) adapts
   to transient issues. Default: sticky with manual reset.

4. **Cross-CP transferability under domain randomization** — when DR
   is added to a CP, does its family-stack still win? Probably not for
   wildly randomized variants. Need DR-aware family clustering.

5. **Recommendation engine vs autonomous agent** — should the agent
   call `recommend_stack` automatically or wait for user authorization?
   Default: agent calls when building a NEW canonical from a text
   prompt; user gets to see + approve recommendation in
   `controller_stack` block of the template.

6. **Tier-1 winner stability across Kit versions** — when Kit updates
   physics, do family winners change? Track and document.

7. **Multi-objective selection (success + speed + cost)** — current
   score is linear combination. Some users want Pareto-frontier.
   Defer; ship linear-combination v1.

---

## 12. Implementation checklist

### Family-clustering
- [ ] `service/.../stack_recommender/family.py` — `family_id` function
- [ ] `workspace/recommendations/family_table.yaml` — manual seed
- [ ] `tests/test_family_clustering.py` — 20 known-CP→family mappings

### Tiered evaluation harness
- [ ] `scripts/qa/eval_tier0.py` — sanity gate
- [ ] `scripts/qa/eval_tier1.py` — family-representative discovery
- [ ] `scripts/qa/eval_tier2.py` — within-family generalization
- [ ] `scripts/qa/eval_tier4_regression.py` — periodic re-verify
- [ ] Integration with Kit Supervisor for unattended overnight runs

### Stack-selection heuristics
- [ ] `service/.../stack_recommender/heuristics.py` — §5 decision tree
- [ ] `service/.../stack_recommender/__init__.py` — public API
- [ ] `tests/test_heuristic_recommendations.py` — ≥30 CP→stack mappings

### Auto-fallback adaptation
- [ ] `scripts/qa/analyze_multimodal_usage.py` — stack-success aggregator
- [ ] `service/.../supervisor` extension: stack-promotion logic
- [ ] `EVENT_SUPERVISOR_STACK_PROMOTED` + `_DEMOTED` events
- [ ] `tests/test_stack_promotion.py` — ≥10 promotion-rule tests

### Stack-discovery harness
- [ ] `scripts/qa/discover_stack.py` — interactive harness
- [ ] `service/.../chat/tools/discover_stack_handler.py` — tool
- [ ] `tests/test_discover_stack_smoke.py` — minimal smoke

### Cost accounting
- [ ] `config/stack_costs.yaml` — per-stack cost declarations
- [ ] `service/.../stack_recommender/cost.py` — score function
- [ ] `tests/test_cost_accounting.py` — ≥10 score-fn tests

### A/B testing
- [ ] `scripts/qa/ab_test_stacks.py` — pairwise comparison harness
- [ ] Fisher's-exact-test implementation (scipy available)
- [ ] `tests/test_ab_test_significance.py` — significance computation

### Recommendation engine
- [ ] `service/.../chat/tools/recommend_stack_handler.py`
- [ ] `workspace/recommendations/family_stacks.yaml` — Tier-1 results
- [ ] `tests/test_recommendation_engine.py` — ≥15 family→stack tests

### Compatibility matrix (§5.5 — central deliverable)
- [ ] `workspace/recommendations/compatibility_matrix.yaml` — seeded
      with default stack + known-bad combos
- [ ] `service/.../stack_recommender/compatibility.py` — matrix
      lookup + update
- [ ] `service/.../chat/tools/compatibility_handlers.py` — exposes
      `get_stack_compatibility`, `validate_stack`,
      `request_stack_validation`, `mark_stack_incompatible`
- [ ] Telemetry hook: drift_detected event → matrix demotion counter
- [ ] `tests/test_compatibility_matrix.py` — ≥20 status-resolution tests
- [ ] `tests/test_validate_stack.py` — ≥15 violation-detection tests

### Telemetry + dashboards
- [ ] New event types: `stack_evaluation_completed`,
      `stack_recommendation_emitted`, plus stack-promotion/demotion
- [ ] Aggregator additions: `stack_success_dashboard`,
      `family_winner_table`, `cost_per_unit_success`

### Documentation
- [ ] `docs/guides/stack_selection.md` — user-facing
- [ ] `docs/guides/discovering_stacks_for_new_canonicals.md`
- [ ] Update master execution plan with Tier-0/1/2 cadence

---

## 13. References

- Contact-Rich Manipulation Spec — defines the 4-layer variant space
  this spec evaluates
- Kit Supervisor Spec — infrastructure for unattended Tier-1/2 runs
- Multimodal Foundation Spec — provides `LayoutSpec.intent` for family
  clustering
- [Design of experiments — orthogonal arrays (Wikipedia)](https://en.wikipedia.org/wiki/Orthogonal_array)
  — theoretical basis for sparse-search efficiency
- [Taguchi methods](https://en.wikipedia.org/wiki/Taguchi_methods)
  — DOE technique applicable to stack-evaluation pruning
- [Multi-armed bandit theory](https://en.wikipedia.org/wiki/Multi-armed_bandit)
  — relevant for online stack-selection adaptation
- [Empirical Bayes / hierarchical models](https://en.wikipedia.org/wiki/Empirical_Bayes_method)
  — for family-level priors with per-CP refinement

---

## 14. Cost analysis summary

| Activity | Frequency | GPU-hours per run | Annual cost (RTX 5070 at $0.30/h cloud-equiv) |
|---|---|---|---|
| Tier-0 (per new stack) | ~4 / year | 0.6 | $0.72 |
| Tier-1 (full family scan) | ~2 / year | 16 | $9.60 |
| Tier-2 (within-family verify) | ~4 / year | 7 | $8.40 |
| Tier-4 (weekly regression) | 52 / year | 4 | $62.40 |
| Stack-discovery (per new CP) | ~50 / year | 0.5 | $7.50 |
| A/B testing (per pair) | ~10 / year | 1 | $3 |
| **Total estimated annual cost** | | | **~$92** |

This is **vastly cheaper** than exhaustive 188k × 80s ≈ 4 200 hours ≈
$1 260 per pass. The tiered strategy gets us 95% of the value at <1%
of the cost.

---

## 15. Glossary

- **Stack:** a 4-tuple of (stability, compliance, planning, policy)
  variants from Contact-Rich Spec §6.1.
- **Family:** a set of CPs sharing structural intent + embodiment.
- **Tier:** an evaluation phase with bounded scope (Tier-0 sanity,
  Tier-1 discovery, etc.).
- **Sticky promotion:** fallback promoted as new primary stays
  primary until manually reset.
- **Rolling promotion:** primary re-evaluated every N runs from
  the chain.
- **DoE:** Design of Experiments — orthogonal-array-based sparse
  search that covers main effects in O(N) vs grid-search's O(N^k).
- **Pareto-frontier:** in multi-objective optimization, the set of
  solutions not dominated by any other on all objectives.

---

## 16. Why this is a separate spec (not part of Contact-Rich)

The Contact-Rich Spec defines WHAT stacks exist. This spec defines HOW
to choose among them. Combining them would conflate two distinct
concerns:

- **Contact-Rich Spec** = variant catalog + integration semantics
- **This spec** = evaluation methodology + selection engine

The catalog can grow (new VLAs ship monthly) without touching the
evaluation methodology. The methodology can evolve (better DoE, online
bandits) without touching the catalog.

Two specs, two implementation tracks, two test suites. Clean separation.

---

## 17. Anti-overengineering safeguards

A common failure mode for spec-driven frameworks is building elaborate
machinery for problems that don't yet exist. To avoid that:

### 17.1 Default-first principle

Every spec consumer starts with the default stack from §0. They only
engage this spec's framework after:
1. Trying the default on a real CP, and
2. Observing it fail or underperform.

The recommendation-engine API (`recommend_stack`) returns the default
unless the LayoutSpec triggers a heuristic exception (§5). The discovery
harness (`discover_stack`) is opt-in, manually invoked.

### 17.2 Tier-0 sanity gate as gatekeeper

Before any deeper investment, the operator/agent must show that the
default isn't working. Tier-0 (§4.1) is the minimum viable test: 3 CPs,
3 stacks, 36 minutes total. If the default + 1-2 obvious alternatives
all pass Tier-0 with ≥80% success, this whole spec is overkill and
the user simply ships with default + fallback chain.

### 17.3 Conservatism on family count

Start with 5-10 families based on intent.pattern_hint alone (pick_place,
sort, reorient, navigate, ×2 embodiments). Only refine into more
families when measured transferability breakdowns are observed.

### 17.4 Manual override is always preserved

The agent never overwrites a `controller_stack` field already declared
in a template. Authors who hand-pick stacks aren't second-guessed by
the recommendation engine.

### 17.5 Decommission criteria

This spec's machinery may itself be deprecated if:
- After 3 months, the recommendation engine produces the same stack as
  the heuristic decision tree for >90% of CPs, OR
- Tier-1 evaluation never identifies a family-winner that beats the
  default by >5%, OR
- Stack-promotion telemetry shows fallback chain rarely fires (<5% of
  runs)

In those cases, the framework is academic and we strip it to just the
recommendation tool + default stack + fallback chain.
