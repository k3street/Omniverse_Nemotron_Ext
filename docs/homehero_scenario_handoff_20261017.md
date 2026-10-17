# Nemotron scenario development → HomeHero workflow integration

Requested on 2026-10-17. The machine-readable queue is
[`aida_nemotron_scenario_queue_v1.json`](../workspace/knowledge/homehero_scenario_queue_v1.json).
An identical request is delivered to Nemotron's
`workspace/knowledge/homehero_scenario_queue_v1.json`, with three entries in its
existing `task_backlog.json`. Their initial status is **proposed**, not running.

## Start here in the Nemotron thread

Develop the following scenarios in priority order, using the current Amber/R5,
Ability Hand controller and existing shared HomeHero geometry. Package the
completed portions immediately; preserve partial outcomes and failed attempts.
Finish an evidence handoff before spending another cycle on a harder extension.

1. **Microwave:** package the existing button press, spring pop and hand-driven
   opening. Then develop insertion and closure as separately scored stages.
2. **Tool transfer:** acquire scissors or a graspable screwdriver by its intended
   region, orient it, and place it in a tray. Test a second compatible tool with
   the same controller. One-hand table-edge pickup must not be replaced by a
   hook pickup or a two-hand manipulation.
3. **Drawer storage:** place and retrieve different fitting objects in an
   already-open drawer; add closing and opening afterward. Use the current
   cabinet before creating a new room. Fully closed lip hooking is not an entry
   requirement for storage experiments.

Do not make the first handoffs depend on powered screw driving, digit-only
scissor cycling or difficult in-hand manipulation. These remain separate
capability studies. A changed grasp candidate is allowed; changing hand gains,
geometry, mass, force limits or the success rule creates a new comparison
revision and must not be hidden as object-specific tuning.

## Division of work

| Nemotron | HomeHero |
| --- | --- |
| Asset/mechanism validation and scenario discovery | Independent reproduction and shared adapters |
| Geometric grasp/contact proposals and bounded corrections | Measured task judging, recovery and lesson records |
| Native execution, critic review and complete evidence | Nav2, reachable base placement and arm staging |
| Declared variations with a common controller | Workflow composition and reserved-case evaluation |

Nemotron should deliver the body/part identities, permitted contact regions,
initial support, required hand count, action order, passive mechanism and
completion observations. HomeHero maps these to the
[shared interaction curriculum](/home/kimate/Documents/Github/HomeHero/docs/aida_interaction_curriculum.md). JSON task
descriptions are requests and requirements, not unrestricted executable code.

## Existing evidence and gaps

The 2026-10-17 source survey found:

- Microwave source measurements report an 8.76 mm button press, 19.8° passive
  pop and 66.2° final opening. The hand-driven portion is 44.2°. This supports
  an opening-prefix candidate, not an 80° opening, insertion or complete task.
  Its backlog already identifies cavity collision geometry, panel contact and
  approximately 150 seconds of planned push motion as issues.
- Refrigerator source measurements report 34.3° peak opening against a modeled
  35 N gasket. Its 60° objective still needs work. Keep it as an additional
  mechanism reference; do not duplicate the microwave handoff with another
  long fixed-base reach investigation.
- The rail-scissors source says pass, while the horizontal-loop digit-cycling
  experiment says fail. These are different tasks. Preserve the critic's
  hand-count and intended-use findings rather than transferring the pass label.
- The drill/screw source reports a failed tool verdict. Its grip and trigger
  issues are already in Nemotron's backlog and are not prerequisites for a
  simple tool transfer.

The survey records the observed file hashes and source verdicts. Current source
bytes are not automatically the code that produced an older recording; missing
execution-time provenance must remain explicit. No source result has been
promoted to a new HomeHero success by this handoff.

## Deliverable and acceptance

Use an immutable folder such as
`workspace/handoffs/homehero/<scenario-id>/<revision>/` in Nemotron. Include the
manifest, frozen case definitions, full attempt ledger, source snapshots,
resolved asset dependencies, commanded and measured states, target-specific
contact/force and mechanism measurements, source results, critic findings and
browser-playable videos. The JSON queue lists the exact required contents.

Declare four development cases: nominal, changed pose/approach, changed initial
mechanism state/orientation, and another compatible asset. Reserve a new pose
combination and another asset for evaluation before tuning. If an asset or
adapter is unavailable, keep that case in the ledger with the reason; do not
substitute a renamed duplicate or claim unseen-object generalization.

Freeze numerical goals and case conditions before execution. Record actual
settled resets, not just random seeds. Keep every failure, planning exclusion,
timeout and correction revision. A failed attempt with complete evidence is a
valid handoff and a useful lesson; it cannot become a successful training label.

HomeHero acceptance progresses through evidence intake, independent native
reproduction, variation evaluation and workflow integration. It retains source
verdicts separately from HomeHero's judgment. A copied video, successful process
exit, visual critic opinion or one successful grasp does not establish the full
workflow or generalization.

Report planning wall time, execution simulation time, execution wall time,
simulation real-time factor, recoveries and retention separately. This allows
HomeHero to distinguish slow motion plans from simulator lag. New mobile
staging comparisons must use matched starts and the same contact controller.

## Coordination

Use the shared `scripts/isaac_slot.sh` lock for bounded native jobs. Do not stop
HomeHero training or another thread's process. Deferred rendering can reuse
measured states; label it as replay. This request adds no hardware activity or
cloud-spending authorization.

Existing Nemotron backlog entries are preserved. The three new stable IDs are
`hh_port_microwave_v1`, `hh_port_tool_transfer_v1` and
`hh_port_drawer_storage_v1`; they link to the source gaps rather than replacing
ongoing work. File delivery queues the request but does not wake another chat
thread or demonstrate that an agent has started it.
