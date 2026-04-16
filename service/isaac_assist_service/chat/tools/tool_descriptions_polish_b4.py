"""
tool_descriptions_polish_b4.py
------------------------------
Rich tool descriptions (Batch 4) for LLM tool selection accuracy.

Each entry uses the format: WHAT / WHEN / RETURNS / CAVEATS

Why: short descriptions make LLMs pick the wrong tool, miss tools that exist,
or misinterpret outputs. Rich descriptions reduce false-positive "missing
feature" reports during Phase 12 QA.

Apply at MCP server startup or import-time:

    from tool_descriptions_polish_b4 import POLISH_B4
    for tool in ISAAC_SIM_TOOLS:
        name = tool["function"]["name"]
        if name in POLISH_B4:
            tool["function"]["description"] = POLISH_B4[name]
"""

POLISH_B4 = {
    # ── Domain Randomization (4 tools) ──────────────────────────────────────
    "suggest_dr_ranges": (
        "WHAT: Recommend domain-randomization ranges for object mass, friction, joint damping, gravity, action latency, "
        "and lighting based on task type, robot, and (optionally) real sensor logs. "
        "WHEN: starting a new sim2real task and need a sane DR starting point, before configure_correlated_dr, "
        "when unsure how wide to randomize. "
        "RETURNS: dict of param -> {min, max, distribution} suggestions plus rationale per parameter. "
        "CAVEATS: heuristic-based when real_data_path is omitted — pass a CSV/JSON of measurements for empirical variance. "
        "Use apply_dr_preset for known scenarios (warehouse, cleanroom). "
        "These are SUGGESTIONS — feed into configure_correlated_dr to actually apply."
    ),
    "apply_dr_preset": (
        "WHAT: Look up a pre-configured domain-randomization preset (indoor_industrial, outdoor_daylight, warehouse, "
        "cleanroom, aggressive_sim2real) and return the full parameter dict. "
        "WHEN: user names a known scenario, want a known-good starting point, no real data available for suggest_dr_ranges. "
        "RETURNS: full parameter dict (lighting, friction, mass, latency ranges) ready to feed into configure_correlated_dr "
        "or any IsaacLab event manager. "
        "CAVEATS: only 5 presets exist — for custom tasks use suggest_dr_ranges. "
        "Returns the spec only — caller must wire to the event manager. "
        "aggressive_sim2real has very wide ranges; only use with deep RL."
    ),
    "add_latency_randomization": (
        "WHAT: Inject per-step action latency (uniform between min_ms and max_ms) into an IsaacLab env via an "
        "ActionLatencyEvent for the EventManager. "
        "WHEN: training a policy that must survive jittery real control loops, sim2real prep for any robot with non-trivial comms delay. "
        "RETURNS: code patch for approval; adds an EventManager-compatible event with appropriate buffer_size. "
        "CAVEATS: defaults are min_ms=10, max_ms=50, physics_dt=0.005s. "
        "buffer_size auto-computed as ceil(max_ms / (physics_dt * 1000)) if omitted — undersize causes silent action drops. "
        "Without latency randomization, sim policies often fail on real hardware when actions arrive late."
    ),
    "preview_dr": (
        "WHAT: Generate a small grid (default 3x3 = 9 frames) of viewport captures with the current DR randomizer "
        "triggered between each, so the user can visually verify ranges before a full SDG/training run. "
        "WHEN: sanity check after configure_correlated_dr or apply_dr_preset, before launching a long SDG run. "
        "RETURNS: code patch for approval; writes PNG previews into output_dir (default workspace/dr_previews). "
        "CAVEATS: default resolution is 512x512 — bump for higher fidelity inspection. "
        "Captures viewport, not the SDG-rendered output — use configure_sdg + render for true SDG samples. "
        "Only previews currently configured DR; configure DR first."
    ),

    # ── Clearance / Safety (3 tools) ────────────────────────────────────────
    "set_clearance_monitor": (
        "WHAT: Configure a near-miss / clearance monitor on a robot articulation: bumps PhysX contactOffset on each link "
        "so contact-report events fire BEFORE penetration, then warns when within clearance_mm or warning_mm of target prims. "
        "WHEN: ISO 10218 collaborative-robot setup, validating that a robot stays clear of fixtures, runtime safety guard. "
        "RETURNS: code patch for approval; subscribes to PhysX contact events and emits warnings. "
        "CAVEATS: default stop=50mm, warning=100mm. "
        "warning_mm must be > clearance_mm. "
        "For pre-flight trajectory check use check_path_clearance instead. "
        "For visual debugging use visualize_clearance."
    ),
    "visualize_clearance": (
        "WHAT: Visualize robot-to-obstacle clearance in the viewport. mode='heatmap' = SDF gradient color-coding link positions; "
        "mode='zones' = invisible PhysX trigger volumes around obstacles at warning + stop distances. "
        "WHEN: 'show me where the robot is too close', presenting safety zones to a stakeholder, debugging set_clearance_monitor. "
        "RETURNS: code patch for approval; modifies viewport visualization or adds trigger prims. "
        "CAVEATS: heatmap requires SDF MeshCollisionAPI on obstacles. "
        "zones mode adds prims to the stage — clean up afterwards. "
        "For runtime warnings use set_clearance_monitor; for trajectory pre-check use check_path_clearance."
    ),
    "check_path_clearance": (
        "WHAT: Pre-flight trajectory clearance check. Runs forward kinematics on each waypoint, queries SDF distance to each "
        "obstacle, flags waypoints whose minimum link-to-obstacle clearance falls below the threshold. "
        "WHEN: BEFORE execute_trajectory or move_to_pose, validating motion-plan output, catching near-miss before motion runs. "
        "RETURNS: {ok: bool, violations: [{waypoint_idx, link, obstacle, distance_mm}], min_clearance_mm}. "
        "CAVEATS: default threshold 50mm; trajectory is list of joint-position waypoints in radians. "
        "Obstacles must have SDF or mesh collider. "
        "Static check — does not detect time-varying motion of obstacles. "
        "For runtime monitoring use set_clearance_monitor."
    ),

    # ── Calibration / Actuator Identification (4 tools) ─────────────────────
    "calibrate_physics": (
        "WHAT: Bayesian-optimization calibration (Ray Tune + Optuna) of joint friction, damping, armature, link masses, "
        "viscous_friction from real robot HDF5 logs to close the sim2real gap. "
        "WHEN: user has 30-120s of real joint trajectory at 200-500 Hz and wants to fit physics parameters. "
        "RETURNS: {calibrated_parameters, suggested_dr_ranges, fit_metrics}. Long-running (30-120 min). "
        "CAVEATS: HDF5 must have joint_positions/velocities/torques_commanded. Default 100 trials × 4 workers. "
        "Skip contact stiffness/restitution/surface friction — randomize via DR instead. "
        "For 5-min first pass use quick_calibrate. For LSTM model use train_actuator_net."
    ),
    "quick_calibrate": (
        "WHAT: Fast (~5 min) physics calibration that fits only highest-impact parameters: armature (rotor inertia), "
        "Coulomb friction, and link masses (if include_masses=true). "
        "WHEN: first-pass sim2real gap reduction, time-constrained calibration, before deciding whether to invest in calibrate_physics. "
        "RETURNS: {calibrated_parameters, fit_metrics} written to output_dir (default workspace/calibration/<robot>_quick). "
        "CAVEATS: skips contact stiffness/restitution/surface friction (use DR for those). "
        "Needs ≥30s of HDF5 logs at 200-500 Hz. "
        "For the full multi-parameter fit use calibrate_physics. "
        "Validate result with validate_calibration on a held-out trajectory."
    ),
    "validate_calibration": (
        "WHAT: Validate a calibration result on a held-out real trajectory. Computes per-joint position/velocity RMSE "
        "(and contact F/T comparison if present) and reports gap reduction vs baseline. "
        "WHEN: after calibrate_physics or quick_calibrate, before committing the parameters into a training run. "
        "RETURNS: {position_rmse_per_joint, velocity_rmse_per_joint, gap_reduction_pct, recommend_commit: bool}. "
        "CAVEATS: test_data_path MUST be different from the data used during calibration (avoid overfitting). "
        "baseline_error is optional — without it only absolute calibrated error is reported. "
        "Pass the dict from calibrate_physics's 'calibrated_parameters' field directly."
    ),
    "train_actuator_net": (
        "WHAT: Train an IsaacLab ActuatorNet (LSTM neural actuator model) on real (q_target, q, q_dot, tau) pairs. "
        "Learns friction, backlash, and motor dynamics end-to-end without identifying individual physical parameters. "
        "WHEN: lots of real motor data available (5-10 min diverse motion), highest-fidelity actuator model needed, "
        "physics-parameter calibration not converging. "
        "RETURNS: code patch for approval; writes LSTM checkpoint to output_dir. Long-running headless job. "
        "CAVEATS: defaults hidden_dim=32, num_layers=2, num_epochs=200. "
        "Heavier than calibrate_physics — only use when parameter calibration is insufficient. "
        "ActuatorNet must be loaded into the env at training/eval time."
    ),

    # ── Sensors / WBC / Loco-Manip (5 tools) ────────────────────────────────
    "setup_contact_sensors": (
        "WHAT: Add GPU ContactSensorCfg for one or more bodies (e.g. fingertips) on an articulation, sized for num_envs "
        "parallel envs. Auto-bumps PhysxCfg.gpu_max_rigid_contact_count to avoid silent overflow. "
        "WHEN: 'add fingertip contact sensors', 'enable touch sensing', tactile-conditioned policies. "
        "RETURNS: code patch for approval (one ContactSensorCfg per body — mandatory one-to-many). "
        "CAVEATS: defaults num_envs=4096, update_period=0.0s, history_length=1. "
        "Path scoped {ENV_REGEX_NS}/Robot/<body>. Without buffer bump large num_envs silently drops contacts. "
        "track_air_time tracks time-since-last-contact (useful for legged locomotion)."
    ),
    "setup_whole_body_control": (
        "WHAT: One-shot whole-body control config combining a locomotion RL policy (lower body) with a Pink-IK QP arm "
        "planner (upper body), wired into an ActionGroupCfg. "
        "WHEN: humanoid that must walk + reach, 'set up whole-body control', 'combine HOVER with Pink-IK'. "
        "RETURNS: code patch for approval; full ActionGroupCfg with locomotion policy + arm planner. "
        "CAVEATS: pre-built profiles for g1 (HOVER flat) and h1 (HOVER rough); other robots get a generic skeleton. "
        "Default arm_planner=pink_ik (alternatives: lula, rmpflow); default ee_frame='left_hand'. "
        "Locomotion policy must be a trained checkpoint name. "
        "For diagnosing balance issues afterwards use diagnose_whole_body."
    ),
    "diagnose_whole_body": (
        "WHAT: Diagnostic for humanoid balance during arm motion: inspects CoM projection vs support polygon, "
        "arm-payload effect on locomotion, EE acceleration during gait. "
        "WHEN: 'why does the robot fall when reaching?', tuning whole-body controller, validating WBC config from setup_whole_body_control. "
        "RETURNS: {balance_margin_m, com_in_support: bool, ee_accel_m_s2, payload_effect, recommendations: [...]}. "
        "CAVEATS: defaults support_polygon_margin_m=0.05, ee_accel_threshold=5.0 m/s². "
        "Requires sim playing or recently played. "
        "Reports diagnoses but doesn't auto-fix — feed recommendations to setup_whole_body_control or tune_gains."
    ),
    "setup_loco_manipulation_training": (
        "WHAT: Set up joint locomotion + manipulation RL training. Picks an approach (decoupled / hierarchical / joint) "
        "and emits a 3-phase reward-mixing schedule that prevents manipulation rewards from drowning out locomotion. "
        "WHEN: 'train the robot to walk and pick up X', loco-manipulation skill bringup. "
        "RETURNS: code patch for approval; env config + ActionGroup + reward schedule + imbalance advisor warnings. "
        "CAVEATS: 'decoupled' = HOVER+IK (slow tasks); 'hierarchical' = dual-agent (dynamic); 'joint' = end-to-end. "
        "Default 'decoupled'. reward_terms optional but enables imbalance detection. "
        "For demo-based init use setup_rsi_from_demos."
    ),
    "setup_rsi_from_demos": (
        "WHAT: Configure Reference State Initialization (RSI) for an RL env — each episode's initial state is sampled "
        "from a demonstration trajectory file with Gaussian noise (default std=0.05) instead of the default fixed pose. "
        "WHEN: highest-impact technique for loco-manipulation training, 'initialize from demos', 'use RSI'. "
        "RETURNS: code patch for approval; InitialStateCfg with mode='demo_sampling' attached to env_cfg. "
        "CAVEATS: demo_path supports .npz, .hdf5, .pkl. "
        "env_cfg is a Python class path or identifier (e.g. 'G1WalkPickEnvCfg'). "
        "Without RSI, loco-manip training often fails to converge — call this before launching long training runs."
    ),
    "setup_multi_rate": (
        "WHAT: Generate a DualRateVecEnvWrapper that runs upper body (manipulation IK) at higher rate than lower body "
        "(locomotion RL). The wrapper caches lower-body actions between its update ticks. "
        "WHEN: manipulation needs ~100 Hz updates while locomotion policy was trained at ~50 Hz, 'multi-rate control', 'dual-rate wrapper'. "
        "RETURNS: code patch for approval; wrapper class wired to existing env. "
        "CAVEATS: defaults lower_rate_hz=50, upper_rate_hz=100, upper_dof=14 (typical humanoid two-arm count). "
        "upper_dof must match the number of upper-body DOFs at the FRONT of the action vector. "
        "Wrapper is a no-op if rates are equal — pass different values."
    ),

    # ── Workflow Engine (8 tools) ───────────────────────────────────────────
    "start_workflow": (
        "WHAT: Start a multi-step autonomous workflow with an editable plan. Generates plan first, then waits for approval. "
        "Types: 'rl_training' (env→reward→train→eval→deploy), 'robot_import' (import→verify→motion plan→report), "
        "'sim_debugging' (diagnose→hypothesis→fix→verify with auto error-fix loop). "
        "WHEN: high-level goals like 'set up RL training for X', 'import this URDF', 'debug why my sim is broken'. "
        "RETURNS: {workflow_id, plan_artifact, first_checkpoint}. "
        "CAVEATS: defaults scope_prim='/World', max_retries=3. auto_approve_checkpoints=true is DANGEROUS. "
        "Resume via approve_workflow_checkpoint. Edit plan via edit_workflow_plan."
    ),
    "edit_workflow_plan": (
        "WHAT: Edit a workflow's plan artifact during the planning checkpoint (before execution). "
        "WHEN: user wants to adjust parameters (e.g. 'change to 128 envs', 'add orientation reward', 'use a different reward shaping') "
        "after start_workflow generated a plan. "
        "RETURNS: {workflow_id, updated_plan}. "
        "CAVEATS: workflow stays paused — must call approve_workflow_checkpoint(action='approve') to actually run. "
        "plan_edits is dict of phase_name -> {field: new_value}, e.g. {'env_creation': {'num_envs': 128}}. "
        "Only works while workflow is at a checkpoint, not mid-execution. "
        "For aborting use cancel_workflow."
    ),
    "approve_workflow_checkpoint": (
        "WHAT: Approve / reject / revise at a workflow checkpoint. Workflows pause at checkpoints (after plan, after reward, "
        "after results, before deploy) and wait. 'approve' resumes, 'reject' cancels and rolls back, 'revise' returns to "
        "previous phase for re-generation. "
        "WHEN: responding to checkpoint prompts emitted by start_workflow. "
        "RETURNS: {workflow_id, next_phase, status}. "
        "CAVEATS: phase name must match the actual paused checkpoint (e.g. 'plan', 'reward', 'results', 'deploy'). "
        "feedback string is passed back to the LLM when action='revise'. "
        "reject triggers full rollback to pre-workflow snapshot."
    ),
    "cancel_workflow": (
        "WHAT: Cancel a running workflow and roll back to the pre-workflow snapshot. "
        "WHEN: user says 'stop', 'cancel', 'abort', or workflow can no longer proceed safely. "
        "RETURNS: {workflow_id, status: 'cancelled', rollback_summary}. "
        "CAVEATS: rollback restores the stage to the snapshot taken at start_workflow, undoing all intermediate changes. "
        "reason is logged for audit but optional. "
        "Different from approve_workflow_checkpoint(action='reject') which only rejects the current checkpoint — "
        "cancel terminates the entire workflow."
    ),
    "get_workflow_status": (
        "WHAT: Query a workflow's current state — current phase, completed phases, pending checkpoints, error-fix attempts, "
        "and the plan artifact. "
        "WHEN: user asks 'how is the workflow going?', 'what step are we on?', surfacing progress. "
        "RETURNS: {workflow_id, type, status, current_phase, completed_phases, pending_checkpoint, error_fix_attempts, plan}. "
        "CAVEATS: read-only — does not advance the workflow. "
        "For listing all workflows use list_workflows. "
        "For approving the pending checkpoint use approve_workflow_checkpoint."
    ),
    "execute_with_retry": (
        "WHAT: Execute a code patch through the autonomous error-fix loop. On failure, system reads the error, asks the LLM "
        "for a fix, and retries (max 3 by default, hard cap 5). "
        "WHEN: code-generation steps where occasional API mismatches or PhysX errors are expected (URDF imports, IsaacLab env "
        "creation, OmniGraph wiring). "
        "RETURNS: {success, attempts, final_code, output, error_history}. "
        "CAVEATS: hard cap on max_retries is 5 — higher values silently clamped. "
        "context_hints (e.g. ['use mdp.joint_pos not joint_positions', 'IsaacLab 2.x API']) help the LLM converge faster. "
        "Stops and reports if all retries exhausted — does not auto-rollback. "
        "Different from start_workflow which orchestrates multiple steps."
    ),
    "proactive_check": (
        "WHAT: Run the proactive agent for a given trigger. Observes scene state and reports issues without auto-modifying "
        "(unless AUTO_PROACTIVE_FIX env var set + auto_fix=true). "
        "WHEN: triggers fired by UI events: 'scene_opened', 'robot_imported', 'console_error', 'training_started/active/finished', "
        "'sim_idle', 'sim_play', 'fps_drop', 'target_placed'. "
        "RETURNS: {issues: [...], suggestions: [...], applied_fixes: [...] if auto_fix}. "
        "CAVEATS: by default reports only — set auto_fix=true AND env var to apply Tier 1 crash-preventer fixes. "
        "context dict is trigger-specific (scene_path / robot_path / error_text / fps / target_path). "
        "For manual deep diagnosis use diagnose_physics_error or check_physics_health."
    ),
    "list_workflows": (
        "WHAT: List active and recently completed workflows for the current session. "
        "WHEN: user asks 'what workflows are running?', recovering after a connection drop, surfacing background work. "
        "RETURNS: list of {workflow_id, type, status, current_phase, start_time}. "
        "CAVEATS: defaults include_completed=false (only active), limit=20. "
        "For full state of a single workflow use get_workflow_status. "
        "Session-scoped — does not persist across server restarts."
    ),

    # ── Templates / Hardware (3 tools) ──────────────────────────────────────
    "filter_templates_by_hardware": (
        "WHAT: Filter scene/example templates by detected GPU capabilities. Reads template metadata (min_vram_gb, "
        "recommended_vram_gb, estimated_fps, tags) and returns those that fit the user's hardware. "
        "WHEN: 'what templates work on my GPU', 'show beginner-friendly examples for my hardware', browsing compatible starters. "
        "RETURNS: list of templates {name, description, vram_required, estimated_fps, tags}. "
        "CAVEATS: auto-detects GPU via HydraEngineStats / nvidia-smi when device_vram_gb omitted. "
        "Default include_recommended_only=false (uses min_vram_gb). "
        "Optional category (e.g. 'manipulation', 'locomotion') and tag (e.g. 'beginner_friendly') filters. "
        "For VRAM cost of a planned op use check_vram_headroom."
    ),
    "export_template": (
        "WHAT: Package the current scene (or a referenced USD path) plus its config and metadata into a portable .isaa "
        "file (zip with manifest.json) for sharing via email, GitHub, Discord — no central server required. "
        "WHEN: 'export this as a template', 'share this scene', 'package this for someone else', 'save as .isaa file'. "
        "RETURNS: code patch for approval; writes <name>.isaa into output_dir (default workspace/templates/exports). "
        "CAVEATS: scene_path optional — exports current open stage if omitted. "
        "name is the only required field. "
        "min_vram_gb/recommended_vram_gb/tags become hardware filters consumable by filter_templates_by_hardware. "
        "Recipient imports with import_template."
    ),
    "import_template": (
        "WHAT: Import a shared .isaa template into the local template library. Validates manifest, extracts USD + config + "
        "assets, registers for filter_templates_by_hardware. "
        "WHEN: user provides a downloaded .isaa file, 'install this template'. "
        "RETURNS: {imported_name, library_path, registered: bool}. "
        "CAVEATS: default library_dir=workspace/templates/library, overwrite=false (refuses duplicate names). "
        "Validates manifest before extracting — corrupt/incompatible files rejected. "
        "After import, browse with filter_templates_by_hardware. "
        "Counterpart to export_template."
    ),

    # ── VRAM / Async (3 tools) ──────────────────────────────────────────────
    "check_vram_headroom": (
        "WHAT: Estimate VRAM cost of an upcoming operation (clone, train, sdg, render, custom) and compare against available "
        "GPU memory. Returns warning + actionable suggestions if likely to OOM. "
        "WHEN: ALWAYS before clone_prim with count >= 64, launch_training, or configure_sdg with high frame counts. "
        "RETURNS: {estimated_mb, available_mb, ok: bool, warning, suggestions: [...]}. "
        "CAVEATS: default complexity='medium'. "
        "per_env_mb_override skips heuristic and uses explicit estimate. "
        "Auto-detects device_vram_gb and currently_used_gb via nvidia-smi when omitted. "
        "Suggestions include reducing env count, switching to headless, using cloud compute. "
        "Heuristic — actual VRAM may vary by ±20%."
    ),
    "dispatch_async_task": (
        "WHAT: Launch a long-running operation (training, SDG, benchmarks, render, custom) in a background thread/process "
        "and return task_id immediately so the user can keep working. Notifies chat panel via SSE on completion. "
        "WHEN: operations >30s that don't need to block chat, parallelizing multiple training runs, fire-and-forget SDG. "
        "RETURNS: {task_id, label, started_at}. "
        "CAVEATS: params dict is task-specific (e.g. {'num_frames': 500, 'output_dir': 'workspace/sdg_output/run_042'} for SDG). "
        "label shown in status messages — include enough context to identify later. "
        "Pair with query_async_task for status polling. "
        "Background task survives chat session ends."
    ),
    "query_async_task": (
        "WHAT: Check status of a previously dispatched async task. "
        "WHEN: user asks 'how's that SDG run going?', 'is the training done?', polling for completion. "
        "RETURNS: {state: 'pending'|'running'|'done'|'error', progress: 0-1, elapsed_s, result, error}. "
        "CAVEATS: task_id must match one returned by dispatch_async_task. "
        "Result payload is only present when state='done'. "
        "error contains traceback when state='error'. "
        "Read-only — does not cancel the task."
    ),

    # ── Visualization / Demo (4 tools) ──────────────────────────────────────
    "visualize_forces": (
        "WHAT: Draw colored debug arrows in the viewport showing per-joint torques on an articulation. Green <70% of limit, "
        "yellow >70%, red >90% (near saturation). "
        "WHEN: visually debugging RL policies, demonstrating robot dynamics, spotting joints near saturation. "
        "RETURNS: code patch for approval; uses omni.isaac.debug_draw.draw_lines for arrows. "
        "CAVEATS: default scale=0.01 m/Nm, update_hz=30. "
        "Requires sim playing for live torque values. "
        "For numerical readout use get_joint_torques or monitor_joint_effort. "
        "Visualization persists until cleared or sim stops."
    ),
    "render_video": (
        "WHAT: Render a video clip using Isaac Sim's RTX renderer (NOT a screen capture — full path/ray-traced output via "
        "omni.kit.capture / Movie Capture). Quality presets: 'preview' (RayTracing 720p 1 SPP), 'presentation' (PathTracing "
        "1080p 64 SPP), 'production' (PathTracing 4K 256 SPP). "
        "WHEN: 'rendered video', 'cinematic clip', 'turntable', 'export a demo video'. "
        "RETURNS: code patch for approval; writes MP4 to output_path (default workspace/renders/<timestamp>.mp4). "
        "CAVEATS: production preset is slow (minutes per second of footage). "
        "Default fps=30. camera defaults to active viewport. "
        "For raw screen capture use record_demo_video instead (faster but lower quality)."
    ),
    "quick_demo": (
        "WHAT: Build a complete demo scene in under 2 minutes. Chains template loading, robot import, object placement, "
        "and pre-trained policy deployment. "
        "WHEN: 'give me a quick pick-and-place demo', presentations, sales pitches, onboarding. "
        "RETURNS: code patch for approval; full demo scene ready to play. "
        "CAVEATS: only 3 demo_types: pick_place, mobile_nav, humanoid_walk. "
        "robot must match demo_type (e.g. 'franka' for pick_place, 'jetbot' for mobile_nav, 'g1' for humanoid_walk). "
        "scene_style controls lighting/background (clean/industrial/lab/dramatic, default 'clean'). "
        "For custom scenes use generate_scene_blueprint."
    ),

    # ── Sim2Real Analysis (3 tools) ─────────────────────────────────────────
    "measure_sim_real_gap": (
        "WHAT: Compare sim and real trajectories to quantify the sim-to-real gap. "
        "WHEN: after running matched task in both sim and real, before deciding to calibrate, validating gap reduction. "
        "RETURNS: {per_joint_position_error, per_joint_velocity_error, ee_cartesian_error_m, observation_distribution_gap}. "
        "CAVEATS: both files HDF5 or CSV with matched task execution (same trajectory, same controller). "
        "Joint counts and order must match between sim and real. "
        "Feed result into suggest_parameter_adjustment for fix recommendations. "
        "For closing the gap use calibrate_physics or quick_calibrate."
    ),
    "suggest_parameter_adjustment": (
        "WHAT: Given a gap report from measure_sim_real_gap, suggest which physics parameters (friction, damping, stiffness) "
        "to adjust to close the gap. "
        "WHEN: after measure_sim_real_gap, deciding what to calibrate next, debugging persistent sim-to-real mismatch. "
        "RETURNS: {recommendations: [{param, current, suggested, rationale}], confidence}. "
        "CAVEATS: gap_report must be the dict returned by measure_sim_real_gap (not a path). "
        "Heuristic — for proper fitting use calibrate_physics with the same real data. "
        "Suggestions are starting points; validate with validate_calibration."
    ),
    "compare_sim_real_video": (
        "WHAT: Side-by-side or overlay comparison of sim and real video using a vision LLM. Identifies behavioral differences "
        "(overshoot, contact timing, missed grasps). "
        "WHEN: behavioral debugging when joint-level metrics look fine but task succeeds in sim and fails on hardware. "
        "RETURNS: {differences: [...], qualitative_summary, suggested_focus_areas}. "
        "CAVEATS: both videos MP4. "
        "Vision LLM call — costs tokens, takes seconds. "
        "Best on short clips (5-30s) of the same task. "
        "Complement to measure_sim_real_gap (numerical) — use both for hard cases."
    ),

    # ── GR00T Diagnostics (6 tools) ─────────────────────────────────────────
    "extract_attention_maps": (
        "WHAT: Extract cross-attention maps from GR00T's DiT for a failed action. Shows which visual patches and language "
        "tokens drove the policy. "
        "WHEN: debugging 'why did GR00T do X', visualizing what the VLA was looking at when an action went wrong. "
        "RETURNS: {visual_attention_per_patch, language_attention_per_token, attended_regions}. "
        "CAVEATS: default layer=12 per GR00T paper. "
        "observation_path must contain image + state. "
        "Requires GR00T checkpoint (.pt). "
        "For OOD detection use detect_ood. For checkpoint analysis use analyze_checkpoint."
    ),
    "detect_ood": (
        "WHAT: Detect out-of-distribution observations for GR00T. Three tiers: 1=action variance/autocorr (free), "
        "2=4-sample DiT variance (+15ms), 3=Mahalanobis on layer-12 embeddings (requires calibration_path). "
        "WHEN: runtime safety guard during deployment, flagging unsafe situations, validating policy operating envelope. "
        "RETURNS: {ood_score, threshold, ood: bool, tier_used}. "
        "CAVEATS: tier 1 needs only action_sequence (recent action history). "
        "tier 2/3 need checkpoint_path. "
        "tier 3 needs calibration_path (precomputed stats from in-distribution data). "
        "Tier 1 cheap but noisy; Tier 3 best but heaviest."
    ),
    "suggest_data_mix": (
        "WHAT: Recommend sim/real/video data ratio for GR00T fine-tuning using NVIDIA's validated 1:1 real:neural recipe "
        "(40% gain over real-only). "
        "WHEN: planning a fine-tune dataset, deciding how much sim data to add to small real-demo set. "
        "RETURNS: {recommended_ratio, rationale, expected_gain_pct}. "
        "CAVEATS: available_data is dict {real_demos, sim_demos, video_demos} — counts, not paths. "
        "task_type is free-form (e.g. 'tabletop pick-and-place', 'mobile navigation'). "
        "For freeze/tune strategy use suggest_finetune_config. "
        "For monitoring forgetting during the run use monitor_forgetting."
    ),
    "suggest_finetune_config": (
        "WHAT: Recommend layer freeze/tune strategy for GR00T fine-tuning based on task similarity, hardware, and data size. "
        "Avoids 'Don't Blind Your VLA' OOD loss from over-tuning vision. "
        "WHEN: starting a fine-tune, deciding what to freeze, hardware-constrained tuning. "
        "RETURNS: {freeze_layers: [...], tune_layers: [...], lora_targets: [...], batch_size, learning_rate, rationale}. "
        "CAVEATS: task_type is one of similar_to_pretrain, new_visual_domain, new_embodiment. "
        "hardware free-form (e.g. 'A6000', 'RTX 4090') — used for batch size + memory. "
        "data_size optional but improves recommendation quality. "
        "Pair with monitor_forgetting during training."
    ),
    "monitor_forgetting": (
        "WHAT: Detect catastrophic forgetting during GR00T fine-tuning. Runs a 30-example VQA regression suite on each "
        "checkpoint and computes per-layer weight drift vs base model. Alerts on >20% VQA score drop. "
        "WHEN: every N checkpoints during fine-tuning, before committing a fine-tuned model, debugging policy regression. "
        "RETURNS: {vqa_score, baseline_vqa, score_drop_pct, drift_per_layer, alert: bool}. "
        "CAVEATS: checkpoint_dir scanned for .pt files. "
        "base_model needed for drift computation. "
        "VQA suite is fixed (30 examples) — not customizable here. "
        "If alert fires, reduce learning rate or freeze more layers (suggest_finetune_config)."
    ),
    "export_policy": (
        "WHAT: Export GR00T checkpoint to deployment format (TensorRT bf16). "
        "WHEN: deploying to robot hardware, benchmarking inference speed, integrating into ROS node. "
        "RETURNS: code patch for approval; writes engine + deployment manifest. "
        "CAVEATS: target_device options: jetson_agx_orin (5.8 Hz), jetson_orin_nx (~3 Hz, no FP8), x86_rtx4090 (~15 Hz), x86_a6000. "
        "inference_budget_ms enforced at engine-build time — narrower budget = lower precision. "
        "Output is device-specific — engine built for AGX Orin won't run on RTX 4090. "
        "For checkpoint inspection use analyze_checkpoint."
    ),
    "analyze_checkpoint": (
        "WHAT: Analyze a GR00T checkpoint: detect embodiment, training steps, per-layer drift (vision/DiT/adapter/LM), "
        "action statistics, forgetting risk. "
        "WHEN: inspecting a downloaded/handed-over checkpoint, pre-flight before deploy, debugging unexpected behavior. "
        "RETURNS: {embodiment, training_steps, per_layer_drift, action_stats, forgetting_risk_score, summary}. "
        "CAVEATS: base_model_path optional but enables drift comparison (key for forgetting risk). "
        "Read-only — does not modify checkpoint. "
        "For runtime export use export_policy. "
        "For real-time forgetting monitoring during training use monitor_forgetting."
    ),

    # ── Education / Determinism / USD Atomic (3 tools) ──────────────────────
    "create_broken_scene": (
        "WHAT: Create a scene with a specific, diagnosable fault for students to find and fix. Educational tool. "
        "WHEN: lab exercises, self-study, classroom demos of common Isaac Sim failure modes. "
        "RETURNS: code patch for approval; new scene with the chosen fault planted. "
        "CAVEATS: fault_type options: missing_collision, zero_mass, wrong_scale, inverted_joint, no_physics_scene, "
        "inf_joint_limits. "
        "Default scene_name='BrokenScene'. "
        "Each fault is intentional and known — diagnosable via check_physics_health, preflight_check, diagnose_physics_error."
    ),
    "enable_deterministic_mode": (
        "WHAT: Enable deterministic simulation. Fixes random seeds, sets PhysX to TGS CPU mode (deterministic for identical "
        "inputs), disables async ops, optionally exports a reproducibility archive. "
        "WHEN: safety certification, reproducibility for paper results, regression-testing physics, audit trails. "
        "RETURNS: code patch for approval; modifies PhysicsScene + DR seeds. "
        "CAVEATS: defaults seed=42, physics_dt=1/60s, solver_iterations=4. TGS CPU is SLOWER than GPU PGS. "
        "export_archive_path writes .zip with scene + params + versions. "
        "Deterministic only for IDENTICAL inputs — different hardware may vary at FP32 boundaries."
    ),
    "get_attribute": (
        "WHAT: Read a single attribute value from a USD prim. "
        "WHEN: verifying result of a set_attribute call, scene introspection, scripted property reads. "
        "RETURNS: typed value (number, string, array, bool) or null if attribute missing. "
        "CAVEATS: attr_name is the full USD attribute name (e.g. 'radius', 'xformOp:translate', 'visibility'). "
        "For listing all attributes on a prim use list_attributes. "
        "For setting a value use set_attribute. "
        "Returns null (not error) if the attribute doesn't exist — check for null before using value."
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
        if name in POLISH_B4:
            tool["function"]["description"] = POLISH_B4[name]
            count += 1
    return count
