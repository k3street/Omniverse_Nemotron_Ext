# A4 — Fourth Canonical Draft Decision Document

**Date:** 2026-05-16
**Agent:** A4
**Backlog entry:** `research-maniskill-turn-faucet-001`

---

## §1 Candidate + Category + Pattern

| Field | Value |
|-------|-------|
| Backlog ID | `research-maniskill-turn-faucet-001` |
| Category | **research** (ManiSkill2 benchmark parity) |
| Pattern hint | **other** (articulated-joint in-place rotation) |
| Priority tier | 2 |
| Blockers | none |
| Motion controller | `direct_joint` (wrist joint targets) |

**Selection rationale:**

Tier-1 research candidates with empty blockers:
- `research-rtx-pick-place-sponge-001` — pick_place (already saturated in A1/A2)
- `research-maniskill-pick-cube-001` — pick_place (same)

All tier-1 research candidates either hit the pick_place or are blocked (physx_instability,
nucleus_only_asset, isaac_lab_install_required). Among tier-2, `research-maniskill-turn-faucet-001`
is the highest-value unblocked candidate that introduces a structural pattern absent from
the entire library: **articulated-fixture in-place rotation** (revolute joint on a scene
object, not a robot link). It is the only template that uses `create_articulated_joint` as a
standalone scene object and measures success by joint angle, not cube delivery.

---

## §2 Why It Diversifies the Library

| Dimension | A1 palletizer | A2 kit-prep | A3 barcode-divert | **A4 turn-faucet** |
|-----------|--------------|-------------|-------------------|--------------------|
| Category | yrkesroll | yrkesroll | yrkesroll | **research** |
| Source | industrial | ONET | industrial | **ManiSkill2 benchmark** |
| pattern_hint | pick_place | pick_place | sort | **other** |
| Transport | conveyor | none | conveyor | **none** |
| Fixture type | pallet (static) | kit tray (static) | 3 lane bins | **revolute articulated** |
| Success criterion | cube in bin | part in slot | SKU-routed | **joint angle ≥ 85°** |
| Novel tools | compute_stack_placement | create_kit_tray, set_semantic_label | barcode_reader_sensor | **create_articulated_joint, set_joint_limits, get_joint_positions on scene prim** |
| Robot task type | transport | transport | transport + classify | **contact torque on in-place object** |
| Motion mode | cuRobo multi-drop | cuRobo per-slot | conveyor+pick | **plan_trajectory + set_joint_targets** |

A4 is the first template in the library to:
1. Use `create_articulated_joint` as a scene-object fixture (not a robot joint)
2. Evaluate success by polling `get_joint_positions` on a non-robot prim
3. Apply `set_joint_limits` to a faucet DOF
4. Reference a named academic benchmark (ManiSkill2 TurnFaucet-v0)
5. Use `pattern_hint: other` with `isaac:fixture.articulated_revolute` structural tag

---

## §3 Form-Gate Result

Command:
```
python scripts/lint_canonical_templates.py workspace/templates/CP-NEW-turn-faucet.json
```

Output:
```
workspace/templates/CP-NEW-turn-faucet.json: OK
1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

Result: **1 OK / 0 ERROR / 0 WARN** — clean pass.

Template stats: ~170 lines JSON, code field ~100 lines Python, roles count: 2.

---

## §4 Backlog Updated

Entry `research-maniskill-turn-faucet-001` in `config/canonical_backlog.yaml` updated:

```yaml
status: drafted
template_file: workspace/templates/CP-NEW-turn-faucet.json
drafted_date: "2026-05-16"
```

No other backlog entries modified.
