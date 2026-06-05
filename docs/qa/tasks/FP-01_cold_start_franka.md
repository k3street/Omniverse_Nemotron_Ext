# Task FP-01 [EASY] — Cold-start single-Franka pick-place via canvas modality

**Modality:** drag-drop canvas (browser SPA per multimodal foundation spec §9)

**Goal:** With no prior session state, the user opens the canvas, drags one
Franka + one conveyor + one bin + four cubes onto the floor plan, hits "send
to scene", and the scene appears in Isaac Sim Kit ready to run.

**Starting state:**
- Fresh canvas session (no LayoutSpec history)
- Kit running and reachable via RPC
- No templates pre-loaded

**Success criterion:**
- LayoutSpec validates (objects placed, no overlaps)
- `ratify` returns `status=ok`
- Retrieval surfaces CP-01 as top match (T1 — sim ≥ 0.85 + margin ≥ 0.20)
- `execute_template_canonical(CP-01)` succeeds end-to-end
- 4/4 cubes visible at their authored positions after settle
- Telemetry: emit `modality_invoked` (drag_drop), `intent_extracted`,
  `retrieval_completed` (tier T1), `ratify_completed` (ok), `build_started`,
  per-tool `build_progress` events, `build_completed` (success)

**Expected role bindings (deterministic):**
1. `primary_robot` → Franka (only candidate)
2. `input_conveyor` → ConveyorBelt (only candidate)
3. `primary_destination` → Bin (only candidate)
4. `workpieces` → 4 cubes (unordered, all 4 bound)

No `needs_choice` should fire — cardinality-trivial throughout.

**Friction points:**
- User drops cubes OUTSIDE the conveyor bbox → ratify should not warn
  (object-on-belt invariant is a build-time check, not ratify)
- User drops cubes INSIDE robot's reach → controller pauses belt immediately;
  belt visibility check at verify time, not at ratify
- Adversarial: user drags a second Franka, then deletes it → ratify must NOT
  see the deleted one

**Telemetry assertions (Block 5):**
- Exactly one `modality_invoked` event per send-to-scene click
- `t1_fire_rate` for this session = 1.0 (CP-01 is a clean T1)
- `proposal_acceptance` = accept (user confirms scene)

**Test harness:** `tests/test_fp_01_cold_start.py` (Playwright + backend
fixtures). Until canvas SPA wired through Block 4 (IA Phase 19), FP-01 is a
backend-only test: synthesize the LayoutSpec from a fixture instead of
drag-drop, then assert the rest.
