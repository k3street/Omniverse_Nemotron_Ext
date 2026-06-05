# Task FP-AD-03 [HARD] — Role binding conflict resolution

**Modality:** drag-drop canvas + chat

**Goal:** When ratify cannot deterministically bind a role (e.g. two
Frankas, template expects exactly one `primary_robot`, no
disambiguator fires), the ratifier returns `status=needs_choice`. UI
surfaces the ambiguity. User clicks one Franka, optionally invokes
`rebind_role`, and the build proceeds.

**Starting state:**
- LayoutSpec with two `franka_panda` objects at (-1, 0) and (+1, 0)
- Template CP-01 declares `primary_robot.expected_count=1` with
  disambiguator `smaller_x_first` — DELETED for this test, simulating
  template author forgot to add it

**Success criterion:**
- `ratify(template, spec)` returns `status="needs_choice"`
- `ambiguous_roles` lists `primary_robot` with both Franka object_ids
  as candidates
- UI surfaces a banner: "Two Frankas could be the primary picker —
  pick one."
- User clicks one Franka → `rebind_role` tool fires →
  `LayoutSpec.bindings["primary_robot"] = {object_id, source="user_correction"}`
- Subsequent ratify returns `status="ok"`
- `build_started` fires; build completes successfully
- Telemetry: `ratify_completed` (needs_choice), `rebind_role`,
  `ratify_completed` (ok), `build_completed`

**Failure modes:**
- User clicks neither Franka and dismisses banner → ratify stuck in
  `needs_choice` until UI surfaces it again; backend MUST NOT
  auto-pick (per spec §5.2 step 4: "ambiguity is resolved by
  something other than deterministic code only at this UI surface")
- User clicks a non-Franka object → `rebind_role` returns
  `wrong_class` error; UI surfaces the constraint mismatch
- User clicks both Frankas in succession (race) → second click
  overrides first; only one binding in final spec

**Adversarial cases:**
- Add a third Franka while ambiguous banner is shown → re-ratify
  surfaces three candidates
- Delete the bound Franka after user picked it → CAS conflict on
  next save; UI shows three-way merge

**Telemetry assertion:**
- Exactly one `rebind_role` event per resolved ambiguity
- `user_correction` event with `surface=ambiguity_banner`
- Time-to-resolution (interval between needs_choice and second
  ratify_completed) recorded in payload

**Test harness:** `tests/test_fp_ad_03_role_binding_conflict.py`. Backend
only — UI surfacing tested via Playwright when Block 4 wiring lands.
