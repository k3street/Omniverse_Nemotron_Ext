# A2 — Second Canonical Creation Decision

**Date:** 2026-05-16
**Agent run:** A2 (second canonical from F.0 backlog)
**Template:** `workspace/templates/CP-NEW-kit-prep-operator.json`

---

## §1 Candidate Picked and Why

**Backlog ID:** `yrkesroll-kit-prep-operator-001`
**Title:** Kit-prep operator — 5-part kitting from bins to tray (ONET SOC 51-2098)

Selection rationale:
- Tier-1, status: queued, zero blockers, single asset dependency (Franka — bundled).
- All other tier-1 queued candidates with no blockers already had existing templates:
  `inspector-reject-divert-001` → `CP-NEW-inspect-reject.json` exists;
  `sorter-color-3lane-001` → `CP-34.json` exists;
  `dr-curriculum-trainer-001` → `CP-NEW-dr-curriculum.json` exists;
  `controller-benchmark-shootout-001` → `CP-NEW-controller-shootout-cp.json` exists;
  `y-merge-singulation-001` → `CP-NEW-y-merge-singulation.json` exists;
  `multi-cam-triangulation-001` → `CP-NEW-multi-cam-triangulation.json` exists.
- `kit-prep-operator-001` had no matching template — clean slot.

---

## §2 Pattern and Roles Summary

**Pattern:** `pick_place` (multi-source bin variant — no conveyor)

**Scene summary:**
- Franka Panda picks one 0.05m part from each of 5 source bins arranged in a fan
  layout (3 front, 2 rear) on a 0.75m work table.
- Parts have distinct semantic labels: PCB, screw, housing, label, bracket.
- Kit tray at rear with `linear_5` slot layout receives one part per slot.
- No conveyor transport; parts are static at rest in bins before picks.
- cuRobo motion planner; CPU dynamics for determinism.

**Roles (4):**
| Role | Type | Count |
|---|---|---|
| `primary_robot` | franka_panda / ur5e / kinova_gen3 | 1 |
| `source_bins` | bin/tray | 5 |
| `kit_tray` | kit_tray/fixture | 1 |
| `workpieces` | cube/part | 1–5 |

---

## §3 Form-Gate Result

```
workspace/templates/CP-NEW-kit-prep-operator.json: OK
1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

Pass — 0 ERROR, 0 WARN, 0 INFO.

---

## §4 Differences from A1

| Dimension | A1 (palletizer-layer-stack) | A2 (kit-prep-operator) |
|---|---|---|
| Robot | UR10 (1.3m reach) | Franka Panda (0.85m reach) |
| Pattern | pick_place | pick_place (same enum, distinct variant) |
| Source | Single infeed conveyor | 5 static source bins (no conveyor) |
| Destination | Pallet, grid_2x3 layout | Kit tray, linear_5 layout |
| Parts | 6 uniform 0.10m boxes | 5 distinct-labelled 0.05m parts |
| Semantics | None | set_semantic_label (PCB/screw/housing/etc.) |
| Sensor | Proximity sensor on belt | None (static parts, implicit pick order) |
| Code lines | 114 (code field) | 114 (code field) |
| Roles | 4 | 4 |
| Industry | Palletizing / logistics | Assembly kitting / electronics |

The pattern_hint is the same (`pick_place`) because the backlog's other tier-1 candidates
with genuinely different patterns (sort, rl-train, other) were already covered by existing
templates. The structural_features `has_multi_source_bins: true` and `uses_conveyor_transport: false`
plus the `isaac:source.multi_bin` structural_tag distinguish this at retrieval time.

---

## §5 Backlog Status

`config/canonical_backlog.yaml` updated:
- `yrkesroll-kit-prep-operator-001`: `status: queued` → `status: drafted`
- `template_file: workspace/templates/CP-NEW-kit-prep-operator.json`
- `drafted_date: '2026-05-16'`
