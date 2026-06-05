# {release_version} Retrospective

> **Generated from:** `docs/templates/retrospective_template.md`
> **Template version:** 1.0.0
> **Instructions:** Replace all `{placeholder}` tokens with real values before publishing.
> Sections marked **(required)** must be filled in before sharing with stakeholders.

---

## Release Summary

| Field | Value |
|-------|-------|
| **Release version** | {release_version} |
| **Release date** | {release_date} |

### Primary Goals

{primary_goals}

### Success Metrics

{success_metrics}

---

## What Went Well

Record wins, smooth processes, effective decisions, and pleasant surprises that
should be **repeated** or **scaled** in future releases.

{went_well}

**Examples of things that typically go well:**

- Core feature delivery landed on the agreed scope with zero scope creep.
- Automated test coverage caught three regressions before they reached staging.
- Cross-team communication was faster than usual thanks to the shared Slack channel.
- Deployment rollout was fully automated and required no manual intervention.
- Post-deploy monitoring alerted the on-call engineer within 90 seconds of the
  only anomaly; rollback was completed before any user-visible impact.
- Documentation was drafted in parallel with development, so the release day was
  not blocked waiting for docs.
- Load testing revealed no performance regressions under 2× expected peak traffic.
- The new onboarding tutorial was praised by three early-access users in the first
  24 hours.
- Feature flags enabled a clean progressive rollout without a maintenance window.
- Team morale was high throughout the sprint; async standups kept the cadence light.

---

## What Didn't Go Well

Record friction points, mistakes, miscommunications, and near-misses that should
be **prevented** or **mitigated** in future releases.

{didnt_go_well}

**Examples of things that typically don't go well:**

- Integration tests were skipped under time pressure, leading to a P1 incident in
  the first 48 hours.
- The staging environment diverged from production in three config keys, masking
  the bug until live traffic hit it.
- Scope was added in the final week without updating the acceptance criteria,
  causing confusion in QA sign-off.
- External dependency (upstream SDK) released a breaking change 72 hours before
  the release date, requiring an emergency patch.
- Release notes were written after the fact rather than during development,
  resulting in inaccuracies.
- The rollback procedure had not been rehearsed; executing it took 4× the
  estimated time under pressure.
- Monitoring thresholds were set too conservatively, generating alert fatigue that
  desensitised the on-call rotation.
- Database migration ran longer than estimated and extended the maintenance window.
- Two features were shipped incomplete because the "done" definition was ambiguous
  in the original spec.
- Sprint retrospective was skipped in the previous cycle, so this release
  inherited unresolved process debt.

---

## Surprises

Unexpected outcomes — positive or negative — that were not on the risk register
and warrant analysis.

{surprises}

**Guidance for this section:**

Record each surprise with three components:
1. **What happened** — a factual one-sentence description.
2. **Why it was unexpected** — the assumption that turned out to be wrong.
3. **Implication** — what to update in the next planning cycle as a result.

Positive surprises (better-than-expected outcomes) are equally valuable to
capture: they can reveal unstated advantages or happy accidents that should be
made deliberate.

---

## Metrics

Key quantitative indicators for this release cycle.

{metrics}

### Metric Definitions

- **tests_passed** — total test cases that returned green in the final CI run
  before merge to `main`.
- **tests_failed** — total test cases that returned red in the same run (should
  be 0 at ship time; non-zero indicates known accepted risk or flaky tests).
- **phases_landed_pct** — `(phases landed / phases planned) × 100`.  A value
  below 80 % is a red flag for scope estimation health.
- **p95_latency_ms** — 95th-percentile end-to-end API response latency measured
  in production during the first 24 hours post-release.  Target varies by
  endpoint; note the target next to the measured value.
- **error_rate_pct** — `(5xx responses / total responses) × 100` in the first
  24 hours.  Target ≤ 0.1 %.

---

## Action Items

Concrete follow-up tasks with clear ownership and deadline.  Every item raised in
**What Didn't Go Well** or **Surprises** should generate at least one action item.

| Owner | Action | Priority | Due |
|-------|--------|----------|-----|
{action_items}

### Priority Legend

| Level | Meaning |
|-------|---------|
| P0 | Security, outage, or data-loss risk — fix before next deploy |
| P1 | Blocker or confirmed bug — fix in current sprint |
| P2 | Improvement or refactor — schedule for next sprint |
| P3 | Polish, docs, or nice-to-have — backlog |

---

## Roadmap

### Next Quarter

Top three highest-impact items for the next 90-day cycle, ordered by priority.

{next_quarter}

Each item should have:
- A one-sentence **goal statement**.
- A clear **success criterion** (measurable).
- An **owner** or owning team.

### Backlog

Items that are valuable but deferred — either because of resource constraints,
external dependencies, or lower relative priority.

{backlog}

Backlog items should be reviewed at the start of every quarter planning session.
Items that have sat in the backlog for more than two cycles without movement
should either be promoted, converted to won't-do, or archived.

---

## Acknowledgments

{acknowledgments}

> _This retrospective was conducted in a blameless spirit.  The goal is to
> improve systems and processes, not to assign fault to individuals._

---

*Template maintained in `docs/templates/retrospective_template.md`.
Rendered by `service/isaac_assist_service/multimodal/post_release_retrospective.py`.*
