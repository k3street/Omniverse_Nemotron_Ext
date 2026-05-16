# A3 Third Canonical — Barcode-Scanner SKU Divert (2026-05-16)

## §1 Candidate + Chosen Pattern

Candidate: `yrkesroll-barcode-scanner-divert-001` (tier-2, category: yrkesroll)
Pattern: **sort** (pattern_hint ≠ pick_place per task constraint)

Selection rationale: The backlog contains three tier-1 sort candidates, but all overlap
with existing templates:
- `yrkesroll-inspector-reject-divert-001` → covered by `CP-NEW-inspect-reject.json`
- `yrkesroll-sorter-color-3lane-001` → covered by `CP-34.json` and `CP-NEW-cross-belt-sorter.json`
- No other tier-1 sort with empty blockers

`yrkesroll-barcode-scanner-divert-001` (tier-2) is the highest-value non-overlapping sort
candidate: it introduces `barcode_reader_sensor` as routing oracle — a sensor modality
not yet represented in any existing template — and the SKU fall-through catch-all
pattern at industrial scale.

## §2 Template Summary

File: `workspace/templates/CP-NEW-barcode-scanner-divert.json`
Line count: ~230 lines (JSON); code field: ~85 lines of Python
Roles: 7 (primary_robot, input_conveyor, barcode_sensor, pick_sensor,
         sku_a_destination, sku_b_destination, catchall_destination) + workpieces multi-role

Distinguishing structural_tags:
- `isaac:sensor.barcode_reader` — first template to use barcode_reader_sensor
- `isaac:routing.barcode_sku_divert` — SKU string from sensor, not colour class
- `isaac:routing.fallthrough_catchall` — unrecognised SKU → catch-all lane (LaneC)
- `isaac:transport.conveyor` — belt transport + pick sensor trigger
- `isaac:robot.fixed_base.arm` — Franka Panda within 0.85m reach
- `isaac:topology.single_station` — one robot, three destinations

Scene: 6 items (2× SKU_A orange, 2× SKU_B cyan, 2× SKU_C yellow/unrouted),
3 lane bins (LaneA/B/C), barcode scanner at x=0.10 y=0.40 z=0.92,
pick sensor at x=0.40. Belt velocity 0.15 m/s.

## §3 Form-Gate Result

Command: `python scripts/lint_canonical_templates.py workspace/templates/CP-NEW-barcode-scanner-divert.json`

```
workspace/templates/CP-NEW-barcode-scanner-divert.json: OK
1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

Result: **1 OK / 0 ERROR** — passes form-gate.

## §4 Structural Differences from A1 / A2

| Dimension          | A1 (palletizer-layer-stack) | A2 (kit-prep-operator)   | A3 (barcode-scanner-divert) |
|--------------------|-----------------------------|--------------------------|-----------------------------|
| pattern_hint       | pick_place                  | pick_place               | **sort**                    |
| Robot              | UR10                        | Franka Panda             | Franka Panda                |
| Transport          | conveyor (0.15 m/s)         | none (static bins)       | conveyor (0.15 m/s)         |
| Routing criterion  | grid position (2×3 layout)  | per-source drop_target   | **barcode SKU string**      |
| Routing oracle     | compute_stack_placement     | label → slot map         | **barcode_reader_sensor**   |
| Destinations       | 1 pallet (6 grid slots)     | 1 kit tray (5 slots)     | 3 lane bins                 |
| Fall-through       | no                          | no                       | **yes** (unrecognised SKU)  |
| Motion controller  | curobo (UR10)               | curobo (Franka)          | untested (Franka)           |
| New sensor API     | none                        | none                     | **barcode_reader_sensor**   |
| ONET anchor        | SOC 51-2091 Palletizers     | SOC 51-2098 Kit-prep     | postal/distribution yrkesroll |

Key differentiator: A3 is the only template in the library to use `barcode_reader_sensor`
as the routing oracle. The routing decision is based on a SKU string read from the item
(not a colour, not a semantic class pre-tagged before simulation), and the fall-through
catch-all lane is explicitly exercised by the SKU_C items. This creates a distinct
retrieval vector from both A1/A2 and the existing CP-18/CP-34 sort templates.
