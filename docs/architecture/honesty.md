# The IA Honesty Charter

> Honesty Charter §0 — canonical location for back-references from
> implementing phases.

Status: charter — stable. Cite as "Honesty Charter §<section>" from any
phase, handler, or validator that implements one of its obligations.

---

## 1. Statement of principle

**IA must never report a state, score, verdict, or success that it
cannot defend against the live stage, the gap log, or the user's
verification step.**

This is the one sentence that the rev. 2 spec spends twelve phases
re-stating in different surfaces. Every honesty obligation in the
system — silent-success elimination, deterministic critics, bridge
lifecycle accounting, validator-rule enforcement, gap-log dual
verification, mock-mode tagging, model-error escalation — collapses to
the same demand: when IA produces a claim, the claim has to be
*defensible*. Defensibility means three things, and exactly three. (a)
The claim can be traced to a measurement of the live stage that the
user could rerun. (b) The claim can be traced to a gap-log entry that
records what was compared, by what metric, and at what threshold. (c)
The claim can be traced to a verification step the user explicitly
authorised, with the result captured. A claim that satisfies none of
these is, by definition, a fabrication, and the charter exists to make
fabrications structurally impossible — not "discouraged", *impossible*
— inside an IA phase.

The charter is not a roadmap. It is a *legibility document*. Reviewers
reading any new phase should be able to consult the charter's four
cross-cutting checks (Section 4) and decide in under a minute whether
the phase honours the principle. If the phase fails any check, it is
either (i) not ready for review, or (ii) intentionally exempt with an
explicit, register-recorded waiver. There is no third option.

## 2. The three failure modes IA refuses

### 2.1 Silent success

A handler returns `{"success": True}` while having performed none, or
only part, of the work the tool name promises. The 2026-04-18
`tool_executor` audit catalogued 344 handlers and found ten honesty
holes of exactly this shape: a `try` block that no-ops on internal
failure, a status assembly that ignores the no-op, and a return value
that lies. Phase 47b is the long-tail eradicator for this mode — every
handler must either *do the work* and report the new state, or *not
claim success*. The two acceptable shapes are `success=True with
verifiable state-change` and `success=False with error reason`. The
forbidden shape is `success=True with no state-change` regardless of
internal reason. Optimistic try/except blocks that swallow the
exception and return `True` are the canonical violation pattern.

### 2.2 Optimistic critic

A scoring function whose output drifts across invocations on the same
input, so two iterations of an optimisation loop are not
*commensurable*. The most expensive form of this is an LLM-as-critic
where the prompt is not fully captured and the seed is uncontrolled —
score(t+1) cannot be compared to score(t) because the rubric quietly
shifted. Phase 45 replaces every drift-prone critic with a
deterministic Math Critic: the score is a pure function of the
recorded inputs, and reruns on the same inputs are bit-equal. If the
domain genuinely needs a soft critic (e.g. multimodal scene quality),
the critic must (a) record its full prompt + model + seed, and (b)
emit a confidence interval, not a point estimate.

### 2.3 Absorbed model error

The analytical model and the simulator disagree, and instead of
surfacing the disagreement, IA quietly applies a correction
("calibration", "tuning", "scaling factor") that makes the discrepancy
disappear. This is the most dangerous failure mode because it
*increases* internal coherence — the residual goes down — while
*decreasing* external validity — the next prediction in a different
regime will be wrong by a larger margin. Phase 56's recalibration
policy disallows silent absorption: any analytical-vs-simulation gap
that exceeds the threshold escalates to
`model_error_pending_review`, and the gap log records the
disagreement until a human (or Phase 56b's BCa CI gate) authorises
either a correction or a model swap. Absorbed model error is the
charter's most subtle target precisely because it can look like
progress.

## 3. Phase-by-phase implementing register

Every phase in this register carries an honesty obligation and should
back-link to this charter (the audit script in Section 6 enforces the
back-link).

- **Phase 11 — Patch validator pluggable pipeline.** Validator failure
  modes must be named, not collapsed into "patch rejected".
- **Phase 11b — Generic ConstraintViolation framework.** Canonical
  reporting shape for any rule violation; the charter cites it as the
  standard error-channel.
- **Phase 11c — Controller `ctrl:*` namespace unification.** Attribute
  ownership must be explicit; silent attribute clobbering is a Phase
  11c violation.
- **Phase 31b — Bridge lifecycle honesty.** Bridge state transitions
  (attach / detach / fault / reattach) must be reported truthfully;
  no "phantom attached" status.
- **Phase 42 — Governance & runtime safety.** Forbidden-term scanner +
  policy verdict logging; verdicts are defensible from the policy
  trace.
- **Phase 45 — Math Critic replacement.** Deterministic scoring; see
  Section 2.2 above.
- **Phase 47 — Validator rule enforcement.** Validator warnings must
  block, not annotate.
- **Phase 47b — Honesty long-tail.** Silent-success eradication across
  the handler population; see Section 2.1 above.
- **Phase 49b — Cache key honesty.** A cache hit must be a hit on the
  *complete* input set; partial-key cache hits are a Phase 49b
  violation.
- **Phase 53 — Dual contract.** Analytical and simulation paths both
  publish results; agreement / disagreement is logged.
- **Phase 54 — Gap log schema.** Canonical record of every comparison
  IA makes; the charter's "gap log" arm refers to this schema.
- **Phase 56 — Recalibration policy.** `model_error_pending_review`
  escalation; see Section 2.3 above.
- **Phase 56b — BCa confidence interval gate.** Soft-critic
  intervals; replaces point-estimate misuse.
- **Phase 78c — Mock-mode tagging.** Every response from a mock
  fallback is tagged so downstream consumers cannot mistake it for a
  live measurement.
- **Phase 83 — Overnight chain governance.** Multi-step autonomous
  chains must surface per-step verdicts; the chain cannot report a
  composite success that hides a failed sub-step.
- **Phase 88b — Production sandboxing.** Production-mode handlers
  must declare their side-effects; silent network or disk writes are
  a Phase 88b violation.

## 4. The four cross-cutting checks any new IA phase must answer

1. **State authoring.** Does this phase author state (USD prim,
   handler-return field, gap-log row, cache entry, persisted record)
   that needs verification? If so, where is the verifier, and what
   does it check?
2. **Score determinism.** Does this phase compute a score, ranking,
   confidence, or other comparable number? If so, is the score a pure
   function of the recorded inputs, and if not, what is recorded
   alongside the score so reruns can be diagnosed?
3. **Verdict defensibility.** Does this phase report a verdict
   (pass / fail / warn / accepted / rejected)? If so, can the verdict
   be reconstructed from the gap log entries alone, without consulting
   the phase's internals?
4. **Stage mutation guard.** Does this phase mutate the live USD
   stage, the file system, or any externally visible state? If so, is
   the mutation guarded by the Phase 47b silent-success eliminator —
   meaning, on failure, the handler returns `success=False` with a
   named error, not a partial / silent / optimistic `True`?

A phase that answers "no" to all four is not in scope for the charter.
A phase that answers "yes" to any of the four must back-link to the
relevant section above.

## 5. Anti-patterns explicitly disallowed

These shapes appeared in the project's production history and must
never be re-introduced.

- **Recovered-state metadata blocks.** The `tool_executor.py:33-1572`
  forensic block is archived because it embedded handler internals as
  a giant "what we recovered" metadata structure. The pattern lies by
  construction — it presents a rebuilt state as if it were measured.
  Never re-introduce a "recovered state" payload that is not derived
  from a re-read of the live stage. (Phase 13 archives the historical
  block to `docs/forensics/`; do not resurrect it.)

- **Silent `Apply` on invalid prims.** `UsdPhysics` /
  `PhysxSchema` API-schema application silently no-ops if the prim is
  not the schema's expected type. The 2026-04-18 audit found
  `ApplyAPISchemaCommand` calls that returned `success=True` while
  having applied nothing because the target prim was wrong. Every
  schema application must explicitly check (a) the prim is valid and
  defined, (b) the prim type is one the schema accepts, before
  invoking `Apply`. A failure to apply must return `success=False`
  with a reason; it must never be silently absorbed.

- **Validator warnings that don't block.** Phase 72c documents the
  exact incident: `validate_scene_blueprint` returned `valid=True`
  with `warnings=[…]` listing AABB overlaps that should have been
  hard failures. Warnings that signal a defensibility break must
  block; if a warning is recoverable, it is not a warning, it is an
  *info* note and should be tagged as such. The charter forbids
  `valid=True` co-existing with warnings that, if taken seriously,
  invalidate the result.

- **Opaque `plan_fail`.** Phase 63c documents cuRobo's
  `plan_pose` failing 24/24 in a handler context without surfacing
  which sub-stage failed (IK reachability? collision check? warp
  scene-collision gap?). Every planner / solver / multi-stage
  pipeline must name the failed sub-stage in its error return. A
  failure return that says only `plan_fail=True` is, by the charter,
  a fabricated verdict — the verdict is not defensible because the
  failed sub-stage is not named.

---

## 6. Enforcement

The companion script `scripts/audit_honesty_links.py` scans every
phase in `specs/IA_FULL_SPEC_2026-05-10.md` and reports whether the
phases in the implementing register (Section 3) carry a back-link to
this charter. The default mode is reporting; `--strict` exits
non-zero if any register phase lacks a back-link. The script does
*not* mutate the spec — back-link insertion is a separate manual
editing pass.

Reviewers do not have to memorise the charter. The audit is the
CI-enforced read. If a new phase introduces an honesty obligation,
the obligation enters the register; if a register phase loses its
back-link, the audit flags it; if the charter prose itself changes,
the section numbers stay stable so existing back-links survive.
