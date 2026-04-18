# New Capability — Sim-to-Real Gap Tooling

**Type:** New tool set  
**Source:** Personas P02 (Erik), P04 (Sarah), P10 (Jin) — "the single most expensive problem we have"  
**Research:** `rev2/research_material_database.md`, personas feature extraction

---

## Overview

Sim-to-real gap is the #1 pain point across startup CTOs, systems integrators, and humanoid ML engineers. Currently: guess parameters → train → deploy → fail → iterate blindly. This adds structured tooling to measure, diagnose, and close the gap.

---

## Tools

### `measure_sim_real_gap(sim_trajectory, real_trajectory)`

**Type:** DATA handler

Compare sim and real trajectories to quantify the gap.

**Input:** Two trajectory files (HDF5 or CSV) with matched timestamps:
- Joint positions over time
- End-effector positions over time
- Optionally: contact forces, sensor readings

**Analysis:**
```python
def measure_gap(sim_traj, real_traj):
    # Align trajectories by timestamp
    aligned = align_trajectories(sim_traj, real_traj)
    
    # Per-joint position error
    joint_errors = {
        joint: {
            "mean_error_deg": np.mean(np.abs(sim[joint] - real[joint])),
            "max_error_deg": np.max(np.abs(sim[joint] - real[joint])),
            "correlation": np.corrcoef(sim[joint], real[joint])[0,1]
        }
        for joint in joints
    }
    
    # End-effector Cartesian error
    ee_error = np.linalg.norm(sim["ee_pos"] - real["ee_pos"], axis=1)
    
    # Distributional distance (for policy evaluation)
    from scipy.stats import wasserstein_distance
    obs_gap = wasserstein_distance(sim["observations"].flatten(), real["observations"].flatten())
    
    return {
        "joint_errors": joint_errors,
        "ee_mean_error_mm": np.mean(ee_error) * 1000,
        "ee_max_error_mm": np.max(ee_error) * 1000,
        "observation_gap": obs_gap,
        "worst_joint": max(joint_errors, key=lambda j: joint_errors[j]["mean_error_deg"]),
        "recommendation": generate_recommendation(joint_errors, ee_error)
    }
```

### `suggest_parameter_adjustment(gap_report)`

**Type:** DATA handler (LLM-assisted)

Given a gap report, suggest which physics parameters to adjust:

```
Gap analysis shows:
- Joint 4 (wrist) has 3.2° mean error — likely friction/damping mismatch
- End-effector drifts 12mm over 5s trajectory — likely joint compliance
- Contact forces 40% higher in sim — likely friction coefficient too high

Suggested adjustments:
1. Reduce wrist joint damping by 30% (current: 0.5 → try: 0.35)
2. Add joint compliance: stiffness 1e4 → 8e3
3. Reduce steel-rubber friction from 0.8 to 0.6
```

### `compare_sim_real_video(sim_video_path, real_video_path)`

**Type:** DATA handler (vision-based)

Side-by-side or overlay comparison of sim and real video. Use the existing Gemini Vision provider to analyze:
- "The real robot overshoots at the wrist — the sim doesn't show this"
- "Contact timing is 0.3s earlier in sim than real"

### `create_calibration_experiment(parameter, range, num_samples)`

**Type:** CODE_GEN handler

Generate a set of sim runs with systematically varied parameters to find the best match to real data:

```python
# Grid search: vary friction from 0.4 to 1.0 in 7 steps
for friction in np.linspace(0.4, 1.0, 7):
    # Set physics material
    # Run trajectory
    # Record sim data
    # Compare with real data
    # Keep the friction value that minimizes gap
```

---

## Workflow Integration

This naturally chains with other tools:

1. **Phase 7C** (teleop) → record demo on real robot → HDF5
2. **Phase 7A** (RL) → train policy in sim
3. **This tool** → `measure_sim_real_gap(sim_rollout, real_rollout)`
4. **This tool** → `suggest_parameter_adjustment(gap)`
5. **Material database** → update friction with calibrated values
6. **Phase 7E** (Eureka) → adjust DR ranges based on measured gap
7. Iterate

---

## Prerequisites

- Real robot data in HDF5 or CSV format (joint positions + timestamps minimum)
- Matched task execution in sim and real (same start config, same command sequence)
- Phase 7C (teleop) for demo recording helps but is not required

---

## Test Strategy

| Test | Level | What |
|------|-------|------|
| Trajectory alignment | L0 | Known timestamps → correct alignment |
| Per-joint error computation | L0 | Known trajectories → correct errors |
| Wasserstein distance | L0 | Known distributions → correct distance |
| Recommendation generation | L0 | Known error patterns → correct suggestions |
| Full pipeline | L3 | Requires real robot data |

---

## Known Limitations

- Requires real robot data — not useful until hardware is in the loop
- Friction/damping suggestions are heuristic, not system identification
- Video comparison via VLM is qualitative, not quantitative
- Calibration grid search is expensive (N sim runs × trajectory length)
- No automatic parameter optimization (future: Bayesian optimization)
