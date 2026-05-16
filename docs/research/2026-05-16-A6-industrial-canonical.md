# A6 Industrial Canonical — Decision Doc
Date: 2026-05-16

## Candidate chosen

**`industrial-assembly-line-4robot-handoff-001`**
Template: `workspace/templates/CP-NEW-assembly-line-4robot-handoff.json`

## Candidate evaluation (industrial category, tier-1/2, no blockers)

| id | tier | complexity | blockers | pattern_hint | topology |
|----|------|-----------|----------|-------------|---------|
| industrial-bin-picking-random-pose-001 | 1 | complex | none | pick_place | single robot, overlap_sphere grasp |
| **industrial-assembly-line-4robot-handoff-001** | **2** | **complex** | **none** | **pick_place** | **4 robots serial handoff + mutex** |
| industrial-kitting-station-6sku-001 | 2 | complex | none | pick_place | single UR10 + rotary carousel |
| industrial-conveyor-tracking-moving-pick-001 | 2 | complex | none | pick_place | single robot, belt velocity feedforward |
| industrial-gravity-dispenser-feeder-001 | 2 | medium | none | pick_place | single robot + dispenser |

## Diversification rationale

A1–A5 topology inventory:
- A1 (kit-prep-operator): 1 Franka, multi-source bins → tray
- A2 (barcode-scanner-divert): 0 robots, conveyor + sensor routing
- A3 (palletizer-layer-stack): 1 UR10, infeed belt → grid pallet
- A4 (turn-faucet): 1 Franka, articulated joint rotation
- A5 (ros2-bridge-franka): 1 Franka, ROS2 bridge plumbing

All A1–A5 are single-robot or no-robot scenes. The 4-robot serial handoff
is the only topology in the industrial backlog that:
1. Uses 4 simultaneous active robots (n_robot_stations=4)
2. Introduces inter-robot coordination primitives: `setup_robot_claim_mutex`
   and `setup_robot_handoff_signal`
3. Represents a production-cell architecture (serial assembly line) rather
   than a single work cell

The rotary-carousel (kitting-station-6sku) and moving-conveyor-pick are
also distinct but remain single-robot. The 4-robot handoff was selected as
the highest-diversity pick for this round.

## Template summary

- **Robots**: FrankaA/B/C/D at x=[-1.5, -0.5, +0.5, +1.5], all on shared 4m table
- **Handoff chain**: InfeedStation → TrayAB → TrayBC → TrayCD → FinalBin
- **Claim mutexes**: MutexAB, MutexBC, MutexCD (serialise tray access)
- **Handoff signals**: SignalAB→BC→CD (downstream activates on upstream place_complete)
- **Code LOC**: 113 lines in `code` field; 105 lines in `code_template`
- **Roles**: 7 roles declared (4 robot stations + handoff_trays + final_destination + workpieces)
- **pattern_hint**: pick_place
- **motion_controllers**: untested=["curobo"]

## Form-gate result

```
workspace/templates/CP-NEW-assembly-line-4robot-handoff.json: OK
1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

Pass. No errors, no warnings.

## Known risks

- Franka reach to furthest tray corner is ~0.74m (85% of 0.85m max reach) — tight
- Serial pipeline latency: 4 stages × ~75s budget each at duration_s=300
- Claim mutex deadlock possible if handler does not implement non-blocking try_acquire
- Narrow trays (0.20×0.20m) require <0.05m placement precision from cuRobo
