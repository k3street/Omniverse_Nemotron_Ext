# Making an asset sim-ready

This guide takes a downloaded USD/USDZ file and turns it into an asset that
simulates correctly. "Correctly" means real-world size, a plausible mass,
working collision, and moving parts that move. Every step leaves evidence that
a reviewer can check. The worked example is a steel door with a crash (panic)
bar: the door opens only when the bar is pushed, and the latch shuts again by
itself.

For what the pipeline is and why it is built this way, see
[README §10](../../README.md#10-sim-ready-asset-pipeline). This page is the how-to.

```
ingest ─▶ (segment) ─▶ (draft joints ─▶ review ─▶ apply) ─▶ (animate) ─▶ approve ─▶ promote ─▶ verify
  │            │                 │                              │             │
 scale,     split fused    hinge/slider spec,             MP4 + measured   registry +
 up-axis,   parts          masses, mechanisms             joint log,       portable
 checks                    (latches)                      gate check       library copy
```

Steps in brackets are only for assets with moving parts. A mug goes
ingest → approve.

---

## 1. Setup

There are two interpreters. Most steps only need OpenUSD; animation and live
checks need Isaac Sim.

```bash
# OpenUSD (pxr) for ingest, segmentation, drafting and the hub
export PYTHONPATH=$HOME/Documents/Github/openusd_build/lib/python
export LD_LIBRARY_PATH=$HOME/Documents/Github/openusd_build/lib

# Isaac Sim's python, for animation (headless)
ISAAC_PY=$HOME/Documents/Github/isaacsim/_build/linux-aarch64/release/python.sh
```

**One Isaac process at a time on the DGX Spark.** Running two Kit processes
at once has wedged the GPU driver, and only a reboot clears it. Anything that
starts Isaac should first take the shared slot, which waits its turn behind
other projects:

```bash
( source scripts/isaac_slot.sh && $ISAAC_PY scripts/animate_asset.py <asset_id> )
```

The review hub is where a person looks at each asset and signs it off:

```bash
./launch_review_hub.sh            # http://127.0.0.1:8777
```

---

## 2. A rigid asset (the short path)

```bash
python3 scripts/ingest_asset.py ~/Downloads/coffee_mug.usdz --class-hint mug
```

Ingest writes a derivative under `workspace/assets_fixed/<id>_simready.usda` and
never modifies the source file. It then:
- **Fixes scale and up-axis.** It reads the file's units and compares the
  bounds with the class prior: a 4 m mug gets scaled.
- **Authors rigid physics.** The mass comes from the class prior, and the
  collision is a convex hull.
- **Queues the asset in the hub.** You get a report, a thumbnail and callouts.

In the hub, check the thumbnail and callouts, choose a category, and press
**Approve**. Approval promotes the asset into
`workspace/asset_library/<id>/`, a portable copy with the source included, and
records it in the registry (`workspace/knowledge/sim_ready_assets.json`).
`scripts/verify_asset_live.py` then drop-tests it in PhysX and stores the
measurements.

For whole folders, use `ingest_asset.py --scan DIR`. To sign off rigid assets
automatically, use `scripts/visual_qa.py`; see README §10.1.

---

## 3. Worked example: a door with a crash bar

The source is `~/Downloads/door_door_metal.usdz`, a Sketchfab model. It comes
as one fused mesh in centimetres, Y-up and 3 m tall. Everything below was run
on 2026-10-01; the outputs shown are real.

### 3.1 Ingest

```bash
python3 scripts/ingest_asset.py ~/Downloads/door_door_metal.usdz --class-hint door --id door_metal_auto
```

```
queued door_metal_auto: PASS pending human review (0 errors, 2 callouts)
```

Ingest scaled it by 0.7 to a standard 2.1 m door and converted it to Z-up. It
raised two callouts:
- **No joints:** a door has moving parts and the file has 2 meshes but no
  joints.
- **No physics:** nothing is authored yet.

### 3.2 Segment

Scanned models often fuse separate parts into one mesh. Segmentation splits a
mesh into its separate pieces, the geometry islands that share no vertices:

```bash
python3 scripts/segment_mesh.py door_metal_auto
```

```
segmented Object_1 into 2 parts; asset now has 3 meshes
```

The crash bar comes out as its own part. The leaf is still joined to its
frame: they share vertices, so connectivity alone cannot separate them. The
door step of the drafter (next) handles that.

If a part ever needs splitting by hand, cut it with a world-space box. Faces
entirely inside the box become one part and the rest become the other; `-`
leaves that side of the box open:

```bash
python3 scripts/segment_mesh.py --box door_metal_auto Object_1_part01 - 0.029 - - - - DoorLeaf DoorFrame
```

### 3.3 Draft the joints

In the hub, press **Draft articulation spec** on the asset's card. To do the
same from a script:

```python
import json, sys
sys.path[:0] = ["scripts", "."]
import asset_review_hub as hub
entry = json.load(open("workspace/review_queue/door_metal_auto.json"))
print(hub.draft_articulation(entry))
```

```
door draft: leaf DoorLeaf, frame DoorFrame, push bar Object_1_part00, push side -Y,
hinge at X=-0.434 — split the leaf out of the frame by its front/back faces —
check hinge side and swing, then Apply
```

Because the class is `door`, the drafter uses its door step
(`scripts/door_draft.py`):
- **Leaf:** it finds the leaf's large front and back faces and splits out
  everything between them.
- **Crash bar:** it finds the bar, a long horizontal part at hand height
  standing proud of one face.
- **Hinge side:** the hinge goes on the edge the bar points away from.
- **Swing:** the door swings away from the face the bar is on.

The class prior supplies what geometry cannot measure: bar travel, swing range,
masses and the latch. That's `mechanism_templates.panic_bar_latch` under
`door` in `workspace/knowledge/asset_class_priors.json`.

Other classes use the generic step, which detects wheels and fixes everything
else to the base. You then edit the joint types and limits yourself.

### 3.4 Review the spec

The draft appears in an editable box on the card. Read it before applying.
This excerpt is shortened, with the long prim paths elided:

```json
{
 "prim_path": "/World/DoorMetalAuto",
 "fixed_base": true,
 "approximation": "convexDecomposition",
 "joints": [
  {"name": "door_hinge", "joint_type": "revolute", "parent_prim": ".../DoorFrame",
   "child_prim": ".../DoorLeaf", "axis": "Z", "lower_limit": 0.0, "upper_limit": 90.0,
   "anchor": [-0.4339, 0.0802, 1.007], "stiffness": 0.0, "damping": 0.2},
  {"name": "crash_bar_push", "joint_type": "prismatic", "parent_prim": ".../DoorLeaf",
   "child_prim": ".../Object_1_part00", "axis": "Y", "lower_limit": 0.0, "upper_limit": 0.02,
   "stiffness": 2500.0, "damping": 50.0},
  {"name": "Object_0_on_leaf", "joint_type": "fixed", "parent_prim": ".../DoorLeaf",
   "child_prim": ".../Object_0"}
 ],
 "link_masses": {".../DoorFrame": 20.0, ".../DoorLeaf": 35.0, ".../Object_1_part00": 2.0, ".../Object_0": 1.5},
 "no_collision": [".../Object_0"],
 "filtered_pairs": [[".../DoorFrame", ".../Object_1_part00"]],
 "mechanisms": [{"type": "latch", "hinge_joint": "door_hinge",
                 "actuator_joint": "crash_bar_push", "leaf": ".../DoorLeaf", "frame": ".../DoorFrame"}]
}
```

What to check:

| Key | Meaning | Units |
|---|---|---|
| `axis` | A **world** axis: X, Y or Z | — |
| `lower_limit` / `upper_limit` | Joint range | degrees for revolute joints, metres for prismatic |
| `anchor` | The pivot point, in world coordinates | metres |
| `stiffness` / `damping` | A spring toward the drive target. `stiffness: 0` means it swings freely, like a hinge with no closer | per degree for revolute joints |
| `link_masses` | Per-part masses. Unlisted parts get a volume share of the class mass at promotion | kg |
| `no_collision` | Parts that should not collide; a flat glass pane has no volume | — |
| `filtered_pairs` | Pairs of parts that must not collide with each other | — |
| `mechanisms` | Couplings between joints (§4) | — |

If the drafter guessed the hinge side wrong, swap the anchor's X to the other
edge and flip the limits to `[-90, 0]`. Then remove the `_analysis` and
`_instructions` keys.

### 3.5 Apply

Press **Apply articulation**. To do it from a script:

```python
print(hub.apply_articulation(entry, entry["articulation_draft"]))
hub.save_queue_entry(entry)
```

```
articulation applied (3 joints) — re-checked: PASS pending human review
```

Applying authors the following into the derivative USD:
- **Bodies and colliders:** rigid bodies and collision shapes for every part.
- **Joints:** written under `<asset>/Joints`, with world-aligned frames so
  each axis is a world axis.
- **Base anchor:** the frame is fixed to the world where it stands.
- **The latch,** described in §4.

### 3.6 Animate

Press **Animate joints (video)**, or run:

```bash
( source scripts/isaac_slot.sh && $ISAAC_PY scripts/animate_asset.py door_metal_auto )
```

This runs headless Isaac Sim. It drives every joint through its range, records
`workspace/asset_animations/<id>/<id>.mp4`, and logs each joint's **measured**
position on every frame. The video is for people; the log is the evidence.
The hub shows the video on the asset's card with this summary:

```
door_hinge:     range [0.0, 90.0] (limits [0.0, 90.0]), max tracking error 5.2
crash_bar_push: range [0.0, 0.02] (limits [0.0, 0.02]), max tracking error 0.017
latch_bolt:     follows, range [-0.025, 0.0]
GATE door_hinge: HELD — pushed toward 54.0, moved 0.904
```

Here is what that summary shows:
- **The latch held:** the door was shoved toward 54° with the bar at rest and
  moved only 0.9°.
- **The bar works:** pushed in, it retracted the bolt the full 25 mm.
- **Full swing:** the door then opened to 90°.
- **It re-latched:** with the bar released, the closing door pushed the bolt
  in and it snapped back behind the keeper, ending at 0°.

The 17 mm bar "error" happens during that last step: the bolt pushes the bar
in, exactly as on a real device.

Other files in the same folder:

| File | Contents |
|---|---|
| `joints.csv` | Commanded vs measured position per joint, every frame, labelled by segment |
| `summary.json` | Reach, tracking error and gate results |
| `contact_sheet.png` | Six frames across the run |
| `frames/` | Every rendered frame |

Options: `--seconds-per-joint`, `--fps`, `--width/--height`,
`--push-torque` (the shove used to test a gate; default 80 N·m) and
`--push-force` (the same for sliders; default 100 N). You can also pass a USD
path instead of an asset id; for example, animate a library asset with
`scripts/animate_asset.py workspace/asset_library/overbed_table/overbed_table.usda`.

### 3.7 Approve, promote, verify

Choose `articulated_unverified` and press **Approve**. Promotion copies the
asset into `workspace/asset_library/<id>/`, binds physics materials, and leaves
the masses you set alone. It also refuses to promote while error callouts
remain. `scripts/verify_asset_live.py <id>` then drive-tests each joint in a
live Isaac session and moves the category to `articulated_verified` when the
measurements pass.

---

## 4. Joint dependencies (gates and mechanisms)

A joint graph says how parts move. It cannot say that one joint only works when
another has moved, such as a door that opens only once its bar is pushed.
There are two ways to express that.

**A physical mechanism (preferred).** The simulator enforces the dependency
itself, so it holds for a robot, a person or a script alike. The latch
(`scripts/add_mechanism.py`, applied from `"mechanisms"` in the spec) authors:
- **A bolt** on the leaf's latch edge: a sliding part with a 25 mm throw. Its
  tip is angled on the closing side, as a real latch bolt is.
- **A PhysX mimic joint** that couples the bolt to the bar:
  `bolt + gearing × bar = 0`. A full 20 mm push retracts the bolt by 25 mm.
- **A keeper**: a static block mounted on the strike jamb, on the side the
  door swings toward. With the bolt out, the keeper stops the door.

**A gate record.** The latch also stores this on the hinge (customData):

```json
"simReady:gate": {"mechanism": "latch", "actuator_joint": "crash_bar_push",
                  "engage": 0.02, "bolt_joint": "latch_bolt"}
```

Tools that drive joints read it and know to work the bar first. The animator
uses it to run the gate check. A gate record with **no** physical mechanism
behind it is only a rule: only the tools that read it enforce it. Use that
form when the dependency has no physical form, such as "drawer locked until
the key turns".

To add a latch to an asset that is already articulated:

```bash
python3 scripts/add_mechanism.py door_door_metal latch \
  '{"hinge_joint": "door_hinge", "actuator_joint": "crash_bar_push",
    "leaf": "<leaf prim path>", "frame": "<frame prim path>"}'
```

The latch works out everything else from the hinge and the parts' bounds: the
latch edge, the throw direction, the height (the bar's), the swing side, and
the gearing sign. Version 1 supports vertical hinges whose leaf width runs
along world X.

**Teaching a new class.** Add a `mechanism_templates` entry to that class in
`asset_class_priors.json`, and a drafting step that knows where the actuator
lives, as `door_draft.py` does for doors. Knowledge about an object type
belongs in the priors, not in geometry heuristics. The routed-cord feature
already works this way: which end a cord leaves a device from is stored per
class (`cord_exit`).

---

## 5. Troubleshooting

These problems all came up on the way to the door example. Each one fails
silently unless you measure.

| Symptom | Cause | Fix |
|---|---|---|
| Ingest crashes with `No module named 'pydantic'` | Older ingest imported the optional NVIDIA validator eagerly | Fixed; `NVIDIA_USD_VALIDATION_ON_INGEST=0` also skips it |
| `launch_isaac.sh` segfaults about 3 s in, after `No module named 'psutil'` | Kit's embedded Python ignores `PYTHONPATH`, and this build's pip archive lacks psutil and PIL | Fixed in `launch_isaac.sh`, which passes the kernel's site-packages via `--/app/python/extraPaths` |
| A part added under an asset lands tiny or in the wrong place | The ingest wrapper rotates and scales everything under the asset root (here ×0.007, Y→Z) | Author points in the parent's local space; `add_mechanism.py` does this |
| The door starts below its 0° limit, or the simulation hangs | Two colliders start overlapping. A convex decomposition can bulge past a part's true face | Filter that pair (`filtered_pairs`); the latch filters the keeper↔leaf and bolt↔bar pairs |
| A slider won't move at all | Its end sits within the contact offset of another part, so it jams before moving | Filter the pair |
| Headless physics doesn't advance: time stays at 0 and poses never change | In a headless `SimulationApp`, playing the timeline does not step PhysX | Call `px.start_simulation()` and then `px.update_simulation(dt, t)`; call `px.update_transformations(False, True, False, False)` before rendering. `animate_asset.py` does this |
| Video shows nothing, though the joint log looks right | A free-standing asset fell out of shot | The animator adds a ground collider when nothing anchors the asset to the world |
| A latched door stalls a few degrees short of closed | The drive's push fades as the error shrinks, and the bolt needs force to be pushed past the keeper | The animator aims past closed, like a door closer's preload; the joint limit stops the door |
| The door won't close, even though the file says the limit is 0° | The Physics Inspector edited a limit in the live session (unsaved) | Reopen the stage, or reset the attribute |
| An animation recorded with the timeline stopped shows no motion | The Physics Inspector simulates on its own while the timeline is stopped | Start the timeline before recording; the animator steps PhysX itself |

**Known issue:** Isaac Sim's GUI hung on Play after the latched door was
reopened in the same session. The headless animator never hangs. The suspected
cause, unconfirmed, is Isaac Assist's ROS 2 articulation bridge re-attaching
on every reopen. If it happens, restart Isaac instead of reopening the stage.

---

## 6. Where things are

| File | Role |
|---|---|
| `scripts/ingest_asset.py` | Ingest, scale and orientation fixes, report, queue |
| `scripts/segment_mesh.py` | Split fused meshes, by connectivity or with `--box` |
| `scripts/articulation_draft.py` | Generic joint drafter (wheels) |
| `scripts/door_draft.py` | Door drafter (leaf, frame, push bar, hinge, latch) |
| `scripts/add_mechanism.py` | Latch: bolt, keeper, mimic coupling, gate |
| `scripts/animate_asset.py` | Headless video, joint log and gate check |
| `scripts/asset_review_hub.py` | Review hub, port 8777 (draft, apply, animate, approve) |
| `scripts/promote_asset.py` | Approved asset → portable library copy |
| `scripts/verify_asset_live.py` | Live PhysX drop and drive tests → registry evidence |
| `scripts/critique_render.py` | Vision critic, run on renders before people see them |
| `workspace/knowledge/asset_class_priors.json` | Class knowledge: size, mass, materials, mechanism templates |
| `workspace/knowledge/sim_ready_assets.json` | Registry of approved assets and their evidence |
| `tests/test_door_mechanism.py`, `tests/test_sim_ready.py` | Tests on in-memory stages (they need pxr) |
