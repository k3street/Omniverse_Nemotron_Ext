"""
tool_descriptions_polish_b3.py
------------------------------
Batch 3 of rich tool descriptions for LLM tool selection accuracy.

Each entry uses the format: WHAT / WHEN / RETURNS / CAVEATS

Why: short descriptions make LLMs pick the wrong tool, miss tools that exist,
or misinterpret outputs. Rich descriptions reduce false-positive "missing
feature" reports during Phase 12 QA.

Apply at MCP server startup or import-time:

    from tool_descriptions_polish_b3 import POLISH_B3
    for tool in ISAAC_SIM_TOOLS:
        name = tool["function"]["name"]
        if name in POLISH_B3:
            tool["function"]["description"] = POLISH_B3[name]
"""

POLISH_B3 = {
    # ── Singularity / Performance Diagnostics ──────────────────────────────
    "check_singularity": (
        "WHAT: Compute the Jacobian condition number for a target end-effector pose to detect proximity to a singularity. "
        "WHEN: before move_to_pose to a risky pose, validating IK targets near workspace boundary, debugging erratic motion near goal. "
        "RETURNS: {condition_number, classification: 'safe'|'warning'|'danger'}. Thresholds: <50 safe, 50-100 warning, >=100 danger. "
        "CAVEATS: requires articulation_path with valid robot_description (call generate_robot_description first). "
        "target_orientation quaternion [w,x,y,z] optional — without it only position-Jacobian is checked. "
        "For full kinematic feasibility use preflight_check."
    ),
    "diagnose_performance": (
        "WHAT: Read PhysX scene statistics, per-zone timing breakdowns, and GPU/VRAM usage, then return actionable issues ranked by severity. "
        "WHEN: 'why is my sim slow', low FPS complaints, deciding what to optimize first, profiling before tuning. "
        "RETURNS: {issues: [{severity, category, description, suggestion}], stats: {fps, physics_ms, render_ms, gpu_pct, vram_mb}}. "
        "CAVEATS: requires sim playing for physics_ms timing. "
        "For lightweight FPS read use get_debug_info instead. "
        "For automated fix patches (not just analysis) use optimize_scene. "
        "For per-mesh triangle counts use find_heavy_prims."
    ),
    "find_heavy_prims": (
        "WHAT: Walk the stage and list all mesh prims with triangle count above threshold_triangles, sorted descending. "
        "WHEN: hunting the source of low FPS, deciding which meshes to simplify or convert to convex hulls, auditing imported assets. "
        "RETURNS: list of {prim_path, triangle_count, collision_approximation_type}, sorted by triangle_count desc. "
        "CAVEATS: default threshold is 10000 triangles — lower it for sensitivity, raise for big scenes. "
        "Reports visual mesh tris, not collision tris (which may differ). "
        "For automated simplification use simplify_collision or optimize_collision per prim, or optimize_scene for bulk."
    ),

    # ── Material / Physics Database ─────────────────────────────────────────
    "lookup_material": (
        "WHAT: Look up physics material properties (static/dynamic friction, restitution, density) for a contact pair from a 16-material, 20-pair database. "
        "WHEN: choosing realistic friction for a steel-on-rubber wheel, validating pre-set values, before apply_physics_material. "
        "RETURNS: {static_friction, dynamic_friction, restitution, density_a_kg_m3, density_b_kg_m3, source: 'measured'|'computed'}. "
        "CAVEATS: covers steel, rubber, aluminum, plastic_abs, concrete, glass, wood, cardboard, etc. "
        "If pair has no measured data, returns average-combine values (PhysX default behavior). "
        "Returns numbers only — does NOT modify the scene; pair with apply_physics_material to apply."
    ),
    "apply_physics_material": (
        "WHAT: Create a PhysicsMaterialAPI on a prim with friction/restitution from the material database, also applying CollisionAPI if missing. "
        "WHEN: making a rubber-tired wheel grip a concrete floor, configuring a steel gripper on a glass jar, after lookup_material. "
        "RETURNS: code patch for approval; creates a Physics Material prim and binds it to prim_path. "
        "CAVEATS: this is the PHYSICS material (friction/restitution), separate from VISUAL material (assign_material). "
        "Material name must exist in the 16-material database — see lookup_material for the list. "
        "Does NOT change visual appearance — for that use create_material + assign_material."
    ),

    # ── Scene Change Tracking ───────────────────────────────────────────────
    "scene_diff": (
        "WHAT: Show what changed in the scene since last_save, last_snapshot, or between two named snapshots — added/removed/modified prims with attribute-level detail. "
        "WHEN: 'what changed', 'show diff', undoable-history audit, debugging 'why does this not work anymore'. "
        "RETURNS: {added: [...], removed: [...], modified: [{path, attrs_changed: [...]}], summary_text}. "
        "CAVEATS: comparing against last_snapshot requires watch_changes(action='start') to have been called earlier. "
        "Provide either `since` shortcut OR `snapshot_a`+`snapshot_b` — not both. "
        "For live monitoring (no diff yet) use watch_changes."
    ),
    "watch_changes": (
        "WHAT: Start/stop/query live USD change tracking via Tf.Notice, accumulating structural and attribute mutations to memory. "
        "WHEN: starting an edit session you want to undo selectively, recording a workflow for replay, before scene_diff(since='last_snapshot'). "
        "RETURNS: {action: str, tracking: bool, change_count: int} — for action='query' also returns the accumulated changes. "
        "CAVEATS: action='start' enables tracking (low-overhead listener); 'stop' disables and discards; 'query' reads without resetting. "
        "Must call 'start' before scene_diff(since='last_snapshot') will work. "
        "Tracking lives in process memory only — restarts clear it."
    ),

    # ── Auto-Optimization ───────────────────────────────────────────────────
    "optimize_scene": (
        "WHAT: Scan the scene for bottlenecks (heavy collisions, over-iterated articulations, CPU PhysX, unnecessary CCD, never-moving joints) and produce optimization patches. "
        "WHEN: 'optimize my scene', 'improve FPS', 'simplify physics', after diagnose_performance flagged issues. "
        "RETURNS: code patch (mode='conservative'/'aggressive') OR analysis-only report (mode='analyze'), with estimated FPS impact toward target_fps. "
        "CAVEATS: 'analyze' makes NO changes — safe anytime. "
        "'conservative' applies safe ops; 'aggressive' includes lossy mesh simplification. "
        "For per-prim use optimize_collision; for archetype defaults use suggest_physics_settings."
    ),
    "suggest_physics_settings": (
        "WHAT: Recommend optimal physics settings (solver type, iteration counts, GPU on/off, CCD, timestep) for a scene archetype WITHOUT modifying anything. "
        "WHEN: starting a new scene of a known type, planning compute budget, sanity-checking current PhysX scene. "
        "RETURNS: {solver_type, position_iterations, velocity_iterations, gpu_enabled, ccd_enabled, dt, rationale}. "
        "CAVEATS: scene_type must be one of: 'rl_training' (1024 envs/fast), 'manipulation' (precision grasp), 'mobile_robot' (navigation), 'digital_twin' (visualization). "
        "Output is advisory — apply via set_physics_params manually. "
        "For automated optimization patches use optimize_scene."
    ),

    # ── Onboarding / UX Helpers ─────────────────────────────────────────────
    "scene_aware_starter_prompts": (
        "WHAT: Generate 3 contextual chat starter prompts tailored to current scene state (empty / robot+objects / mobile robot / no physics / etc.). "
        "WHEN: chat panel just opened, user looks unsure what to ask, periodic refresh after large scene changes. "
        "RETURNS: {prompts: [str, str, str], detected_state: str}. "
        "CAVEATS: takes no parameters — auto-detects via scene_summary. "
        "Output is suggestion text only — does NOT execute anything. "
        "Refresh after major scene changes (import_robot, build_scene_from_blueprint) for relevance."
    ),
    "hardware_compatibility_check": (
        "WHAT: Probe GPU model + VRAM, Isaac Sim version, Python version, and LLM connectivity, then return a structured pass/warn/info report. "
        "WHEN: first run on a new machine, debugging 'simulation won't start', verifying environment before a long training job, support troubleshooting. "
        "RETURNS: {gpu: {name, vram_gb}, isaac_sim_version, python_version, llm_status, checks: [{name, status, detail}]}. "
        "CAVEATS: takes no parameters. "
        "Reports static system info — does NOT measure runtime performance (use diagnose_performance for that). "
        "LLM connectivity check pings the configured backend; failure means chat won't work."
    ),
    "slash_command_discovery": (
        "WHAT: Return the list of available slash commands filtered by current scene state — hides commands not applicable (e.g., /workspace if no robot present). "
        "WHEN: user types '/' in the chat input, building a context-aware autocomplete UI. "
        "RETURNS: list of {command, description, category} relevant to current scene. "
        "CAVEATS: scene_has_robot/scene_has_physics auto-detected via scene_summary if omitted. "
        "Pass them explicitly to override detection (faster on big stages). "
        "Returns commands only — does NOT execute them."
    ),
    "console_error_autodetect": (
        "WHAT: Check for new ERROR-severity console messages since since_timestamp (Unix seconds) to proactively offer diagnosis. "
        "WHEN: after a tool execution to detect silent failures, between chat turns to surface late errors, polling for crash hints. "
        "RETURNS: {new_error_count: int, errors: [{timestamp, source, message}], summary}. "
        "CAVEATS: ERRORS only — warnings are filtered to avoid spam. "
        "Default since_timestamp=0 returns ALL errors (use last chat turn's timestamp for incremental check). "
        "For full log including warnings use get_console_errors. "
        "For diagnosis with fix suggestions use diagnose_physics_error."
    ),
    "post_action_suggestions": (
        "WHAT: Get 2-3 context-aware follow-up prompts based on what tool just executed and its result (e.g., after import_robot suggest 'add ground plane' / 'set anchor'). "
        "WHEN: immediately after a successful tool execution to drive workflow continuation. "
        "RETURNS: {suggestions: [{prompt, reason}]}. "
        "CAVEATS: completed_tool is required; tool_args/tool_result optional but improve relevance. "
        "Suggestions are heuristic — not guaranteed to match user intent. "
        "Use scene_aware_starter_prompts at session start instead (no prior tool context)."
    ),

    # ── OmniGraph ───────────────────────────────────────────────────────────
    "explain_graph": (
        "WHAT: Read all nodes/connections/attribute values of an OmniGraph and return a plain-language description of what it does (e.g., 'ticks every physics frame, reads joint state, publishes to ROS2'). "
        "WHEN: 'what does this graph do', auditing an unfamiliar scene's automation, before debug_graph or modifying. "
        "RETURNS: {graph_path, summary_text, nodes: [...], execution_pattern, ros2_topics: [...]}. "
        "CAVEATS: graph_path must point to an OmniGraph prim (typically '/World/ActionGraph'). "
        "Read-only — does NOT modify the graph. "
        "For creation use create_graph; for repair use debug_graph."
    ),

    # ── Interactive Teaching ────────────────────────────────────────────────
    "start_teaching_mode": (
        "WHAT: Enable interactive robot teaching: drag_target (RMPflow follows ghost), keyboard (WASD/QE), spacemouse (3Dconnexion), or gravity_comp (zero-PD backdrivable arm). "
        "WHEN: collecting demo waypoints for imitation learning, prototyping a motion sequence by hand, kinesthetic teaching workflow. "
        "RETURNS: code patch for approval; creates input handler + (for drag_target) ghost target prim. "
        "CAVEATS: drag_target/keyboard need RMPflow config — only ~9 robots supported (franka, ur10, ur5e, cobotta default). "
        "spacemouse requires 3Dconnexion driver. "
        "gravity_comp requires sim playing and may drift without external support. "
        "After moving the robot, capture poses with record_waypoints."
    ),
    "record_waypoints": (
        "WHAT: Record current articulation joint state as a waypoint, OR save accumulated waypoints to disk in HDF5 (robomimic), JSON (motion planning), or USD TimeSamples format. "
        "WHEN: during start_teaching_mode after moving the robot to a target pose, building demo datasets, capturing replayable trajectories. "
        "RETURNS: code patch for approval; writes file at output_path. "
        "CAVEATS: format auto-detected from output_path extension if `format` omitted. "
        "HDF5 follows robomimic schema (consumable by Phase 7G fine-tuning). "
        "JSON for replay_trajectory. "
        "USD TimeSamples for in-stage scrubbing. "
        "Requires sim playing to read joint state."
    ),
    "replay_trajectory": (
        "WHAT: Play back a previously recorded trajectory file (.json or .hdf5) by driving the articulation through its waypoints at adjustable speed. "
        "WHEN: validating a recorded demo, demoing a learned policy, deterministic regression of a known motion. "
        "RETURNS: code patch for approval; runs playback on the next sim step. "
        "CAVEATS: speed multiplier 0.1-4.0 (default 1.0). "
        "Requires sim playing and articulation_path matching the recording's robot. "
        "For smooth interpolation between sparse waypoints use interpolate_trajectory first. "
        "Reads file from disk at execute time — file must still exist."
    ),
    "interpolate_trajectory": (
        "WHAT: Generate a dense smooth trajectory from sparse joint-space waypoints using linear, cubic spline, or RMPflow (collision-aware) interpolation. "
        "WHEN: smoothing recorded teach-pendant waypoints, creating planning seeds, sub-stepping for accurate playback. "
        "RETURNS: {trajectory: [...], num_total_steps: int} or saves to output_path if provided. "
        "CAVEATS: 'rmpflow' method requires a supported robot_type ('franka' default) with RMPflow config. "
        "num_steps (default 50) is between EACH consecutive pair, total = num_steps × (len(waypoints)-1). "
        "Linear is fast but jerky at waypoint corners; cubic smooths but may overshoot joint limits. "
        "For replay use replay_trajectory."
    ),

    # ── Preflight ───────────────────────────────────────────────────────────
    "preflight_check": (
        "WHAT: Run 23 scene-validation checks across 4 tiers — Tier 1 (crash preventers), Tier 2 (correctness), Tier 3 (RL training), Tier 4 (ROS2/OmniGraph). "
        "WHEN: before pressing Play, before launching a long training run, after major edits, before handoff. "
        "RETURNS: {issues: [{severity, tier, prim, description, auto_fix_suggestion}], summary, pass_count, fail_count}. "
        "CAVEATS: scope='all' runs all 23 (~few seconds); pick a tier for targeted check. "
        "articulation_path narrows checks to one robot. "
        "Reports issues but doesn't auto-fix — use apply_robot_fix_profile. "
        "For shallow collision-only use check_collisions."
    ),

    # ── RL Training Diagnostics ─────────────────────────────────────────────
    "diagnose_training": (
        "WHAT: Read TensorBoard scalars + RSL-RL perf logs from run_dir, then check action collapse, entropy collapse, reward hacking, bimodal success, NaNs (with PD stability), throughput. "
        "WHEN: training appears stuck/diverged/degenerate, success curve flat, reward exploding. "
        "RETURNS: {checks: [{name, status, evidence, suggestion}], summary, severity_counts}. "
        "CAVEATS: run_dir must contain TensorBoard event files (RSL-RL perf logs optional). "
        "physics_dt (default 1/120) used by NaN/PD stability check — set to actual training dt. "
        "Read-only. Pre-training? use review_reward. Throughput-only? use profile_training_throughput."
    ),
    "review_reward": (
        "WHAT: Static analysis of RL reward source code BEFORE training — checks sparse-reward desert, dominant-term swamping, alive-bonus hacking, scale, success-criterion misalignment. "
        "WHEN: after authoring a new reward, before first training run, code review of others' RewTerm definitions. "
        "RETURNS: {checks: [{name, verdict, fix_suggestion}], overall_risk}. "
        "CAVEATS: reward_code is the Python source STRING — pass file contents, not a path. "
        "has_fall_termination=true changes alive-bonus verdict. "
        "max_possible_reward inferred from code if omitted. "
        "Static — for runtime diagnosis use diagnose_training."
    ),
    "profile_training_throughput": (
        "WHAT: Analyze RSL-RL Perf/collection_time, learning_time, total_fps logs to classify a run as sim-bound vs train-bound and suggest concrete fixes. "
        "WHEN: 'training is slow', wondering whether to reduce num_envs or shrink the network, deciding when to switch to TiledCamera. "
        "RETURNS: {classification: 'sim_bound'|'train_bound'|'balanced', fps, suggestions: [...]}. "
        "CAVEATS: requires RSL-RL TensorBoard event files in run_dir. "
        "TiledCamera fix is vision-policy specific (~10x speedup). "
        "Sim-bound suggestions: reduce num_envs, simplify collision meshes. "
        "Train-bound: smaller net, larger batch, fewer PPO epochs. "
        "For full diagnostics use diagnose_training."
    ),
    "generate_eval_harness": (
        "WHAT: Generate a reproducible Python eval script that runs num_episodes deterministic rollouts on a Gym task, recording reward/success/length per episode and saving JSON results (optionally with video). "
        "WHEN: benchmarking a trained policy, regression-testing across checkpoints, producing eval numbers for a paper/report. "
        "RETURNS: code patch for approval; produces eval script at output_dir/eval_<task>.py. "
        "CAVEATS: task_name is the Gym ID (e.g. 'Isaac-Reach-Franka-v0'). "
        "checkpoint_path optional — without it evaluates the env-default policy. "
        "record_video=true uses gym.wrappers.RecordVideo (slower, larger output). "
        "max_steps_per_episode=1000 caps each episode."
    ),

    # ── Teleop Quality ──────────────────────────────────────────────────────
    "check_teleop_hardware": (
        "WHAT: Check whether a teleop input device is supported, report its transport (WebXR/CloudXR/USB-HID), latency budget, and known limitations. "
        "WHEN: before start_teleop_session to decide if device will work, planning a teleop session, debugging 'device not detected'. "
        "RETURNS: {device, supported: bool, transport, latency_budget_ms, limitations: [...]}. "
        "CAVEATS: device must be one of: 'quest_3' (WebXR/Wi-Fi), 'vision_pro' (CloudXR native), 'spacemouse' (USB-HID), 'keyboard' (USB-HID). "
        "Reports static capabilities — does NOT actually probe a connected device. "
        "For hardware system check (GPU/Isaac Sim) use hardware_compatibility_check."
    ),
    "validate_teleop_demo": (
        "WHAT: Validate an HDF5 teleop demo file against the Phase 7C robomimic schema — checks action shape, obs keys, episode length, NaN/Inf in actions. "
        "WHEN: before feeding demos to Phase 7G fine-tuning (avoids wasting GPU on corrupt episodes), QA after a teleop session, sanity-check after disk transfer. "
        "RETURNS: {valid: bool, errors: [...], warnings: [...], episode_count, total_steps}. "
        "CAVEATS: hdf5_path must be absolute. "
        "Schema is robomimic-style — files from other recorders may need conversion. "
        "Read-only — does NOT modify the file. "
        "For human-readable summary use summarize_teleop_session."
    ),
    "export_teleop_mapping": (
        "WHAT: Generate a Python script that writes a teleop mapping YAML to workspace/teleop_mappings/<session_name>.yaml in IsaacTeleop/dex-retargeting config shape. "
        "WHEN: capturing a tested mapping for version control, sharing a known-good config, regenerating after editing joint_map. "
        "RETURNS: code patch for approval; produces YAML at the documented path. "
        "CAVEATS: joint_map entries follow {'name', 'source', 'gain', 'limit_rad': [lo,hi]}. "
        "device must be one of: 'quest_3', 'vision_pro', 'spacemouse', 'keyboard'. "
        "robot defaults to 'franka_panda'. "
        "YAML is committable/diffable and can be re-fed into configure_teleop_mapping."
    ),
    "generate_teleop_watchdog_script": (
        "WHAT: Generate a Python script that arms a Kit-side teleop watchdog — if no control msg arrives on socket_path within timeout_ms, hold last command for hold_time_ms, then zero all joint velocity targets. "
        "WHEN: adding safety to start_teleop_session, deploying teleop in production, network-hostile environments where teleop link may drop. "
        "RETURNS: code patch for approval; arms watchdog on robot_path. "
        "CAVEATS: defaults: timeout_ms=500, hold_time_ms=2000, socket_path='/ws/teleop'. "
        "Watchdog only zeros velocity — for full E-stop combine with sim_control(stop). "
        "Requires the WebSocket teleop bridge to be running."
    ),
    "summarize_teleop_session": (
        "WHAT: Read an HDF5 teleop session and report demo count, total duration, per-joint velocity and range statistics. "
        "WHEN: human-readable 'how much data did I record', deciding whether dataset is large enough for Phase 7G fine-tuning, comparing sessions. "
        "RETURNS: {demo_count, duration_s, per_joint: {name: {velocity_max, range_rad}}, summary_text}. "
        "CAVEATS: hdf5_path must be absolute. "
        "fps defaults to file's 'fps' attr, then 30. "
        "For schema validation (not stats) use validate_teleop_demo."
    ),

    # ── Synthetic Data Generation (SDG) ─────────────────────────────────────
    "scatter_on_surface": (
        "WHAT: Scatter source prims across the surface of an arbitrary target mesh (organic/curved geometry, e.g. fruit on a branch) with optional Poisson-disk spacing, normal alignment, penetration rejection. "
        "WHEN: placing objects on non-planar surfaces, decorating organic meshes for SDG, diverse training placements. "
        "RETURNS: code patch for approval; spawns up to `count` instances of source_prims on target_mesh. "
        "CAVEATS: target_mesh must be a USD Mesh prim (or filesystem path). "
        "spacing in meters (Poisson-disk min distance). "
        "normal_align=true (default) orients Y-axis to surface normal. "
        "penetration_check=false by default — may overlap existing geo."
    ),
    "configure_differential_sdg": (
        "WHAT: Configure partial re-render: freeze static elements (geo, materials) and only re-randomize/re-render dynamic ones per frame — yields 3-10x throughput vs full per-frame eval. "
        "WHEN: SDG pipelines where most of scene is static (walls/floor) and only a few elements vary (lights/cameras/products). "
        "RETURNS: code patch for approval; modifies the active SDG pipeline configuration. "
        "CAVEATS: static_elements lose all randomization. "
        "randomize defaults to ['rotation','color']; pick from ['rotation','position','color','intensity','scale']. "
        "Verify speedup with benchmark_sdg before/after."
    ),
    "configure_coco_yolo_writer": (
        "WHAT: Configure a COCO/YOLO writer with multi-camera support — globally unique annotation IDs, per-camera image-ID namespacing, single merged category map. "
        "WHEN: SDG datasets with >1 camera, training detection models needing canonical class IDs across views, exporting to YOLO instead of COCO JSON. "
        "RETURNS: code patch for approval; registers writer on the listed cameras. "
        "CAVEATS: format='coco' (default) writes JSON+images, format='yolo' writes txt+images. "
        "id_offset=1000000 is per-camera image-ID stride — increase if one camera produces >1M frames. "
        "categories list defines class index order — keep stable across runs."
    ),
    "benchmark_sdg": (
        "WHAT: Run a headless SDG throughput benchmark (FPS, peak VRAM, disk I/O) and classify the bottleneck (CPU randomization vs GPU render vs disk write) against preset baselines. "
        "WHEN: before kicking off a multi-hour SDG job, verifying configure_differential_sdg actually sped things up, sizing storage. "
        "RETURNS: {fps, peak_vram_mb, disk_throughput_mbps, bottleneck, vs_baseline_pct}. "
        "CAVEATS: defaults — num_frames=100, annotators=['rgb'], resolution=[1280,720]. "
        "annotators choices include 'rgb','depth','bounding_box_2d', etc. "
        "Larger resolutions stress GPU; more annotators stress disk. "
        "pipeline_id is the last configured pipeline if omitted."
    ),
    "enforce_class_balance": (
        "WHAT: Reject SDG frames missing required class occurrences and re-randomize up to max_retries; optionally write the partial frame after exhaustion. "
        "WHEN: training datasets where each class must appear N times per frame (e.g. 'every frame must contain ≥1 visible fruit'), avoiding occluded-class frame waste. "
        "RETURNS: code patch for approval; injects a pre-write filter into the SDG pipeline. "
        "CAVEATS: defaults — min_per_class=1, max_retries=5, write_partial_on_fail=true. "
        "classes auto-derived from pipeline's semantic labels if omitted. "
        "Each retry costs a full re-render — high min_per_class on hard scenes can halve throughput. "
        "Combine with benchmark_sdg to measure cost."
    ),

    # ── Enterprise Scale ────────────────────────────────────────────────────
    "build_stage_index": (
        "WHAT: Build a lightweight in-memory metadata index of the USD stage (prim path, type, applied schemas, physics flags) for fast retrieval at enterprise scale. "
        "WHEN: at session start on stages with >5K prims, before query_stage_index, after a major scene-load to refresh the cache. "
        "RETURNS: {indexed_prim_count, scope, max_prims, build_time_ms}. "
        "CAVEATS: prim_scope defaults to '/World'; narrow it to a subtree to skip irrelevant prims. "
        "max_prims=50000 safety cap — raise for huge stages but watch memory. "
        "Replaces full-stage context-stuffing into the LLM. "
        "Pair with query_stage_index for retrieval."
    ),
    "query_stage_index": (
        "WHAT: Query the in-memory stage index by keywords against prim paths/types/schemas, returning 50-200 matches instead of the full stage. "
        "WHEN: 'find all cameras', 'where are the lights', selected-prim neighborhood lookup, keeping LLM context small at 50K+ prims. "
        "RETURNS: list of {path, type, schemas, score} sorted by relevance, up to max_results. "
        "CAVEATS: requires build_stage_index to have run first. "
        "max_results=100 default; bump for exhaustive search. "
        "selected_prim adds spatial/hierarchy neighbors to results. "
        "For unfiltered traversal use list_all_prims (slower at scale). "
        "Keyword match is substring — for regex use find_prims_by_name."
    ),
    "save_delta_snapshot": (
        "WHAT: Save only the dirty USD layer deltas (KB-MB) instead of a full snapshot (800MB-2GB) — applies on top of a base snapshot. "
        "WHEN: enterprise stages with 50K+ prims, frequent checkpointing during long edit sessions, when full snapshots are too slow/large. "
        "RETURNS: code patch for approval; writes delta to internal snapshot store under snapshot_id. "
        "CAVEATS: base_snapshot_id defaults to the snapshot manager's last full snapshot. "
        "Restore via restore_delta_snapshot — REQUIRES the base snapshot still to exist. "
        "For full-stage snapshots use save_snapshot. "
        "Delta only captures dirty layers — inactive layers won't restore."
    ),
    "restore_delta_snapshot": (
        "WHAT: Restore a delta snapshot by replaying its saved dirty-layer strings on top of the base snapshot. "
        "WHEN: rolling back to a checkpoint saved via save_delta_snapshot, recovering after a bad edit, undoing a long sequence. "
        "RETURNS: code patch for approval; loads base + replays deltas. "
        "CAVEATS: snapshot_id must reference an existing delta snapshot AND its base must still exist. "
        "Replays — does NOT merge; conflicting current-stage edits are overwritten. "
        "For full-stage restore use restore_snapshot."
    ),
    "batch_delete_prims": (
        "WHAT: Delete many prims atomically via Sdf.BatchNamespaceEdit — single Hydra rebuild instead of N individual omni.kit.commands calls. "
        "WHEN: removing >10 prims at once, cleaning up after a failed import, mass-deleting a category, scene reset. "
        "RETURNS: code patch for approval; removes all listed prims in one transaction. "
        "CAVEATS: undoable with Ctrl+Z as a single action. "
        "All paths must exist; missing paths fail the whole batch (no partial success). "
        "For single-prim use delete_prim. "
        "References to deleted prims break — pre-check with list_relationships."
    ),
    "batch_set_attributes": (
        "WHAT: Set many prim attributes inside a single Sdf.ChangeBlock so only one stage notification fires — much faster than per-attribute calls at scale. "
        "WHEN: bulk parameter sweep over 10+ prims, applying randomization, mass-config of physics properties, scripted recoloring. "
        "RETURNS: code patch for approval; applies all changes atomically. "
        "CAVEATS: each change entry needs prim_path, attr_name, value. "
        "Attribute must already exist (or be auto-creatable from value type). "
        "All-or-nothing — one bad change fails the batch. "
        "For single-attribute use set_attribute. "
        "For transform ops prefer teleport_prim (handles xformOpOrder)."
    ),
    "activate_area": (
        "WHAT: Selectively activate a robot cell — deactivates every prim outside prim_scope (SetActive=false) so it's excluded from physics and rendering. "
        "WHEN: working on one cell of a 50K+ prim factory stage, focusing physics compute on the area being tested, isolating issues. "
        "RETURNS: code patch for approval; toggles activation on siblings. "
        "CAVEATS: deactivate_siblings_only=true (default) preserves /World and pseudo-root. "
        "Set false to deactivate the entire rest of stage (more aggressive). "
        "Reversible — re-activate via set_active_state(path, true) on each. "
        "Deactivated prims still exist on disk — for true removal use batch_delete_prims."
    ),
    "queue_write_locked_patch": (
        "WHAT: Queue Python code behind the stage write-lock so it serializes cleanly with concurrent OPC-UA / digital-twin syncs writing the same layer stack at 30 Hz. "
        "WHEN: live digital-twin scenes where external systems write USD concurrently, preventing HNSW/USD layer corruption from two writers. "
        "RETURNS: code patch for approval; enqueues code for serialized execution. "
        "CAVEATS: code is a Python source string executed under the lock — keep it short to avoid blocking external sync. "
        "priority=0 default; higher runs sooner. "
        "description is required and shows in the queue UI. "
        "For non-concurrent scenes use direct edits — the lock is overhead."
    ),

    # ── ROS2 Bridge / Nav2 ──────────────────────────────────────────────────
    "setup_ros2_bridge": (
        "WHAT: One-shot configure a complete ROS2 bridge for a known robot+stack profile — builds the OmniGraph with all required pubs/subs, QoS, and a ROS2 clock node. "
        "WHEN: wiring Isaac Sim to Nav2 / MoveIt2 quickly without configuring nodes individually, standing up a known reference stack. "
        "RETURNS: code patch for approval; creates OmniGraph at graph_path. "
        "CAVEATS: profile must be one of: 'ur10e_moveit2', 'jetbot_nav2', 'franka_moveit2', 'amr_full' — all others need create_graph manually. "
        "Default graph_path '/World/ROS2_Bridge'. "
        "robot_path must be the articulation root. "
        "For custom topologies use create_graph with explicit template; for debugging use debug_graph."
    ),
    "export_nav2_map": (
        "WHAT: Generate a Nav2 map_server-compatible map.yaml + map.pgm pair from the current scene via Phase 8A.3 occupancy generation. "
        "WHEN: bringing up Nav2 against an Isaac Sim scene, exporting a digital-twin floorplan, regression testing nav stacks. "
        "RETURNS: code patch for approval; writes <output_path>.pgm and <output_path>.yaml. "
        "CAVEATS: output_path is a stem (no extension). "
        "Defaults: resolution=0.05 m/px (Nav2 standard), origin=[0,0,0], dimensions=[10,10] m, height_range=[0.05,0.5] m, occupied_thresh=0.65, free_thresh=0.196. "
        "Cells with geometry between height_range Z values are occupied. "
        "Larger maps × higher res = bigger PGMs."
    ),
    "replay_rosbag": (
        "WHAT: Deterministically replay a recorded rosbag (rosbag2 dir or .db3) through the live sim — publishes bag's cmd_vel into sim, sim produces its own odom/TF for sim-vs-real comparison. "
        "WHEN: validating sim matches real-robot behavior, regression testing nav stack against recorded runs, debugging where sim and real diverge. "
        "RETURNS: code patch for approval; starts replay on the next sim step. "
        "CAVEATS: sync_mode='sim_time' (default) is step-locked to /clock — use for comparisons. "
        "'real_time' is wall-clock — use for visualization only. "
        "topics whitelist defaults to ['/cmd_vel'] — add more to replay extra streams. "
        "rate=1.0 default; higher = faster replay (loses sync determinism)."
    ),
    "check_tf_health": (
        "WHAT: Diagnose ROS2 TF tree — flags missing expected frames, stale transforms (>max_age_seconds old), extrapolation-into-future risk, missing static_transforms, orphan frames not connected to root. "
        "WHEN: Nav2/MoveIt2 misbehaving and TF suspected, after setup_ros2_bridge to verify, debugging 'frame X not found' errors. "
        "RETURNS: {missing: [...], stale: [{frame, age_s}], orphans: [...], extrapolation_risk: bool, summary}. "
        "CAVEATS: defaults — expected_frames=['base_link','odom','map'], max_age_seconds=1.0, root_frame='map'. "
        "For Nav2 add scan/laser frames; for MoveIt2 add tool frames. "
        "Requires rosbridge active. "
        "For node-level health use ros2_get_node_details / ros2_list_nodes."
    ),

    # ── Domain Randomization ────────────────────────────────────────────────
    "configure_correlated_dr": (
        "WHAT: Correlated domain randomization where related params (mass+friction, damping+temperature) are sampled jointly via a Gaussian copula instead of independently — preserves physical plausibility. "
        "WHEN: sim2real where independent DR breaks correlations the policy depends on, training robust policies that respect parameter coupling. "
        "RETURNS: code patch for approval; generates a Replicator/IsaacLab-compatible randomizer. "
        "CAVEATS: parameter_groups: list of {'params':[...], 'ranges':{...}, 'correlation':float, 'method':'copula'|'linear'}. "
        "method default 'copula'; 'linear' for affine couplings only. "
        "target_path='/World' default. "
        "For sensible defaults use suggest_dr_ranges or apply_dr_preset."
    ),
}


def apply_polish(tools_list):
    """Apply polished descriptions to a list of tool dicts in-place.

    Args:
        tools_list: ISAAC_SIM_TOOLS list (each entry has 'function.name' and 'function.description')

    Returns:
        Number of descriptions replaced.
    """
    count = 0
    for tool in tools_list:
        try:
            name = tool["function"]["name"]
        except (KeyError, TypeError):
            continue
        if name in POLISH_B3:
            tool["function"]["description"] = POLISH_B3[name]
            count += 1
    return count
