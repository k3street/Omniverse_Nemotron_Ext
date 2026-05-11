# Plan: alla controllers → 4/4 + motion-kvalitet

**Mål**: alla target_source-moden ska leverera 4/4 kuber i bin på
conveyor_pick_place-scenariot, OCH vi ska rendera video-exempel så
användaren kan bedöma motion-smoothness subjektivt.

**Ultrathink-premiss**: 4/4-leverans är en binär metrik. Motion-kvalitet
(jerk, wrist-snap, joint-limit-drift, close-call collisions) är ortogonal.
Planen måste nå båda utan att låta den ena skymma den andra.

## 0. Utgångsläge (verifierat 2026-04-21)

| Controller | Nuläge | Historik | Huvudblockare |
|---|---|---|---|
| native | 0-1/4 det. | Möjligen 3/4 förr (oklart om användaren blandar ihop m. spline — bekräfta via git-hist) | PickPlaceController + RmpFlow reaktiv; grip-timing |
| spline | **3/4 det.** | Stabil vinnare | Sista kuben (varierar) landar ~20cm off-bin |
| sensor_gated | untested | Befintlig kod | Kräver teach_robot_pose eller coord-IK setup |
| fixed_poses | untested | Befintlig kod | Kräver pose_sequence byggd för scenariot |
| cube_tracking | untested | Legacy | Omniscient; borde "bara funka" men otestad |
| ros2_cmd | untested (stub) | OmniGraph ofullständig (sub-node inte kopplad till arm) | Både intern graph + extern ROS2-node saknas |
| diffik | 0/4 | 4 cykler, 0 fel; grip-timing | Per-tick Jacobian → PD konvergerar långsamt vid dwell |
| osc | 0/4 | Experimentell; simplified (ingen M/g) | Jacobian-transpose impedance inherent lösare |
| curobo | 0/4 | Planerar + kör perfekt (3mm målträff); saknar scene-obstacles | Arm sveper genom kuber → knockar dem av bandet |
| auto | resolver ✓ | Väljer spline just nu | — |

**Native-historik bekräftad 2026-04-21 av användaren**: native nådde 3/4
(och tillfälligt 4/4) FÖRE spline byggdes, som en IK+RmpFlow-hybrid —
Lula IK beräknar cspace-target varje tick, pushas in i RmpFlow's motion
policy via `set_cspace_target(q)`. Den IK-guide-koden FINNS KVAR i
native-generatorn (`_guide_via_ik` funktionen, kallas per tick i
`_on_step` executing branch, pumpad via `_last_ik_cspace` warm-start-cache).

**Så varför 0-1/4 nu?** Något har regresserat. Troliga misstänkta:
1. `_PP_OBSERVABILITY_SNIPPET`/`_PP_SCENE_RESET_MGR_SNIPPET`-refactor — textextraktion,
   men om ordning av inline-kod ändrades kan det påverka stängningens tidpunkt
2. Scene Reset Manager — hooks från spline/diffik/osc/curobo persisterar i
   `builtins._scene_reset_manager` mellan benchmark-runs. Native's install
   unregistrerar INTE stale hooks (medan spline/diffik/osc/curobo gör det).
   På Stop+Play fires stale hooks → manager i trasigt tillstånd → native's
   reset ofullständig nästa cykel.
3. Belt-pause-logik — native resumear belt mellan cykler (kvarstår från
   gammal cube_tracking-arv); newer controllers håller belt paused. Kuber
   driver förbi sensorn mellan cykler.

**FAS A konkret**: diffa nuvarande native-generator mot den version som
låg i tool_executor.py före FAS 1-refactorn. Troligen `git log --all -p
service/isaac_assist_service/chat/tools/tool_executor.py | grep -B3 -A20
"def _gen_pick_place_native"` + datum-filter runt refactorn (2026-04-21).

## 1. Realistiska förväntningar — per controller till 4/4

Inte alla controllers KAN nå 4/4 för pick-place. Viktigt att vara ärlig
innan vi investerar timmar på lösningar som inte existerar:

### A. Sannolik 4/4 (hög ROI):
- **spline**: 3/4 → 4/4 kräver TUNING, inte refaktor. Kort väg.
- **curobo**: plan + exec fungerar — saknar bara scene-obstacles. Hög-ROI.
- **diffik**: grip-timing ÄR fixbar med hold-during-dwell + FixedJoint.
- **cube_tracking**: legacy, fuskar med ground-truth pose. Borde leverera ≥3/4 "gratis".

### B. Osäker 4/4 (medel ROI, kan kräva trade-offs):
- **native**: PickPlaceController har inneboende reaktiv dynamik. Dess
  4/4 kan kräva att vi monkey-patchar PickPlaceController internals
  (events_dt + grip-timing). Möjligt men grumligt.
- **sensor_gated**: med rätt coord-IK-targets och fixed_joint grip-style
  borde 4/4 vara nåbart — men den är redan designad för denna arkitektur.
  Inte rakt på.
- **fixed_poses**: 4/4 kräver att POSERNA är exakt rätt för varje kub.
  Om vi pre-genererar per-kub-sekvens från spline's waypoints så blir
  det ett cheat (pose-replay som gömmer spline-logiken). Meningen med
  fixed_poses är DEMO/cycle-time, inte real delivery.

### C. Låg sannolik 4/4 utan arkitektur-ändring:
- **osc**: Jacobian-transpose impedance har precision-golv. Full OSC med
  mass-matrix skulle hjälpa men Isaac Sim exponerar inte M(q) rent.
  Troligt tak: 2-3/4 om vi har tur. **Föreslår: acceptera 2/4 som
  mål och märk experimentell.**
- **ros2_cmd**: ingen self-contained 4/4 — ROS2-controller är PER DEFINITION
  beroende av en extern node som gör plan/IK. Om vi skriver den externa
  noden som en rclpy Python-process som speglar spline's logik, då når
  vi 4/4 MEN vi har då byggt "spline över ROS2" och testat ROS2-bridgen,
  inte pick-place själva.

### D. Meta-beslut:
- **Definition av 4/4 per controller** är INTE enhetlig. För demo/replay-
  controllers (fixed_poses, cube_tracking) är 4/4 trivialt om vi gör
  inputen perfekt — vilket då gömmer alla underliggande problem.
- **Ärlig ambition**: 4/4 på `native`, `spline`, `diffik`, `curobo`,
  `sensor_gated`, `cube_tracking`. Aspirationellt 2-3/4 på `osc`.
  `fixed_poses` markeras "demo-only, pose_sequence-beroende". `ros2_cmd`
  markeras "kräver extern orchestrator, levererar det orchestrator gör".

## 2. Delad infrastruktur att bygga FÖRST

Tre komponenter som låser upp flera controllers:

### 2.1 `_grip_helpers` — robust grip-modul

**Problem**: friction-grip med fingers (kp=10000) misslyckas på vissa
cykler pga att kuben inte är exakt mellan fingrarna. FixedJoint via
`UsdPhysics.FixedJoint.Define` mid-sim funkar inte (PhysX läser inte
USD-ändringar efter reset).

**Lösning**: använd **CONTACT-CHECK + TEMPORARY PHYSICS PARENT** istället.
När grip-close fires:
1. Probe att panda_leftfinger + panda_rightfinger har contact med kub (via
   `getContactReports` eller `isaac_sensor`-trigger på fingers)
2. Om kontakt: sätt kuben som `xformOp:translate` reference till
   panda_hand's transform (kinematic parent) — cube follows hand
3. Om ingen kontakt: retry → fail-and-recover (back up + retry)

Alternativt: fortsätta med friction-grip men med längre dwell (2.5s) och
verifiera kontakt innan LIFT.

**Fil**: `service/isaac_assist_service/chat/tools/grip_helpers.py` (ny,
~100 LOC). Exportera `_PP_GRIP_SNIPPET` för insertion i generatorerna.

**Tests**: unit test för contact-check i isolation.

### 2.2 `_obstacle_builder` — USD → WorldConfig för cuRobo

**Problem**: cuRobo planerar utan att veta om kuber/bord/bin → sveper
igenom dem.

**Lösning**: helper som läser USD-prim bounding boxes och returnerar en
`WorldConfig` med Cuboid-primitives.

```python
def build_world_config_from_prims(stage, prim_paths, exclude_being_picked=None):
    from curobo.scene import Scene, Cuboid
    cuboids = []
    for path in prim_paths:
        if path == exclude_being_picked: continue
        bb = UsdGeom.Imageable(stage.GetPrimAtPath(path)).ComputeWorldBound(0, UsdGeom.Tokens.default_).ComputeAlignedRange()
        mn, mx = bb.GetMin(), bb.GetMax()
        center = [(mn[i]+mx[i])/2 for i in range(3)]
        dims = [mx[i]-mn[i] for i in range(3)]
        cuboids.append(Cuboid(name=f"obs_{path.strip('/').replace('/','_')}",
                               dims=dims, pose=center + [1,0,0,0]))  # wxyz identity
    return Scene(cuboid=cuboids)
```

**Fil**: `service/isaac_assist_service/chat/tools/curobo_helpers.py`.

**Tests**: mock stage, verify returned cuboid dims match.

### 2.3 `_ros2_pickplace_node.py` — extern rclpy orchestrator

**Problem**: ros2_cmd generator är en OmniGraph-skeleton som bara
publicerar joint_states och subscribar target_pose — ingen pick-logik.

**Lösning**: dedicerad Python-fil som körs som subprocess:
```
scripts/ros2/pickplace_node.py
```

Noden gör:
1. Subscribe till `/isaac/cube_poses` (publicerad av Isaac Sim)
2. Subscribe till `/isaac/robot/joint_states`
3. Kör state-machine identisk med spline's (6 Cartesian waypoints)
4. Publicera `/isaac/robot/target_pose` (geometry_msgs/PoseStamped)
5. Publicera `/isaac/robot/gripper_cmd` (std_msgs/Bool)

Isaac Sim-sidan måste ha en OmniGraph som:
- Publicerar cube-poser till /isaac/cube_poses
- Subscribar till /isaac/robot/target_pose → IK → writes joint targets
- Subscribar till /isaac/robot/gripper_cmd → gripper.forward()

Detta är REAL work — ~200-300 LOC Python + OmniGraph-utbyggnad.

**Fil(er)**:
- `scripts/ros2/pickplace_node.py` — extern node
- `service/isaac_assist_service/chat/tools/tool_executor.py` —
  utöka `_gen_pick_place_ros2_cmd` med fullständig OmniGraph
- `scripts/qa/ros2_launch_helper.sh` — starta ROS2 daemon + extern node
  parallellt med Isaac Sim

**Tests**: separat integration test som startar daemon + node + Kit,
kör 60s, verifierar cubes_delivered.

## 3. Fasordning

### FAS A — Native-regression-utredning + backup (1h)

User bekräftat: native körde IK+RmpFlow-hybrid till 3/4 (ev. 4/4) före
refactor. Uppgift: identifiera exakta kod-diff som bröt den.

- [ ] `git log --follow -p -- service/isaac_assist_service/chat/tools/tool_executor.py |
      grep -E "^(\+|-).*_gen_pick_place_native|_guide_via_ik|_last_ik_cspace|set_cspace_target"`
- [ ] Jämför pre-FAS1 vs post-FAS1 versioner av native-generator (rader ~26154-26830)
- [ ] Särskilt: flödet kring `controller.forward()` + `_cjp = franka.get_joint_positions()`
      + `_guide_via_ik(...)` ordning per tick
- [ ] Kandidat-hypoteser att verifiera:
      (H1) `_PP_OBSERVABILITY_SNIPPET` insertion flyttade `S["mode"]`-initiering
           efter en tick — missar första pick-window
      (H2) Scene Reset Manager stale-hook pollution mellan benchmark-runs
      (H3) Belt-pause-regression: resume-mellan-cykler utan frysa
      (H4) ctrl:mode-set överskriver tidigare ctrl:phase write
- [ ] `git stash` + branch `feat/all-4-of-4-push`
- [ ] Snapshot baseline: `python -m scripts.qa.benchmark_controllers
      --controllers native,spline,diffik,osc,curobo --n-runs 3 --out /tmp/bench_pre_4of4.json`

**Exit**: native-regressionens root cause identifierad; baseline frysd;
branch skapad.

### FAS B — Shared helpers (2-3h)
- [ ] Bygg `_grip_helpers.py` med contact-check + kinematic-parent-attach
- [ ] Bygg `_obstacle_builder` i `curobo_helpers.py`
- [ ] Unit tests: `tests/controllers/test_grip_helpers.py`,
      `tests/controllers/test_obstacle_builder.py`
- [ ] Regress: spline fortfarande 3/4 (helpers inte kopplade än)

**Exit**: helpers green, inga regressioner.

### FAS C — spline: 3/4 → 4/4 (1-2h)
- [ ] Analysera vilken kub som missar i varje run (var landar den?)
- [ ] Hypotes A: bin-bbox shift pga första kuben i bin → drop-xy glider
  över tid. Fix: frys drop-xy vid första kub-leverans.
- [ ] Hypotes B: IK warm_start för sista kuben divergerar. Fix: chain från
  HOME_Q varje cykel (redan delvis gjort).
- [ ] Hypotes C: friction-grip misslyckas pga finger-drift under transit.
  Fix: använd `_grip_helpers` från FAS B.
- [ ] Iterera tills 4/4 i 3 on-efter-varandra runs.

**Exit**: `--controller spline` → 4/4 deterministiskt, 3 runs.

### FAS D — curobo: 0/4 → 4/4 (2-3h)
- [ ] Wire `scene_model` från `_obstacle_builder` (FAS B). Inkludera:
      ConveyorBelt, Table, Bin (wall-outer-bbox), alla Cube_N utom den
      just-plockade.
- [ ] Update scene-obstacle-set före varje plan-segment (picked cube
      exkluderas från obstacles; delivered cubes kan sitta i bin-bbox
      eller exkluderas också).
- [ ] Använd `_grip_helpers` (FAS B).
- [ ] Verifiera med run — förhoppning 4/4 pga global trajektorie-optimering.

**Exit**: `--controller curobo` → 4/4 med scene_model. Om <4/4: dokumentera
vilken kub + hypotes.

### FAS E — diffik: 0/4 → 3-4/4 (1-2h)
- [ ] Lägg till hold-during-dwell: vid grip-events, skippa
      `_dik.compute()` under 1-2s och apply_action(håll senaste joint-konfig)
- [ ] Använd `_grip_helpers` (FAS B)
- [ ] Öka dwell_dt från 1.2s till 2.0s

**Exit**: ≥3/4. 4/4 bonus men inte krav.

### FAS F — native: regression-recovery → 3-4/4 (1.5-3h)

Nu KONFIRMERAD reachable — IK+RmpFlow-hybriden har levererat 3/4 tidigare.
Uppgift: hitta vad som brutits och återställa.

Flödet per tick i native (var den levererade 3/4):
1. Sensor-trigger → picked_path set
2. `controller.reset()` + `_pause_belt()`
3. Per tick i "picking":
   - `_guide_via_ik(cube_pos[:2], cube_z_or_h1)` → beräknar IK, sätter cspace target
   - `_cjp = franka.get_joint_positions()`
   - `actions = controller.forward(picking_position=cube_pos, placing_position=drop_pos,
       current_joint_positions=_cjp, end_effector_offset=EE_OFFSET)`
   - `art_ctrl.apply_action(actions)` (med None-guard)
4. När `controller.is_done()`: bump counter, reset state

Från FAS A ska vi ha identifierat vilken av H1-H4 som bröt. Åtgärder:

- [ ] Tillämpa FAS A's root-cause fix (troligen Scene-Reset-Manager
      stale-hook cleanup added to native's install — samma pattern som
      spline/diffik/osc/curobo fick)
- [ ] Verifiera `_guide_via_ik` anropas varje tick med rätt target (cube
      xy+z under descending, drop xy+z under transit)
- [ ] Frys belt mellan cykler (kopia av spline-patterns: resume endast
      när `len(delivered) >= len(SOURCE_PATHS)`)
- [ ] Använd `_grip_helpers` (FAS B) för robust cube-pickup
- [ ] Om fortfarande <3/4: tune `events_dt` aggressivt
- [ ] Kör 3 back-to-back runs

**Exit**: ≥3/4 deterministiskt. 4/4 bonus.

### FAS G — sensor_gated: untested → 4/4 (1-2h)
- [ ] Testa coord-IK-style: pass pick_target + drop_target + home_target
      i args. Använd samma koordinater som spline.
- [ ] grip_style='fixed_joint' (redan robust i sensor_gated — den har
      working FixedJoint-patch från tidigare)
- [ ] Om det inte fungerar direkt: debug RmpFlow-konfig-upptäckt

**Exit**: `--controller sensor_gated` + coord-IK-args → 4/4.

### FAS H — cube_tracking: untested → 4/4 (1h)
- [ ] Smoke-test `--controller cube_tracking`
- [ ] Om funktion: verifiera 4/4 (bör vara lätt pga omniscient)
- [ ] Annars debug

**Exit**: 4/4 eller dokumenterad begränsning.

### FAS I — fixed_poses: untested → 4/4 (1h)
- [ ] Generera pose_sequence från spline's waypoints (8 poser × 4 kuber = 32)
- [ ] Installera `setup_pick_place_controller(target_source='fixed_poses',
      pose_sequence=[...], cycles=1)`
- [ ] Verifiera

**Exit**: 4/4. Notera i docs att "fixed_poses är pose-replay; 4/4 betyder
att inputen räcker, inte att kontrollen är smart".

### FAS J — osc: 0/4 → 2-3/4 (2-4h, osäker)
- [ ] Försök med `partial_inertial_dynamics_decoupling=True` + M(q) från
      `_articulation_view._physics_view.get_mass_matrices()` om exponerat
- [ ] Använd `_grip_helpers`
- [ ] Acceptera 2/4 om OSC's natur gör 4/4 opraktiskt

**Exit**: ≥2/4, dokumenterat som experimentell.

### FAS K — ros2_cmd: stub → 4/4 via extern orkestrator (3-5h)
- [ ] Utöka `_gen_pick_place_ros2_cmd` med komplett OmniGraph (IK-node +
      ArticulationView write)
- [ ] Skriv `scripts/ros2/pickplace_node.py` (rclpy)
- [ ] Skriv `scripts/qa/ros2_launch_helper.sh`
- [ ] Testa end-to-end: Kit + daemon + extern node

**Exit**: `--controller ros2_cmd` + ros2-launch-helper → 4/4.

### FAS L — Video-rendering för motion-kvalitet (1-2h)

Efter 4/4 nått på målcontrollers, rendera korta videos för användarfeedback:

- [ ] `scripts/qa/render_controller_video.py` — tar `--controller X`,
      bygger scenen, installerar controller, kör 120s, renderar viewport
      @60Hz till `~/workspace/videos/<controller>_<ts>.mp4` via
      `omni.kit.capture.viewport` eller `omni.replicator`
- [ ] Kör för alla controllers som nådde ≥3/4
- [ ] Pack videos + lägg i browserbar directory
- [ ] Snabbspola: render med timescale=4x eller MP4-speed=2x för snabb
      review

**Exit**: användaren har ett set videos att bedöma motion-smoothness på.

### FAS M — Regress-sweep (1h)
- [ ] Full benchmark 5 runs × 8 controllers med nya helpers
- [ ] JSON-rapport till `/tmp/bench_post_4of4.json`
- [ ] Vinnare-tabell per controller-klass

**Exit**: reproducerbar 4/4 på minst 5 av de 8 huvudcontrollers.

## 4. Tidsbudget

| FAS | Estimate | Buffer 30% | Total |
|---|---|---|---|
| A Utredning | 0.5h | 0.2h | 0.7h |
| B Helpers | 3h | 0.9h | 3.9h |
| C spline | 2h | 0.6h | 2.6h |
| D curobo | 3h | 0.9h | 3.9h |
| E diffik | 2h | 0.6h | 2.6h |
| F native | 3h | 0.9h | 3.9h |
| G sensor_gated | 2h | 0.6h | 2.6h |
| H cube_tracking | 1h | 0.3h | 1.3h |
| I fixed_poses | 1h | 0.3h | 1.3h |
| J osc | 3h | 0.9h | 3.9h |
| K ros2_cmd | 4h | 1.2h | 5.2h |
| L Video | 1.5h | 0.5h | 2h |
| M Regress | 1h | 0.3h | 1.3h |
| **Summa** | **27h** | **8.2h** | **~35h** |

Realistiskt: 4-5 arbetsdagar om fokuserat. Plus interruption-tid.

## 5. Risklog

| Risk | Severity | Mitigation |
|---|---|---|
| native inte fixbar till 4/4 utan patcha PickPlaceController | high | Acceptera 2/4 + dokumentera inherent reaktiv limit |
| osc aldrig 4/4 utan M(q)-access | high | Acceptera 2/4 + märka experimentell |
| FixedJoint.Define mid-sim inkonsekvent | medium | Contact-check + kinematic-parent fallback |
| ros2_cmd extern node introducerar sync-buggar | medium | Börja med 60Hz target-publish, slow ner om racey |
| curobo scene_model update per cykel är dyrt | medium | Cache WorldConfig, rebuild bara när cube-set ändras |
| video-render kostar >10 min per controller | low | Batch-köra over natt eller timescale speedup |
| spline's "sista kub"-problem kräver algoritmändring | low | Frys drop-xy vid cycle 1; kompensera bbox-drift |
| Motion-smoothness subjektiv bedömning — användaren gillar inte resultaten | medium | Rendera MED vs UTAN IK-guide, MED vs UTAN dwell → användare kan välja |

## 6. Framgångskriterier

**MÅSTE**:
- ≥5 controllers på 4/4 deterministiskt (3 back-to-back runs identiska)
- Videos renderade för alla på ≥3/4
- Inga regressioner i list_available_controllers / auto-resolver
- conveyor_pick_place_incidents.md uppdaterad med I-36+ (nya issues)

**BONUS**:
- ROS2 end-to-end fungerande
- OSC 2/4 med M(q)
- CP-01.json uppdaterad med vinnare (om ej spline längre)
- controller_matrix.md leaderboard uppdaterad

**INTE MÅSTE**:
- 4/4 på alla 10 moden — några är arkitekturellt begränsade
- Best-in-class motion quality (subjektiv feedback-loop)

## 7. Beslut som krävs från användaren

1. ~~Bekräfta native-historik~~ ✅ **Klargjort 2026-04-21**: native IK+RmpFlow-
   hybrid levererade 3/4 (tillfälligt 4/4) före spline-splitten. Koden
   finns kvar men regress har införts — FAS A fixar.
2. **OSC ambition**: acceptera 2/4 som "experimentell fine" eller
   investera i mass-matrix-work?
3. **ROS2 arkitektur**: full extern rclpy-node (3-5h work) eller skippa
   och dokumentera "kräver extern orchestrator"?
4. **Video-format**: MP4 4x speed per controller, eller side-by-side
   grid (alla controllers parallellt i samma video)?
5. **Prioritets-ordning**: default är A → B → C (spline-polish) → D
   (curobo-obstacles) → F (native-recovery) → E (diffik) → G-I
   (sensor_gated/fixed_poses/cube_tracking) → J (osc) → K (ros2) → L
   (video) → M (regress). Vill du att ROS2 ska in tidigare eftersom du
   just bekräftat att ROS2 finns installerat?

## Append: shared-kod-map

Nya filer planerade:
- `service/isaac_assist_service/chat/tools/grip_helpers.py`
- `service/isaac_assist_service/chat/tools/curobo_helpers.py`
- `scripts/ros2/pickplace_node.py`
- `scripts/qa/ros2_launch_helper.sh`
- `scripts/qa/render_controller_video.py`
- `docs/qa/motion_quality_notes.md` (after video review)

Modifierade:
- `tool_executor.py` — per-generator grip_helpers integration,
  curobo scene_model wiring, ros2_cmd graph-expansion
- `tool_schemas.py` — ev. nya params för grip_mode, obstacle_paths
- `controller_matrix.md` — leaderboard update
- `conveyor_pick_place_incidents.md` — I-36..I-4N nya issues
- `CP-01.json` — om vinnare skiftar från spline till curobo

## Nästa steg — konkret start-åtgärd

Jag väntar på ditt OK. Start = FAS A (utredning + snapshot), 30 min,
ingen kod-ändring ännu. Säg till om prioritetsordningen ska ändras
(t.ex. ros2_cmd först för att du är mest nyfiken på den, eller
video-rendering innan 4/4 så du kan bedöma spline vs curobo motion
redan nu).
