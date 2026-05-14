# Industrial pick-place scenarios — Sonnet web research 2026-05-07

Output from 4 parallel Sonnet agents tasked with researching industrial scenarios suitable for new canonical templates (CP-06+).

---

## Set 1: General industrial pick-and-place (6 scenarios)

### CP-06: Inspect-and-Reject Divert
- 1 robot, 1 in-feed conveyor, 1 pass-through conveyor, 2 output bins (pass / reject)
- Cubes arrive on in-feed; vision check classifies; robot diverts rejects
- **Coordination**: sequential, single robot, no inter-robot timing
- **Stresses**: timing-constrained state machine with hard deadline (must pick within reachable window)
- Source: [OnRobot pick-place overview](https://onrobot.com/en/blog/what-is-a-pick-and-place-robot)

### CP-07: Kitting Station (Multi-Source to Tray)
- 1 robot, 3 source bins (cube types A/B/C), 1 kit tray
- Robot picks one of each type → seats in assigned slots → signals tray-complete
- **Coordination**: enforced pick order, no double-pick
- **Stresses**: per-slot occupancy tracking, multi-type identity
- Source: [Dematic kitting](https://www.dematic.com/en-us/insights/articles/conveyor-systems-for-order-picking-and-kitting/)

### CP-08: Two-Cell Kit-Tray Relay (NIST Pattern)
- 2 robots in 2 cells, 1 shared kit tray, feeder bins + assembly fixture
- Robot-1 fills tray → handoff → Robot-2 mates parts → returns
- **Coordination**: token-passing handoff, deadlock risk
- **Stresses**: cross-robot signaling, deadlock if both wait for the other
- Source: [NIST heterogeneous work cell](https://www.nist.gov/video/coordinated-assembly-heterogeneous-robotic-work-cell)

### CP-09: Dual-Robot Dynamic Fixture Hold
- 2 robots, no fixed fixture; R1 holds workpiece mid-air, R2 inserts mating part
- **Coordination**: simultaneous coordinated motion in shared workspace
- **Stresses**: hardest collision-avoidance case; treat moving robot as obstacle
- Source: [AMD dual-arm assembly](https://amdmachines.com/blog/dual-arm-robots-for-complex-assembly-tasks/)

### CP-10: Parcel Singulation and Multi-Chute Sort
- 1 robot, 1 heap input zone (overlapping cubes), 4 output chutes
- Robot picks one at a time, reads label, routes to correct chute
- **Coordination**: non-deterministic pick order; runtime routing decisions
- **Stresses**: unstructured input, branching state machine on object identity
- Source: [AmbiSort](https://www.ambirobotics.com/ambisort-a-series/), [Photoneo singulation](https://www.photoneo.com/how-singulation-and-sorting-of-parcels-can-benefit-from-ai-powered-robots/)

### CP-11: Mixed-SKU Palletizing Column
- 1 robot, conveyor with 2-3 cube sizes, 1 pallet zone
- Stable column: large bottom, small top, weight/size ordering
- **Coordination**: layer-aware stacking
- **Stresses**: vertical workspace, planning placement geometry
- Source: [Photoneo mixed palletizing](https://www.photoneo.com/mixed-palletizing-3d-vision/)

**Recommended order (simplest first)**: CP-06 → CP-07 → CP-10 → CP-11 → CP-08 → CP-09

---

## Set 2: Multi-robot coordination patterns (5 patterns, beyond CP-02 relay)

### Parallel Picking Duo (Zone-Partitioned Belt)
- 2 robots own spatial zones along shared belt
- **Failure modes**: zone-boundary race, throughput starvation, envelope overlap
- Source: [Multirobot pick-place on moving conveyor — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0736584511001396)

### Fixed-Point Robot-to-Robot Handoff
- R1 holds at transfer pose; R2 grasps with overlap window (dual-grip)
- Force/signal-triggered release
- **Failure modes**: off-center grasp drops, silent grip failure, both-open simultaneously
- Source: [Handover Control — Frontiers in Robotics and AI](https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2021.672995/full)

### Producer/Consumer with Bounded Buffer
- R1 fills staging rack (N slots), R2 empties; semaphore-gated
- Classic CS bounded-buffer in physical form — rack IS the semaphore
- **Failure modes**: underflow (idle-wait), overflow (collision on full slot), livelock
- Source: [Producer-consumer — Wikipedia](https://en.wikipedia.org/wiki/Producer–consumer_problem)

### Leader/Follower Rotary Station (3 robots)
- 3 robots around a rotary table (120° apart), each one sub-operation
- Leader controls table index timing; followers wait for ready signal
- Roles can swap for deadlock breaking
- **Failure modes**: consensus deadlock (all ready, none initiates), mid-op collision, role-swap cascade
- Source: [Leader-Follower deadlock avoidance — ResearchGate](https://www.researchgate.net/publication/224341939)

### Vision-Gated Bin Picking Duo (no fixed poses)
- Shared bin, randomly-posed objects, 3D camera publishes graspables
- Each robot claims via shared claim-table mutex
- **Failure modes**: claim mutex doesn't check workspace overlap, knocked-by-approach, vision latency
- Source: [Bin picking with dual-arm robots — ResearchGate](https://www.researchgate.net/publication/312410432)

---

## Set 3: Sortation and inspection (5 scenarios beyond CP-03 binary color)

### CP-04: Vision Quality Gate (3-path: pass / rework / reject)
- 1 camera, defect-severity-based routing, 3 destinations
- Beyond CP-03: third path, multi-threshold confidence, belt tracking for diverter timing
- Source: [Roboflow Machine Vision](https://blog.roboflow.com/machine-vision-in-manufacturing/)

### CP-05: Size + Color 4-Class Sorter
- (size, color) → 4 bins via 2-level decision tree
- Beyond CP-03: two independent sensor axes combine multiplicatively
- Source: [Roboflow Automated Sorting](https://blog.roboflow.com/automated-sorting-with-computer-vision/)

### CP-06: Parcel Postal Cross-Belt Sorter (20+ destinations)
- Barcode/RFID scan → WMS lookup → destination chute
- Beyond CP-03: scale, recirculation loop, lookup table, dimensional gating
- Source: [Cross Belt Sorter — Wikipedia](https://en.wikipedia.org/wiki/Cross_belt_sorter)

### CP-07: Recycling Multi-Sensor Stream Splitter (5 streams)
- NIR + metal detector + color → delta robot routes to 6 bins
- Beyond CP-03: 3 sensor modalities vote, NIR not native to Isaac (simulate as Semantics_material)
- Source: [RecyclingInside AI sorting](https://recyclinginside.com/recycling-technology/instrumentation-and-control/ai-driven-smart-recycling-the-future-of-waste-sorting/)

### CP-08: 3-Part Kitting Station (recipe-based)
- Robot picks (bolt M6, washer, bracket), fills tray to recipe (2,2,1)
- Beyond CP-03: stateful (accumulate-then-release), recipe success criterion
- Source: [Covariant Robotic Kitting](https://covariant.ai/robotic-kitting/)

---

## Set 4: Packaging / palletizing / assembly (5 patterns)

### CP-06: Brick-Layer Palletizer
- 18 cubes, 3x3x2 pallet, alternating 90° per layer (brick interlocking)
- Strictly bottom-up, layer-complete before next, gripper rotates between layers
- Source: [Palletizing Patterns — Robotiq](https://blog.robotiq.com/palletizing-pallet-pattern-charts)

### CP-07: Nested-Box Packer
- 4 cubes inside container, then lid placed flush on top
- Two-phase state machine (FILLING → SEALING) with guard condition (count=4)
- Source: [Robotic Case Packing — Motion Controls](https://motioncontrolsrobotics.com/resources/case-study/robotic-packaging-case-packing/)

### CP-08: Peg-in-Hole Insertion Array
- 4 cylindrical pegs, 5mm clearance tolerance, 3-phase per peg (APPROACH → ALIGN → INSERT)
- Force-threshold gated transition; search spiral on ALIGN
- Source: [Peg-in-Hole RL with Isaac Sim — arXiv 2504.04148](https://arxiv.org/html/2504.04148v1)

### CP-09: Graduated Tower (Sequence-Constrained Stacking)
- 5 cubes decreasing size, strict descending order
- Priority queue on size attribute as precondition for action
- Source: [Robotic Stacking of Diverse Shapes — OpenReview](https://openreview.net/forum?id=U0Q8CrtBJxJ)

### CP-10: Pinwheel Palletizer with Mid-Layer Rotation
- 8 cubes single layer, pinwheel arrangement, central gap
- Pairs placed as units, central gap maintained as forbidden target
- Source: [Palletizing Patterns and Robot Programming — AMD Machines](https://amdmachines.com/blog/palletizing-patterns-and-robot-programming/)
