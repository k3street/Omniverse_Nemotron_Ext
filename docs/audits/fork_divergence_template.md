# Fork Divergence Audit — {date}

**Base (working branch):** `{base_ref}`
**Head (k3street fork):** `{head_ref}`
**Total divergent commits:** {n_commits}

**Verdict counts:**
- `adopt`: {n_adopt}
- `defer`: {n_defer}
- `unknown`: {n_unknown}
- `merged`: {n_merged}
- `reject`: {n_reject}

**Advisory only.** No commits are auto-cherry-picked. The script
(`scripts/audit_fork_divergence.py`) classifies each commit by
subject-keyword + diff-file rules; the user reviews each row and
promotes `unknown` to one of the four other verdicts. Patterns that
recur land back in `_SUBJECT_RULES` in the script.

---

## Verdict reference

- **`adopt`** — the feature belongs in the spec; assign it a phase
  number (existing or new b-suffix). Open an IA-side PR that ports
  the relevant code under the same architectural constraints (no
  RL strategic content per Phase 17b mandate-guard).
- **`defer`** — adopt later, lower priority than the current epoch.
  Note the deferred item in `docs/audits/deferred_features.md` so
  future audits don't re-surface the same row.
- **`unknown`** — needs deeper read; surface to the user for
  decision. The initial output over-fires here by design; the
  user's classification turns into new `_SUBJECT_RULES` entries.
- **`merged`** — the feature already exists on the working branch
  (under a different commit hash, possibly refactored). No action
  required.
- **`reject`** — the feature is out of scope (RL strategic content,
  mandate violation, superseded by a spec phase). Document the
  reason inline in the next audit run so reviewers don't relitigate.

---

## Sections

For each verdict bucket with at least one row, the report emits a
table:

| sha | date | subject | hint |
|---|---|---|---|
| `abcd1234` | 2026-03-15 | example commit subject | evaluate against Phase X |

The `hint` column is best-effort — it suggests which existing IA
phase the commit might map to, based on subject keywords. Empty
hints mean the script had no rule for that subject and you should
read the actual diff before deciding.

---

## How to read this report

1. Start with the `adopt` section — these are concrete candidates
   for promotion into the IA spec.
2. Scan `unknown` — every row here is a judgment call. Promote each
   to one of the other four verdicts. Repeated patterns should be
   added to `_SUBJECT_RULES`.
3. `defer` is your "I'll get to this later" pile. Make sure each
   row has a reason recorded.
4. `merged` and `reject` are informational — useful sanity-check
   that the script's rules are recognising correctly.

---

## How the next run incorporates your feedback

1. For every `unknown` row you triage, add a one-line rule to
   `_SUBJECT_RULES` in `scripts/audit_fork_divergence.py` (e.g.
   `("foo_feature_xyz", "adopt"),`).
2. Re-run `python scripts/audit_fork_divergence.py`.
3. The new report has fewer `unknown` rows. Iterate quarterly per
   Phase 96.

---

*Template per Phase 0b in `specs/IA_FULL_SPEC_2026-05-10.md`. The
live output replaces the `{placeholder}` fields with real values
when the audit runs; this template is also kept as the documentation
of the schema.*
