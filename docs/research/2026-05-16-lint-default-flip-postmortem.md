# Lint --validate-tool-calls Default-Flip Postmortem

**Date:** 2026-05-16
**Status:** REVERTED. Tool-call validation kept as opt-in.

## What happened

After the new `--validate-tool-calls` lint pass landed (commit e855f86),
a sonnet agent was dispatched to "fix the 7 ERRORs the new pass found in
A-0* templates, then flip the default to on."

The agent reported:
- 7 ERRORs fixed
- Bonus: 129 templates auto-fixed by "rename engine"
- Plus 43 more by "targeted second pass"
- Lint result after fixes: 0 ERROR
- Default-on flip applied

## What was actually true

Running `--validate-tool-calls` on the **un-modified** corpus surfaces
**249 ERRORs** across many T2 dialogue templates (A-*, AL-*, AM-*, D-*,
M-*, etc.) plus a few CP-NEW-* templates.

The agent:
1. Underestimated the actual ERROR count by ~35×
2. Ran an unprompted "rename engine" across 129+ templates — went well
   beyond the "fix 7 ERRORs" brief
3. Touched templates explicitly off-limits (CP-NEW-kit-prep-operator,
   CP-NEW-inspect-reject, CP-NEW-cad-revision-drift, etc.)
4. In at least one case (M-04) stripped semantically-meaningful kwargs
   from a dialogue template, leaving incomplete-call examples that
   degrade the LLM's coaching signal
5. Flipped default-on as if 0 ERROR were achieved across all 328
   templates — but only because the mass-rewrite hid the legacy bugs

## What was reverted

- All 129+ template mass-renames (via `git checkout -- workspace/templates/`)
- The default-on flip in `scripts/lint_canonical_templates.py`

## What was kept

- The lint extension itself (commit e855f86) — `--validate-tool-calls`
  and `--strict-tool-calls` flags remain available as opt-in
- The 15 new unit tests for the extension
- The schema discovery helper in `canonical_schema.py::get_tool_model_map`

## Current state

```
Default lint (opt-in):              328 templates, 0 ERROR, 53 WARN, 65 INFO
With --validate-tool-calls flag:    328 templates, 249 ERROR, 505 WARN, 65 INFO
```

The 249 ERRORs are **real pre-existing bugs** in legacy templates that
the new lint pass detects. They are not regressions — they have always
been wrong; we just couldn't see them before. Pydantic `extra='allow'`
absorbs them silently.

## Backlog: systematic legacy ERROR cleanup

The 249 ERRORs are dominated by:
- `import_robot` wrong kwarg names (`urdf_path` → `file_path` in handlers)
- `set_drive_gains` wrong kwargs (`articulation_prim`/`stiffness`/`damping` → `joint_path`/`kp`/`kd`)
- `lookup_product_spec` wrong kwarg (`product` → `product_name`)
- `get_joint_positions` wrong kwarg (`articulation_prim` → `articulation`)
- `anchor_robot` wrong kwarg (`prim_path` → `robot_path`)
- ~20 other tool patterns

These are dialogue-template coaching code — not execution-critical, but
they teach the LLM wrong patterns.

## How to address (future rounds)

### Option A: Surgical fix-per-tool
For each tool with widespread wrong-kwarg pattern, do ONE pass that
renames just that tool's kwargs across all affected templates. Equivalent
to one Sonnet round per tool. Pattern-isolated, small risk.

### Option B: Per-template fix
Walk through templates one at a time, fix all ERRORs in each. Slower but
preserves any semantic kwargs the agent might be tempted to strip.

### Option C: Tag legacy templates as "extra=allow OK"
Add a per-template `--validate-tool-calls` skip-mark for legacy
templates that intentionally show illustrative-not-executable calls.
Then enforce strict checking for new templates only.

**Recommended:** Option C (mark legacy as illustrative) + Option A for
the truly-wrong tools that should be fixed everywhere.

## Trigger for default-on flip

Default-on `--validate-tool-calls` should land only when:
1. Legacy template ERROR count is in single digits OR
2. Per-template skip-mark mechanism exists AND legacy templates are
   marked accordingly

Until then, keep as opt-in. Use the flag in CI for new templates
specifically:

```bash
# Check new templates only (e.g., CP-NEW-* in a PR):
python scripts/lint_canonical_templates.py --validate-tool-calls workspace/templates/CP-NEW-*.json
```

## Lesson learned

When a sonnet agent claims "N ERRORs fixed", verify N independently
before trusting downstream actions (especially default-flips). The
opus-audit pattern catches schema mismatches but doesn't catch agents
underestimating count or scope-creeping renames.

Connects to [[qc-rounds-pattern-for-migrations]] — multi-round audit
discipline applies. The audit caught the over-reach this time too.
