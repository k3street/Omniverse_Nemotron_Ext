# Multi-Session Coordination — Multimodal Foundation × Controller-Logic 100%

Authored 2026-05-09. Active artifact while two parallel work sessions run
against the same codebase. **Update this doc when the work-split shifts.**

## Sessions

| Session | Scope |
|---|---|
| **Multimodal foundation** | LayoutSpec IR, ratify, verifier-registry refactor, role-based CP-N refactor, multimodal handlers, canvas SPA, chat_view.py UI additions (`👁 Modes` launcher, scrolling quick-prompts, canvas-mirror panel). Spec: `docs/specs/2026-05-08-multimodal-foundation-spec.md`. |
| **Controller-logic 100%** | Drive `verify_pickplace_pipeline` + `simulate_traversal_check` to 100% function-gate ✓ across CP-N suite. New CP-templates (CP-06+). ROS2-bridge handlers. Manufacturing-metrics handlers. Library/dependency shrink. |

The sessions converged on this split via three rounds of negotiation;
this doc captures the result.

---

## Ownership table — who edits what

### Multimodal foundation owns fully

- `service/isaac_assist_service/multimodal/` (new module — IR, validators,
  ratify, persistence, retrieval-extension, migrations)
- `web/floor-plan-ui/` (new SPA — Vite, Konva)
- `service/isaac_assist_service/chat/tools/multimodal_handlers.py` (new file
  — all handlers for `read_layout_spec`, `update_layout_spec`,
  `commit_layout_spec`, `apply_layout_spec_to_scene`, `query_layout_metric`,
  `rebind_role`, modality producers)
- `service/isaac_assist_service/chat/canonical_instantiator.py` —
  ratify-wrapper before `execute_template_canonical`. Touches the entry path,
  does **not** touch verify steps the controller-logic session is iterating on.
  **In Block 1B**: also extends `substitute_template_params` for role-based
  templates.
- `service/isaac_assist_service/chat/tools/template_retriever.py` —
  structural-filter-first retrieval extension.
- `exts/isaac_5.1/omni.isaac.assist/ui/chat_view.py` and
  `exts/isaac_6.0/omni.isaac.assist/ui/chat_view.py` — `👁 Modes` launcher,
  popover, scrolling quick-prompts row, canvas-mirror panel registration,
  SSE listeners for canvas/multimodal events.
- `workspace/templates/CP-01.json` … `CP-05.json` — **Block 1B only**;
  role-based refactor with function-gate revalidation.
- New routes module `service/isaac_assist_service/multimodal/routes.py` —
  `/api/v1/canvas/*` endpoints.

### Controller-logic 100% owns fully

- Pick-place controller logic in `tool_executor.py` (its existing section).
- Variants of `verify_pickplace_pipeline` and `simulate_traversal_check`
  iteration during the 100% drive.
- New CP-templates `workspace/templates/CP-06.json` and onward.
- ROS2-bridge handlers in `tool_executor.py` (its existing section).
- Manufacturing-metrics handlers in `tool_executor.py` (its existing section).
- `requirements.txt` / dependency-shrink.

### Shared files — sectional ownership

These files have both sessions adding code. Use explicit comment-based
sectional ownership.

#### `service/isaac_assist_service/chat/tools/tool_executor.py`

```python
# === MULTIMODAL HANDLERS (multimodal-foundation session) ===
from .multimodal_handlers import register_multimodal_handlers
register_multimodal_handlers(DATA_HANDLERS)
# === END MULTIMODAL HANDLERS ===

# === PICK-PLACE CONTROLLERS (controller-logic session) ===
# (existing handlers stay here)
# === END PICK-PLACE CONTROLLERS ===

# === ROS2 BRIDGE (controller-logic session) ===
# === END ROS2 BRIDGE ===

# === MANUFACTURING METRICS (controller-logic session) ===
# === END MANUFACTURING METRICS ===
```

The multimodal section is one import + one register-call. All actual handler
implementations live in `multimodal_handlers.py` (multimodal session owns).

#### `service/isaac_assist_service/chat/tools/tool_schemas.py`

Mirror the same sectional pattern — one section per session, explicit
comment markers, additions go inside owning section only.

#### `service/isaac_assist_service/chat/orchestrator.py`

Low-conflict surface. Multimodal session adds a single hook for
text-prompt → LayoutSpec extraction at a specific line (~795 in current
HEAD). Controller-logic session should not touch `handle_message`-flow
without coordinating.

---

## Sync points

### Sync point A — Block 1B (verifier-registry refactor + CP-N role-based refactor)

**Held until controller-logic session signals 100% function-gate.**

- Multimodal session does Block 1A first (parallel-safe; no holds)
- Controller-logic session iterates on `verify_pickplace_pipeline` +
  `simulate_traversal_check` to reach 100% across CP-N suite
- When 100% is reached, controller-logic session signals
- Multimodal session then lands Block 1B: registry refactor + CP-N role-based
  refactor against now-stable pipeline

Rationale: Block 1B is structural refactor with **zero immediate behavior
change**. Value materializes for future feature-dispatched checks. Forcing
controller-logic session to rebase 5-10 commits through Block 1B's
restructure would produce massive rebase pain for zero immediate value.
Sequential ordering wins.

### Sync point B — Function-gate revalidation post role-based refactor

**Discipline non-negotiable.**

Before role-based refactor of CP-01..CP-05:
1. Capture baseline snapshot via `function_gate_suite.py`: per-template
   cube_final positions, delivered counts, exact verify diagnostics. Persist
   to `workspace/baselines/pre-role-refactor/CP-N.snapshot.json`.

After role-based refactor:
2. Run the same suite against same seed.
3. **Any previous ✓ that becomes ✗ → template rolled back.** Refactor
   approach reworked. No exceptions.

The role-based refactor replaces literal paths (`/World/Franka`) with
placeholder substitution (`{{primary_robot.path}}`). Substitution should
resolve identically — but this is testable, not assumable. The baseline-vs-post
test enforces the assumption.

### Sync point C — New CP-templates format

Two valid orderings:

- **Sequential (preferred)**: multimodal session lands Block 1B (role-based
  refactor of CP-01..05) FIRST → controller-logic session writes CP-06+ in
  role-based format directly.
- **Parallel**: controller-logic session writes CP-06+ in current format →
  multimodal session migrates them to role-based at Block 1B time as part
  of the same refactor pass.

Sequential is cleaner and reduces re-work. If timing aligns, prefer it.

---

## Branch and merge protocol

- **Separate feature branches** off the shared base (current HEAD on
  `feat/live-progress-ui` or whichever working branch).
- **No worktrees** — branches with rebases are sufficient. Worktrees would
  add filesystem-isolation overhead without commensurate benefit; only one
  service runs on port 8000 at a time anyway.
- **Push to `anton` remote**, not `origin` or `fork` (matches project's
  established git flow per memory `feedback_isaac_assist_push_target`).
- **Rebase frequently** against the other session's branch when shared files
  are touched. Daily rebases minimize conflict scope.
- **Merge to common branch** when both sessions reach a stable point.

## Communication protocol

- **Commit messages flag shared-file edits** explicitly. Examples:
  - `tool_executor.py: register MULTIMODAL handlers (multimodal session)`
  - `tool_executor.py: add manufacturing-metrics handler X (controller-logic session)`
- **Daily sync**: a short status note on what's been touched in shared files,
  surfaced via human relay (until both sessions can read same workspace).
- **Sync-point signaling**: when controller-logic session reaches 100%, they
  emit explicit signal (e.g., commit message
  `function-gate: 100% reached — multimodal can land Block 1B`). Multimodal
  session waits for this commit to appear before starting Block 1B work.

---

## Conflict-resolution protocol

If a merge conflict surfaces in a shared file:

1. **Section-marker conflicts** (both sessions edited within the same comment
   section): unlikely if ownership table is followed; if it happens, the
   owning session's version wins, the offending session moves their changes
   to their own section.
2. **Cross-section conflicts** (one session edited the other's section by
   mistake): revert the offending edit; coordinate via human relay for the
   correct path.
3. **Block 1B function-gate regression** (post-role-based refactor, a CP
   template fails function-gate): roll back the template edit; multimodal
   session reworks the substitution approach. Do not let regression land.
4. **Surprise dependency change** (controller-logic library shrink removes
   a package multimodal needs): coordinate via human relay; pin the dep
   in multimodal's module if necessary.

---

## Status as of authorship

- **Multimodal session**: actively working on Block 1A (foundation +
  multimodal_handlers.py + UI specifications + ratify-wrapper +
  template_retriever extension).
- **Controller-logic session**: actively iterating on
  `verify_pickplace_pipeline` + `simulate_traversal_check` for 100%
  function-gate. Latest commits include `79b587a` (cube_paths multi-cube)
  and recent additions of `per_cube_status` + `delivered_cubes` fields.
  Pending work: Cortex multi-cube fix, Stack precision fix, Belt-pause
  root cause v2.

When controller-logic signals 100%, multimodal session begins Block 1B.
Until then, both sessions run parallel without rebase pain.

---

## Update log

This doc is meant to evolve as the work progresses. Append entries below as
ownership shifts, sync points fire, or unexpected coordination needs arise.

| Date | Author | Change |
|---|---|---|
| 2026-05-09 | multimodal session | Initial authoring after three-round negotiation |
