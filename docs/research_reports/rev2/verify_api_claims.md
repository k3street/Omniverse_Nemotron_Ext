---
date: 2026-04-15
purpose: Fact-check seven reviewer claims about NVIDIA Isaac Sim extension Python APIs
scope: Isaac Sim 4.5 / 5.1 / 6.0 documentation, GitHub issues, NVIDIA developer forums
verdict_key: "CONFIRMED = reviewer was right | REFUTED = reviewer was wrong | PARTIALLY = nuanced"
---

# API Claims Verification — Isaac Sim Extensions

Seven claims from the previous review team were checked against official NVIDIA Isaac Sim docs
(versions 4.5, 5.1, 6.0), the public GitHub repos, and NVIDIA developer forums. Each claim
is evaluated independently. No speculation — only what is directly documented or explicitly
stated by NVIDIA is treated as evidence.

---

## Claim 1 — `isaacsim.util.camera_inspector` has no Python API (GUI only)

**Verdict: CONFIRMED**

The official extension API page for `isaacsim.util.camera_inspector` (present in 4.5 and 5.0
docs) lists zero Python classes, functions, or methods. The page contains only:
- Extension version and description ("inspect and modify camera properties")
- Three enablement methods (CLI flag, .kit file, Extension Manager UI)

No public Python interface is documented anywhere in the 4.5 or 5.1 docs. The Camera class
in the *separate* `isaacsim.sensors.camera` module does provide a rich headless Python API
(focal length, distortion parameters, sensor captures), but that is a different extension —
it is not part of camera_inspector and does not replicate the inspector's "view all camera
properties in a dropdown" functionality.

No forum threads or GitHub issues reveal an undocumented programmatic API for
camera_inspector.

**Evidence:**
- https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.util.camera_inspector/docs/index.html
- https://docs.isaacsim.omniverse.nvidia.com/5.1.0/sensors/isaacsim_sensors_camera.html

---

## Claim 2 — `isaacsim.robot_setup.gain_tuner` has no Python API for auto-tuning

**Verdict: CONFIRMED**

All three documentation sources checked for the gain tuner returned zero Python classes or
methods:

- The extension API stub page (v4.5, "Gain Tuner for Articulation PD Gains") contains only
  the enablement block — no class or method definitions.
- Tutorial 11 "Tuning Joint Drive Gains" (v6.0) is entirely GUI-driven: all screenshots,
  no Python code.
- Tutorial 06 "Joint Gains Tuning" (v6.0 OpenUSD path) is likewise GUI-only.
- The Gain Tuner Extension user guide (v5.1) describes the UI panels but contains no API.

The underlying physics gains *can* be set from Python by writing USD attributes directly
(e.g. `PhysxSchema.PhysxJointAPI`), but that is raw PhysX USD — not the gain tuner extension
itself. The extension's auto-compute logic (natural frequency / damping ratio → PD gains) is
not exposed in any documented Python call.

**Evidence:**
- https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.robot_setup.gain_tuner/docs/index.html
- https://docs.isaacsim.omniverse.nvidia.com/5.1.0/robot_setup/ext_isaacsim_robot_setup_gain_tuner.html
- https://docs.isaacsim.omniverse.nvidia.com/6.0.0/robot_setup_tutorials/joint_tuning.html
- https://docs.isaacsim.omniverse.nvidia.com/6.0.0/openusd_tuning_tutorials/tutorial_06_joint_gains_tuning.html

---

## Claim 3 — `isaacsim.robot_setup.wizard` has no Python scripting API

**Verdict: CONFIRMED** (with a narrow caveat on settings)

The API stub page for `isaacsim.robot_setup.wizard` v0.2.0 (in the 6.0 docs) documents only
two settings values (`timeout` and `launch_on_startup`). These can be set via the carb
framework in Python:

```python
import carb
settings = carb.settings.get_settings()
settings.set("/exts/isaacsim.robot_setup.wizard/timeout", 10)
```

That is the full extent of the documented "Python API." It controls extension behavior but
does not expose any robot-import or USD-manipulation functions. There are no documented
Python classes, methods, or commands for programmatically driving the wizard's robot setup
steps (hierarchy definition, collider assignment, joint drive configuration, schema
application).

The Robot Wizard [Beta] user guide (v6.0) is entirely GUI-documented. No headless / scripted
robot-import equivalent is documented within this extension.

**Caveat:** The reviewer's phrasing "no Python scripting API" is essentially correct. Carb
settings are technically callable from Python but are configuration knobs, not a scripting
interface for robot setup automation.

**Evidence:**
- https://docs.isaacsim.omniverse.nvidia.com/6.0.0/py/source/extensions/isaacsim.robot_setup.wizard/docs/index.html
- https://docs.isaacsim.omniverse.nvidia.com/6.0.0/robot_setup/robot_wizard.html

---

## Claim 4 — `isaacsim.util.merge_mesh` has no Python API

**Verdict: CONFIRMED**

Both the 4.5 and 5.1 official API stub pages for `isaacsim.util.merge_mesh` contain no
Python classes, functions, or methods. The tool is documented exclusively as a GUI utility:
"Tools > Robotics > Asset Editors > Mesh Merge Tool."

A separate `isaacsim.util.merge_mesh` entry in the Python API index page (v4.5) exists but
points to the same stub, which has no code.

The Mesh struct documented at `docs.isaacsim.omniverse.nvidia.com/.../struct_mesh.html` does
expose a Python `merge()` method as part of lower-level mesh data structures, but this is
not the same extension and does not replicate the USD-prim-combining workflow of
`isaacsim.util.merge_mesh`.

Forum threads about the merge mesh utility (e.g., discussion #272995) ask about GUI
behavior; NVIDIA's responses reference only the UI tool, not a Python API.

**Evidence:**
- https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.util.merge_mesh/docs/index.html
- https://docs.isaacsim.omniverse.nvidia.com/5.1.0/robot_setup/ext_isaacsim_util_merge_mesh.html
- https://docs.isaacsim.omniverse.nvidia.com/6.0.0/py/api/struct_mesh.html

---

## Claim 5 — `isaacsim.asset.gen.conveyor.ui` has no programmatic API for track building

**Verdict: PARTIALLY — the claim is correct for .ui but overlooks a documented programmatic path in the core extension**

The `.ui` sub-extension is definitively GUI/config-only. Its documentation on 4.5 and 6.0
states: "menu entries to support UI-based creation of Custom Conveyor Bodies, and a Conveyor
Track tool that creates a system of fixed conveyor belts from a dataset." No Python classes
or methods are listed. The Conveyor Track Builder at "Tools > Conveyor Track Builder" is
configured via JSON, not Python.

However, the **core extension** `isaacsim.asset.gen.conveyor` (without `.ui`) does document
a programmatic path:

- The `OgnIsaacConveyor` OmniGraph node (`isaacsim.asset.gen.conveyor.IsaacConveyor`) can
  be instantiated and wired in an OmniGraph constructed from Python using the OmniGraph
  scripting API (`og.Controller`, `omni.kit.commands`).
- The node exposes nine typed inputs (velocity, direction, curved, conveyorPrim, animate*,
  delta, enabled) that can be set programmatically once the graph exists.
- The docs state: "commands to create the conveyor belt Omnigraph programmatically" are
  available, though no specific command name is published in the documentation. The implied
  pattern is `omni.kit.commands.execute("IsaacSimConveyorBelt", ...)` — consistent with how
  other Isaac Sim asset-creation commands work — but this command is not publicly named in
  the docs found.

**Summary:** The reviewer is right that the `.ui` sub-extension itself has no programmatic
API. They are partially wrong at the broader level: the underlying conveyor primitive is
accessible via OmniGraph Python scripting, even though the convenient "track building"
workflow in the UI has no Python equivalent.

**Evidence:**
- https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.asset.gen.conveyor.ui/docs/index.html
- https://docs.isaacsim.omniverse.nvidia.com/latest/py/source/extensions/isaacsim.asset.gen.conveyor/docs/ogn/OgnIsaacConveyor.html
- https://docs.isaacsim.omniverse.nvidia.com/6.0.0/digital_twin/warehouse_logistics/ext_isaacsim_asset_gen_conveyor.html
- https://docs.robotsfan.com/isaacsim/6.0.0/py/source/extensions/isaacsim.asset.gen.conveyor.ui/docs/index.html

---

## Claim 6 — `isaacsim.robot_setup.xrdf_editor` has no headless Python API

**Verdict: CONFIRMED**

Multiple evidence threads converge on the same result:

1. The official API stub page for `isaacsim.robot_setup.xrdf_editor` (labeled "Robot
   Description Editor") explicitly states: **"This documentation is incomplete. To use this
   version, build Isaac Sim from source on GitHub."** No Python classes or methods are listed.

2. Tutorial 8 "Generate Robot Configuration File" (v6.0) demonstrates only GUI steps:
   opening the editor, configuring joints and collision spheres visually, then clicking
   "Export To File." No Python code is shown.

3. The Lula Robot Description and XRDF Editor user guide (v5.1) is a pure GUI walkthrough:
   "Tools > Robotics > Lula Robot Description Editor." All operations are menu/click-based.

4. No forum posts reveal an undocumented headless XRDF generation API.

For generating XRDF-compatible robot descriptions in code, the recommended approach is to
edit or write YAML/XRDF files directly outside Isaac Sim (documented by cuRobo at
curobo.org/tutorials/1_robot_configuration.html), not via the xrdf_editor extension.

**Evidence:**
- https://docs.isaacsim.omniverse.nvidia.com/latest/py/source/extensions/isaacsim.robot_setup.xrdf_editor/docs/index.html
- https://docs.isaacsim.omniverse.nvidia.com/6.0.0/robot_setup_tutorials/tutorial_generate_robot_config.html
- https://docs.isaacsim.omniverse.nvidia.com/5.1.0/manipulators/manipulators_robot_description_editor.html

---

## Claim 7 — `debug_draw` supports only points, lines, lines_spline — no spheres/arrows/boxes/text

**Verdict: CONFIRMED** (for `isaacsim.util.debug_draw`; workarounds exist in Isaac Lab)

The `isaacsim.util.debug_draw` Python API (`DebugDraw` class, acquired via
`_debug_draw.acquire_debug_draw_interface()`) exposes exactly seven methods:

| Method | Purpose |
|---|---|
| `draw_points()` | Batches of points (RGBA + radius) |
| `draw_lines()` | Batches of line segments (RGBA + width) |
| `draw_lines_spline()` | Spline through a set of points (filled or dashed) |
| `clear_points()` | Remove all drawn points |
| `clear_lines()` | Remove all drawn lines |
| `get_num_points()` | Current point count |
| `get_num_lines()` | Current line count |

No `draw_sphere`, `draw_box`, `draw_arrow`, or `draw_text` functions exist in this module.
This is confirmed by:
- The API class documentation (v4.5) which lists only the seven methods above.
- The Debug Drawing Extension API page (v5.1 and latest) which states: "This submodule
  provides bindings to draw debug lines and points."
- A NVIDIA developer forum thread (forums.developer.nvidia.com/t/more-control-with-debug-draw/333504)
  where NVIDIA confirms that selective deletion of individual drawn elements is unsupported
  ("We have created an internal ticket") — and no sphere/box/text primitives are mentioned
  even in that discussion about adding features.
- No changes to debug_draw primitives appear in the Isaac Sim 5.1 or 6.0 release notes.

**Workarounds that do support spheres/arrows/boxes:**
- **Isaac Lab** `isaaclab.markers.VisualizationMarkers` class — supports spheres, cuboids,
  cylinders, cones, arrows, frames. This is an Isaac Lab feature, not native Isaac Sim. It
  requires Isaac Lab to be installed and uses `UsdGeom.PointInstancer` internally.
- **UsdGeom prims** — spheres, boxes, etc. can be created as persistent USD prims via
  `UsdGeom.Sphere`, `UsdGeom.Cube`, etc. in Python. This is a general Omniverse capability,
  not a debug draw equivalent.
- **OmniGraph nodes** — `OgnIsaacXPrimAxisVisualizer` is part of the debug_draw extension
  and visualizes axis frames on XPrim prims, but is not a general-purpose shape renderer.

The reviewer's claim is technically accurate as stated. However, the absence of
spheres/arrows/boxes/text in `debug_draw` does not mean they cannot be drawn in Isaac Sim —
it means a different API must be used.

**Evidence:**
- https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.util.debug_draw/docs/index.html
- https://docs.isaacsim.omniverse.nvidia.com/5.1.0/utilities/debugging/ext_isaacsim_util_debug_draw.html
- https://isaac-sim.github.io/IsaacLab/main/source/how-to/draw_markers.html
- https://forums.developer.nvidia.com/t/more-control-with-debug-draw/333504

---

## Summary Table

| # | Claim | Verdict | Key Finding |
|---|---|---|---|
| 1 | `camera_inspector` — GUI only, no Python API | **CONFIRMED** | Zero Python symbols documented across all versions |
| 2 | `gain_tuner` — no Python auto-tuning API | **CONFIRMED** | All tutorials and API stubs are GUI-only |
| 3 | `robot_setup.wizard` — no Python scripting API | **CONFIRMED** | Only carb settings (not a functional scripting API) |
| 4 | `merge_mesh` — no Python API | **CONFIRMED** | GUI tool only; separate struct_mesh is a different module |
| 5 | `conveyor.ui` — no programmatic API for track building | **PARTIALLY** | `.ui` is GUI+JSON only, but core OmniGraph node supports Python wiring |
| 6 | `xrdf_editor` — no headless Python API | **CONFIRMED** | Docs explicitly incomplete; all workflows GUI-only |
| 7 | `debug_draw` — no spheres/arrows/boxes/text | **CONFIRMED** | 7 methods total: only points, lines, splines; Isaac Lab offers VisualizationMarkers as workaround |

**Overall:** 6 of 7 claims are fully confirmed. Claim 5 is partially confirmed — the specific
`.ui` sub-extension is correctly characterized, but the reviewer's phrasing missed that
the underlying conveyor OmniGraph node can be wired from Python scripts.
