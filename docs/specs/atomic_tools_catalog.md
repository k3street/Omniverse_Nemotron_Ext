# Atomic Tools Catalog — 100 Missing Operations

**Status:** Not implemented  
**Source:** `rev2/brainstorm_missing_atomic_tools.md` (full details) + `brainstorm_tool_combinations.md`

---

## Why These Matter

The existing 50 tools are heavily WRITE-biased. LLM can build scenes but can't verify what it built. Adding these 100 atomic tools (most trivial to implement) unlocks dozens of new workflows via combination, not new code.

**Strategy:** Build in priority tiers. Each tier unlocks a specific class of workflows.

---

## Tier 0 — Foundation (Build First)

These 12 tools close the read/write asymmetry and bridge tool clusters. Without them, many existing tools can't form complete workflows.

| # | Tool | Implementation | Unlocks |
|---|------|---------------|---------|
| 1 | `get_attribute(prim_path, attr_name)` | One line: `prim.GetAttribute(attr_name).Get()` | Verify every set_attribute |
| 2 | `get_world_transform(prim_path)` | `UsdGeom.Xformable.ComputeLocalToWorldTransform()` | Spatial reasoning |
| 3 | `get_bounding_box(prim_path)` | `UsdGeom.BBoxCache.ComputeWorldBound()` | Placement, clearance |
| 4 | `set_semantic_label(prim_path, class_name)` | `Semantics.SemanticsAPI.Apply()` | SDG annotation |
| 5 | `get_joint_limits(articulation, joint_name)` | `UsdPhysics.RevoluteJoint.GetLowerLimitAttr()` | Safety before motion |
| 6 | `set_drive_gains(joint_path, kp, kd)` | `UsdPhysics.DriveAPI.GetStiffnessAttr().Set()` | RL tuning |
| 7 | `get_contact_report(prim_path)` | PhysX contact subscribe | Grasp detection |
| 8 | `set_render_mode(mode)` | Render settings switch | Preview↔SDG quality |
| 9 | `set_variant(prim_path, variant_set, variant)` | `UsdVariantSet.SetVariantSelection()` | Asset configuration |
| 10 | `get_training_status(run_id)` | Read TensorBoard + subprocess state | Monitor training |
| 11 | `pixel_to_world(camera, x, y)` | Camera projection + depth buffer | Click-to-position |
| 12 | `record_trajectory(articulation, duration)` | Subscribe physics step, sample joint state | Behavior comparison |

---

## Tier 1 — USD Core (10 tools)

USD stage inspection — making scene structure queryable.

| # | Tool | Description |
|---|------|-------------|
| T1.1 | `list_attributes(prim_path)` | Enumerate all attributes on a prim |
| T1.2 | `list_relationships(prim_path)` | List all relationships (target paths) |
| T1.3 | `list_applied_schemas(prim_path)` | Show which API schemas are applied |
| T1.4 | `get_prim_metadata(prim_path, key)` | Read USD metadata (kind, specifier, etc.) |
| T1.5 | `set_prim_metadata(prim_path, key, value)` | Write metadata |
| T1.6 | `get_prim_type(prim_path)` | Return typeName (Mesh, Xform, Camera, etc.) |
| T1.7 | `find_prims_by_schema(schema_name)` | Find all prims with ArticulationRootAPI, etc. |
| T1.8 | `find_prims_by_name(pattern)` | Regex search on prim paths |
| T1.9 | `get_kind(prim_path)` | Read Kind metadata (component, assembly, etc.) |
| T1.10 | `get_active_state(prim_path)` | Check if prim is active/deactivated |

## Tier 2 — Physics Bodies & Scene (10 tools)

| # | Tool | Description |
|---|------|-------------|
| T2.1 | `get_linear_velocity(prim_path)` | Current rigid body linear velocity |
| T2.2 | `get_angular_velocity(prim_path)` | Current rigid body angular velocity |
| T2.3 | `set_linear_velocity(prim_path, vel)` | Set rigid body velocity |
| T2.4 | `get_mass(prim_path)` | Current mass value |
| T2.5 | `get_inertia(prim_path)` | Current inertia tensor |
| T2.6 | `get_physics_scene_config()` | Solver type, iterations, dt, GPU flags |
| T2.7 | `set_physics_scene_config(config)` | Update solver settings |
| T2.8 | `list_contacts(prim_path)` | All current contact pairs for a body |
| T2.9 | `apply_force(prim_path, force, torque)` | External force/torque application |
| T2.10 | `get_kinematic_state(prim_path)` | Full kinematic state (pos, vel, accel) |

## Tier 3 — Articulation & Joints (9 tools)

| # | Tool | Description |
|---|------|-------------|
| T3.1 | `get_joint_positions(articulation)` | Current joint positions vector |
| T3.2 | `get_joint_velocities(articulation)` | Current joint velocities |
| T3.3 | `get_joint_torques(articulation)` | Applied joint torques |
| T3.4 | `get_drive_gains(joint_path)` | Current kp/kd values |
| T3.5 | `set_joint_limits(joint_path, lower, upper)` | Modify joint range |
| T3.6 | `set_joint_velocity_limit(joint_path, vel_limit)` | Max velocity constraint |
| T3.7 | `get_articulation_mass(articulation)` | Total mass of all links |
| T3.8 | `get_center_of_mass(articulation)` | CoM world position |
| T3.9 | `get_gripper_state(articulation, gripper_joints)` | Open/closed, force applied |

## Tier 4 — Geometry & Spatial Analysis (7 tools)

| # | Tool | Description |
|---|------|-------------|
| T4.1 | `raycast(origin, direction, max_distance)` | PhysX raycast, return hit info |
| T4.2 | `overlap_sphere(center, radius)` | Find prims within sphere |
| T4.3 | `overlap_box(center, half_extents, rotation)` | Find prims within box |
| T4.4 | `sweep_sphere(start, end, radius)` | Collision sweep |
| T4.5 | `compute_volume(prim_path)` | Mesh volume calculation |
| T4.6 | `compute_surface_area(prim_path)` | Mesh surface area |
| T4.7 | `compute_convex_hull(prim_path)` | Generate convex hull mesh |

## Tier 5 — OmniGraph (6 tools)

| # | Tool | Description |
|---|------|-------------|
| T5.1 | `list_graphs()` | All OmniGraph action graphs in scene |
| T5.2 | `inspect_graph(graph_path)` | Nodes, connections, attribute values |
| T5.3 | `add_node(graph_path, node_type, name)` | Add single node to existing graph |
| T5.4 | `connect_nodes(graph_path, src, dst)` | Wire two nodes |
| T5.5 | `set_graph_variable(graph_path, name, value)` | Set a graph variable |
| T5.6 | `delete_node(graph_path, node_name)` | Remove node from graph |

## Tier 6 — Lighting (5 tools)

| # | Tool | Description |
|---|------|-------------|
| T6.1 | `list_lights()` | All light prims in scene |
| T6.2 | `get_light_properties(light_path)` | Type, intensity, color, angle |
| T6.3 | `set_light_intensity(light_path, lux)` | Intensity control |
| T6.4 | `set_light_color(light_path, rgb)` | Color control |
| T6.5 | `create_hdri_skydome(hdri_path)` | Environment lighting |

## Tier 7 — Camera (5 tools)

| # | Tool | Description |
|---|------|-------------|
| T7.1 | `list_cameras()` | All camera prims |
| T7.2 | `get_camera_params(camera_path)` | FoV, focal, aperture, clipping |
| T7.3 | `set_camera_params(camera_path, params)` | Modify camera |
| T7.4 | `capture_camera_image(camera_path, resolution)` | Render from specific camera |
| T7.5 | `set_camera_look_at(camera_path, target)` | Orient camera at target |

## Tier 8 — Render Settings (5 tools)

| # | Tool | Description |
|---|------|-------------|
| T8.1 | `get_render_config()` | Current renderer, samples, resolution |
| T8.2 | `set_render_config(config)` | Switch renderer or quality |
| T8.3 | `set_render_resolution(width, height)` | Resolution change |
| T8.4 | `enable_post_process(effect, params)` | Bloom, tonemap, DoF |
| T8.5 | `set_environment_background(hdri_or_color)` | Background setup |

## Tier 9 — USD Layers & Variants (6 tools)

| # | Tool | Description |
|---|------|-------------|
| T9.1 | `list_layers()` | All sublayers on stage |
| T9.2 | `add_sublayer(layer_path)` | Attach new sublayer |
| T9.3 | `set_edit_target(layer_path)` | Change where edits go |
| T9.4 | `list_variant_sets(prim_path)` | Available variant sets |
| T9.5 | `list_variants(prim_path, variant_set)` | Available variants in set |
| T9.6 | `flatten_layers(output_path)` | Bake all layers to one |

## Tier 10 — Animation & Timeline (5 tools) [IMPLEMENTED]

5 tools wiring the LLM to the USD timeline + TimeSamples. **Distinct from
`sim_control`** (which steps PhysX): tier-10 controls the USD playback cursor
and authors keyframes that PhysX-decoupled clients (Replicator, SDG, replay)
consume.

| # | Tool | Type | Implementation |
|---|------|------|----------------|
| T10.1 | `get_timeline_state()` | DATA | omni.timeline + stage time-code metadata |
| T10.2 | `set_timeline_range(start, end, fps)` | CODE_GEN | Set{Start,End,TimeCodesPerSecond}TimeCode |
| T10.3 | `set_keyframe(prim_path, attr, time, value)` | CODE_GEN | attr.Set(value, TimeCode(t * fps)) |
| T10.4 | `list_keyframes(prim_path, attr)` | DATA | attr.GetTimeSamples() + attr.Get per sample |
| T10.5 | `play_animation(start, end)` | CODE_GEN | timeline.set_{start,end}_time + timeline.play() |

All schemas use the WHAT/WHEN/RETURNS/UNITS/CAVEATS template so the LLM can
disambiguate between `set_keyframe` (TimeSamples), `set_attribute` (default
value), and `sim_control` (physics step). Time arguments to T10.3 / T10.5 are
in **seconds** and converted to USD time codes internally via the stage's
`timeCodesPerSecond`.

## Tier 11 — SDG Annotation (5 tools) [IMPLEMENTED]

5 tools that wrap the Synthetic-Data-Generation (SDG) annotation surface
on top of `Semantics.SemanticsAPI`. Together with the existing tier-0
`set_semantic_label` (PR #59) they form the full SDG-labeling toolkit:
`set_semantic_label` (write one), this tier (read / discover / bulk-write /
clear / verify), and PR #23 `validate_annotations` (lint the annotation
*output files* on disk). The names below are namespaced so the three
surfaces don't clash.

| # | Tool | Type | Implementation |
|---|------|------|----------------|
| T11.1 | `list_semantic_classes()` | DATA | walk stage, collect `Semantics.SemanticsAPI.GetSemanticDataAttr().Get()` per labeled prim |
| T11.2 | `get_semantic_label(prim_path)` | DATA | enumerate `Semantics_*` API instances on a single prim |
| T11.3 | `remove_semantic_label(prim_path)` | CODE_GEN | `prim.RemoveAPI(Semantics.SemanticsAPI, instance)` for every Semantics_* instance |
| T11.4 | `assign_class_to_children(prim_path, class_name)` | CODE_GEN | recurse subtree, apply `Semantics.SemanticsAPI` to every Mesh / Imageable child with the same class |
| T11.5 | `validate_semantic_labels()` | DATA | report empty class strings, orphan SemanticsAPI applications, classes used on a single prim, and prims with conflicting labels |

**Why a separate name from PR #23 `validate_annotations`:** PR #23's
`validate_annotations` lints the SDG output FILES (cross-checks bbox bounds,
unique IDs, zero-area, missing classes inside `_labels.json` / Replicator
captures on disk). Tier-11 `validate_semantic_labels` lints the USD STAGE
itself — the upstream Semantics.SemanticsAPI annotations *before* SDG runs.
Both can fire in the same workflow: tier 11 catches bad source labels,
PR #23 catches downstream rendering / writer bugs.

**Why a separate tool from tier-0 `set_semantic_label`:** that tool labels
exactly one prim; `assign_class_to_children` walks a subtree (e.g. a tray
asset) and applies the same class to every Mesh inside, which is the common
case when bulk-labeling a robot's links or a referenced asset hierarchy.

All schemas use the WHAT / WHEN / RETURNS / CAVEATS rich-description
template so the LLM picks the right surface based on user intent.

## Tier 12 — Asset Management (5 tools)

| # | Tool | Description |
|---|------|-------------|
| T12.1 | `list_references(prim_path)` | All USD references on prim |
| T12.2 | `add_reference(prim_path, usd_url)` | Add asset reference |
| T12.3 | `list_payloads(prim_path)` | Deferred-loaded payloads |
| T12.4 | `load_payload(prim_path)` | Activate payload |
| T12.5 | `get_asset_info(prim_path)` | Origin file, version, hash |

## Tier 13 — IsaacLab RL Runtime (5 tools)

| # | Tool | Description |
|---|------|-------------|
| T13.1 | `get_env_observations(env_id)` | Current observation tensor |
| T13.2 | `get_env_rewards(env_id)` | Per-env reward breakdown |
| T13.3 | `get_env_termination_state(env_id)` | Success/timeout/crash |
| T13.4 | `pause_training(run_id)` | Pause without stopping |
| T13.5 | `checkpoint_training(run_id)` | Save checkpoint mid-run |

## Tier 14 — Bulk Operations (5 tools)

| # | Tool | Description |
|---|------|-------------|
| T14.1 | `bulk_set_attribute(prim_paths, attr, value)` | Apply to many prims atomically |
| T14.2 | `bulk_apply_schema(prim_paths, schema)` | Apply API to many prims |
| T14.3 | `select_by_criteria(criteria)` | Find prims matching query |
| T14.4 | `group_prims(prim_paths, group_name)` | Create Xform parent |
| T14.5 | `duplicate_prims(prim_paths, offset)` | Duplicate with offset |

## Tier 15 — Viewport & UI (4 tools)

| # | Tool | Description |
|---|------|-------------|
| T15.1 | `get_viewport_camera()` | Current active viewport camera |
| T15.2 | `get_selected_prims()` | User's current selection |
| T15.3 | `highlight_prim(prim_path, color, duration)` | Flash prim in viewport |
| T15.4 | `focus_viewport_on(prim_path)` | Frame prim in view |

## Tier 16 — Scene Persistence (4 tools)

| # | Tool | Description |
|---|------|-------------|
| T16.1 | `save_stage(path)` | Save current USD to disk |
| T16.2 | `open_stage(path)` | Load USD from disk |
| T16.3 | `export_stage(path, format)` | Export as FBX/OBJ/GLB |
| T16.4 | `list_opened_stages()` | Multi-stage management |

## Tier 17 — Extension Management (2 tools)

| # | Tool | Description |
|---|------|-------------|
| T17.1 | `list_extensions()` | Loaded Kit extensions + versions |
| T17.2 | `enable_extension(ext_id)` | Activate optional subsystems |

## Tier 18 — Audio (2 tools)

| # | Tool | Description |
|---|------|-------------|
| T18.1 | `create_audio_prim(position, audio_file)` | Spatial audio source |
| T18.2 | `set_audio_property(prim_path, prop, value)` | Volume, pitch, attenuation |

---

## Build Order Recommendation

1. **Tier 0 (12 tools)** — Foundation. Build first.
2. **Tier 1 (10 tools)** — USD inspection. Enables verify-before-modify workflows.
3. **Tier 2+3 (19 tools)** — Physics read symmetry. Enables debugging workflows.
4. **Tier 4 (7 tools)** — Spatial analysis. Enables spatial reasoning.
5. **Tier 5 (6 tools)** — OmniGraph manipulation. Enables incremental graph edits.
6. **Tier 6+7+8 (15 tools)** — Visual control. Enables demo/SDG workflows.
7. **Tier 14 (5 tools)** — Bulk ops. Required for large scenes (enterprise).
8. **Remaining tiers** — As user demand dictates.

Total: 100 tools. Most are 1-5 lines of USD/PhysX API. Implementation speed: ~10-15 tools per day with focused work.

---

## Test Strategy

Each tool: L0 unit test with mock stage, L3 integration test with real Kit.

**TestAllAtomicToolsCovered** class enforces every tool in `tool_schemas.py` has a test vector.
