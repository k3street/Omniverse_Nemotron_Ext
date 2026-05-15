# Ghost-Corpus Remap Decisions — 2026-05-15

**Agent:** Track A / Week 1  
**Task:** t04 — remap 30 TP-* ghost IDs in `role_template_index.py` to existing CP templates  
**Source file:** `service/isaac_assist_service/multimodal/role_template_index.py`  
**Prior findings:** Q3 §3, Q4 §5.1, Q7 task-graph t04  

---

## Summary

- Total TP-* IDs in registry before this patch: **30**  
- Remapped to existing CP/CP-NEW templates: **30** (0 deleted — all kept to satisfy test minimums)  
- Flagged [REVIEW] for human follow-up: **10** (8 original + TP-WLD-02 + TP-WLD-03 added by Round 3 patch C)  
- Tests passing after patch: **53/53**  

No registry entries were deleted. The `test_phase_21_role_template_index.py` enforces minimum role counts
(welder≥4, machine_tender≥3, dispenser≥2, etc.) which would fail on deletion. All TP-* `template_id` strings
were replaced with real CP IDs while the `role`, `sub_role`, `robot_class`, `gripper`, and `tags` metadata
were preserved verbatim — the role-binding intent survives.

---

## Decision Table

One row per original TP-* ID.

| Original ID | Role | Sub-role | Remap Target | Justification | Flag |
|-------------|------|----------|--------------|---------------|------|
| TP-WLD-01 | welder | spot_welder | **CP-76** | Dual-robot fixture hold — only CP with industrial precision fixture interaction; no weld-cell CP exists | [REVIEW] |
| TP-WLD-02 | welder | mig_welder | **CP-02** | Multi-station assembly line; closest multi-robot industrial workflow | [REVIEW] |
| TP-WLD-03 | welder | tig_welder | **CP-24** | Narrow-slot insertion, ±precision placement — closest path-precision analog | [REVIEW] |
| TP-WLD-04 | welder | robotic_arm_welder | **CP-69** | UR10 cuRobo; `robot_class=ur10e` exact match; collaborative scale | |
| TP-PCK-01 | picker | bin_picker | **CP-01** | Canonical Franka parallel-jaw bin-pick from conveyor; exact robot+gripper match | |
| TP-PCK-02 | picker | mixed_sku | **CP-54** | Franka surface_gripper (suction) pick; closest suction_cup_array analog | |
| TP-PCK-03 | picker | parcel_sorter | **CP-35** | Industrial sortation cell with `barcode_reader` + color routing into bins | |
| TP-PCK-04 | picker | pallet_picker | **CP-08** | 2×2 grid palletizer — closest depalletizing/layer-gripper analog | |
| TP-ASM-01 | assembler | pcb_assembler | **CP-24** | Narrow-slot insertion, ±precision; no SCARA/PCB CP exists | [REVIEW] |
| TP-ASM-02 | assembler | panel_assembler | **CP-02** | Multi-station assembly line, 2-robot handoff, sheet-stock analog | |
| TP-ASM-03 | assembler | sub_assembly | **CP-58** | Peg-in-hole insertion array; bolt/clip/snap-fit insertion analog | |
| TP-INS-01 | inspector | surface_inspector | **CP-18** | Inspect-and-reject station with semantic defect labeling | |
| TP-INS-02 | inspector | dimensional_inspector | **CP-18** | Same station; dimensional go/no-go shares inspect-reject flow | [REVIEW] |
| TP-INS-03 | inspector | vision_inspector | **CP-48** | TRUE runtime-vision inspect-reject with AI anomaly classifier | |
| TP-PAL-01 | palletizer | bag_palletizer | **CP-08** | 2×2 grid palletizer — single-layer grid stacking | |
| TP-PAL-02 | palletizer | box_palletizer | **CP-10** | 3×3 grid palletizer — layer pattern, mixed heights | |
| TP-PAL-03 | palletizer | mixed_palletizer | **CP-12** | Mixed-SKU palletizer — 3 different cube sizes | |
| TP-MCT-01 | machine_tender | cnc_loader | **CP-31** | Pick-from-pile; closest load/unload cycle analog; no CNC-tending CP | [REVIEW] |
| TP-MCT-02 | machine_tender | lathe_tender | **CP-69** | UR10 cuRobo; `robot_class=ur10e` exact match; single-pick-place cycle | |
| TP-MCT-03 | machine_tender | press_operator | **CP-76** | Dual-robot fixture hold; fixture-interaction closest to press loading | [REVIEW] |
| TP-PKR-01 | packer | bagger | **CP-57** | Parcel-singulation-from-heap; high-speed pick-and-place cycle | |
| TP-PKR-02 | packer | cartoner | **CP-77** | Nested-box packer with lid seal — cartoning/sealing analog | |
| TP-PKR-03 | packer | kit_packer | **CP-49** | Kitting station, 4-slot tray, BOM-driven pick | |
| TP-AMR-01 | AMR_driver | tugger | **CP-64** | Carter Nav2 mobile robot; intralogistics navigation | |
| TP-AMR-02 | AMR_driver | bin_mover | **CP-NEW-amr-pickup-handoff** | Nova Carter docks + Franka handoff; autonomous bin transport | |
| TP-AMR-03 | AMR_driver | mobile_manip | **CP-NEW-multi-amr-corridor** | 3 AMRs navigate corridor + handoff; closest mobile-manip scenario | [REVIEW] |
| TP-DSP-01 | dispenser | glue_dispenser | **CP-58** | Peg-in-hole precision placement; closest controlled end-effector path; no dispensing CP | [REVIEW] |
| TP-DSP-02 | dispenser | sealant_dispenser | **CP-69** | UR10 cuRobo; `robot_class=ur10e` exact match; automotive scale | [REVIEW] |
| TP-KIT-01 | kitter | parts_to_tray | **CP-49** | Kitting station, 4-slot kit tray, JIT-sequenced pick | |
| TP-KIT-02 | kitter | kit_assembly | **CP-50** | Vision-driven kitting, 2-color BOM routing into kit tray | |

---

## [REVIEW] Ambiguous Entries — Human Follow-up Required

These 10 entries have semantically weak remap targets. The current remap prevents silent-failure but a human
should author dedicated templates for Track F.
(Round 3 patch C added TP-WLD-02 and TP-WLD-03 — same logic as TP-DSP-02 applied consistently.)

| Entry | Remap Target | Weakness |
|-------|-------------|---------|
| TP-WLD-01 (spot_welder) | CP-76 | CP-76 is a fixture-hold task, not a welding cell; no arc/torch in scene |
| TP-WLD-02 (mig_welder) | CP-02 | CP-02 is multi-station conveyor pick-place; no MIG torch, no weld bead, no seam tracking |
| TP-WLD-03 (tig_welder) | CP-24 | CP-24 is narrow-slot insertion; no TIG torch, no shielding gas, no weld puddle |
| TP-ASM-01 (pcb_assembler) | CP-24 | CP-24 is narrow-slot insertion; no SCARA or PCB geometry |
| TP-INS-02 (dimensional_inspector) | CP-18 | CP-18 uses semantic labels, not laser profilometer metrology |
| TP-MCT-01 (cnc_loader) | CP-31 | CP-31 is heap-pick; no CNC machine or door-interlock |
| TP-MCT-03 (press_operator) | CP-76 | CP-76 is fixture hold; no stamping press or die-protection sensor |
| TP-AMR-03 (mobile_manip) | CP-NEW-multi-amr-corridor | CP-NEW is 3 AMRs navigating, not a mounted arm pick task |
| TP-DSP-01 (glue_dispenser) | CP-58 | CP-58 is peg insertion; no bead-dispensing end-effector path |
| TP-DSP-02 (sealant_dispenser) | CP-69 | CP-69 is a bin-pick; no sealant path or automotive panel |

---

## Track F Backlog — Roles with No Good Existing CP

These roles have no dedicated template and all current remaps are weak analogs:

| Role | Sub-roles Needing Templates |
|------|-----------------------------|
| welder | spot_welder, mig_welder, tig_welder, robotic_arm_welder |
| machine_tender | cnc_loader, lathe_tender, press_operator |
| dispenser | glue_dispenser, sealant_dispenser |

---

## Test Results

```
tests/test_phase_21_role_template_index.py  24 passed
tests/test_role_based_templates.py          21 passed
tests/test_role_index.py                     2 passed
tests/test_role_template_equivalence.py      6 passed
Total: 53 passed / 0 failed
```

All minimum-count constraints satisfied after remap (no deletions needed).
