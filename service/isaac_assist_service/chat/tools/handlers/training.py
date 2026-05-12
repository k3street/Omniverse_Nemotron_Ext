"""Training handlers — target scope: launch training jobs, evaluate
rewards, GR00T finetuning + evaluation, environment cloning,
policy export, locomotion+manipulation training setup.

Phase 6 wave 6 — first training/RL code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-5.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 6 wave 6 — training launch + reward eval + GR00T + env cloning + policy export


def _gen_launch_training(args: Dict) -> str:
    """Generate code to launch an IsaacLab training run."""
    task = args["task"]
    algo = args.get("algo", "ppo")
    num_steps = args.get("num_steps", 1_000_000)
    num_envs = args.get("num_envs", 64)
    ckpt_dir = args.get("checkpoint_dir", f"workspace/rl_checkpoints/{task}")

    # Map algos to IsaacLab train script args
    algo_map = {
        "ppo": "rsl_rl",
        "sac": "skrl",
        "td3": "skrl",
        "rsl_rl": "rsl_rl",
    }
    runner = algo_map.get(algo, "rsl_rl")

    lines = [
        "import subprocess",
        "import sys",
        "import os",
        "",
        f"task = '{task}'",
        f"algo = '{algo}'",
        f"num_envs = {num_envs}",
        f"max_iterations = {num_steps // (num_envs * 24)}  # steps / (envs * horizon)",
        f"log_dir = '{ckpt_dir}'",
        "os.makedirs(log_dir, exist_ok=True)",
        "",
        "# Launch IsaacLab training",
        "cmd = [",
        "    sys.executable, '-m',",
        f"    'isaaclab.train',",
        f"    '--task', task,",
        f"    '--num_envs', str(num_envs),",
        f"    '--max_iterations', str(max_iterations),",
        f"    '--log_dir', log_dir,",
        "]",
        "print('Launching training: ' + ' '.join(cmd))",
        "proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)",
        "print(f'Training started (PID: {proc.pid}). Checkpoints → {log_dir}')",
    ]
    return "\n".join(lines)


def _gen_evaluate_reward(args: Dict) -> str:
    """Generate code to evaluate a candidate reward function via short training."""
    reward_code = args["reward_code"]
    env_id = args["env_id"]
    num_steps = args.get("num_steps", 1000)

    # Escape the reward code for embedding in a string
    escaped_reward = reward_code.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

    return f"""\
import os
import sys
import json
import tempfile
import subprocess
from pathlib import Path

# 1. Write the candidate reward function to a temp file
reward_code = '''{reward_code}'''

reward_dir = tempfile.mkdtemp(prefix='eureka_reward_')
reward_path = os.path.join(reward_dir, 'reward_fn.py')
with open(reward_path, 'w') as f:
    f.write(reward_code)

print(f'Reward function written to {{reward_path}}')

# 2. Launch training subprocess with the custom reward
env_id = '{env_id}'
num_steps = {num_steps}

cmd = [
    sys.executable, '-m', 'isaaclab.train',
    '--task', env_id,
    '--num_envs', '16',
    '--max_iterations', str(num_steps // 16),
    '--custom_reward', reward_path,
    '--headless',
]

print(f'Launching evaluation: {{" ".join(cmd)}}')
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    cwd=reward_dir,
)
stdout, _ = proc.communicate(timeout=300)

# 3. Parse training metrics from stdout
results = {{
    'env_id': env_id,
    'num_steps': num_steps,
    'reward_path': reward_path,
    'return_code': proc.returncode,
    'stdout_tail': stdout[-2000:] if stdout else '',
}}

# 4. Look for metrics JSON in output
metrics_path = os.path.join(reward_dir, 'metrics.json')
if os.path.exists(metrics_path):
    with open(metrics_path) as f:
        metrics = json.load(f)
    results['fitness'] = metrics.get('fitness', 0.0)
    results['components'] = metrics.get('components', {{}})
    results['task_success_rate'] = metrics.get('task_success_rate', 0.0)
else:
    results['fitness'] = 0.0
    results['components'] = {{}}
    results['task_success_rate'] = 0.0
    results['note'] = 'No metrics.json found — training may have failed'

print(f'Evaluation complete: fitness={{results["fitness"]:.4f}}, success={{results["task_success_rate"]:.2%}}')
print(json.dumps(results, indent=2))
"""


def _gen_evaluate_groot(args: Dict) -> str:
    """Generate code to run closed-loop GR00T N1 evaluation."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    task = args["task"]
    num_episodes = args.get("num_episodes", 50)
    checkpoint = args.get("checkpoint")

    model_path_expr = (
        f"'{checkpoint}'" if checkpoint
        else f"'workspace/groot_models/{model_id.split('/')[-1]}'"
    )

    return f"""\
import subprocess
import sys
import os
import json

model_path = {model_path_expr}
task = '{task}'
num_episodes = {num_episodes}
results_dir = 'workspace/groot_eval_results'
os.makedirs(results_dir, exist_ok=True)

# Step 1: Launch GR00T policy server as background process
server_cmd = [
    sys.executable, '-m', 'gr00t.deploy.policy_server',
    '--model-path', model_path,
    '--port', '50051',
]
print(f'Launching GR00T policy server: {{" ".join(server_cmd)}}')
server_proc = subprocess.Popen(server_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

# Step 2: Run IsaacLabEvalTasks evaluation
eval_cmd = [
    sys.executable, '-m', 'gr00t.eval.isaac_lab',
    '--task', task,
    '--num-episodes', str(num_episodes),
    '--policy-server', 'localhost:50051',
    '--results-dir', results_dir,
]
print(f'Running evaluation: {{" ".join(eval_cmd)}}')
eval_proc = subprocess.Popen(eval_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
eval_proc.wait()

# Step 3: Collect results
results_file = os.path.join(results_dir, f'{{task}}_results.json')
if not os.path.exists(results_file):
    # Eval subprocess completed (.wait() returned) but didn't produce a
    # results JSON — the run failed. Previously we printed the missing
    # path and returned success=True; the agent would then narrate
    # "Evaluation complete" with no data. Terminate the server, collect
    # what stdout we have, then raise.
    server_proc.terminate()
    _stdout = b''
    try:
        _stdout = eval_proc.stdout.read() if eval_proc.stdout else b''
    except Exception:
        pass
    raise RuntimeError(
        'evaluate_groot: eval subprocess finished (returncode=' + str(eval_proc.returncode) +
        ') but no results file at ' + repr(results_file) + '. '
        'Last stdout: ' + repr(_stdout[-400:])
    )
with open(results_file) as f:
    metrics = json.load(f)
print(f'Evaluation complete: success_rate={{metrics.get("success_rate", "N/A")}}')
print(f'Task metrics: {{json.dumps(metrics.get("task_metrics", {{}}), indent=2)}}')

# Step 4: Cleanup policy server
server_proc.terminate()
print(f'Policy server terminated (PID: {{server_proc.pid}})')
"""


def _gen_finetune_groot(args: Dict) -> str:
    """Generate code to fine-tune GR00T N1 on demo data."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    demo_data = args["demo_data"]
    num_steps = args.get("num_steps", 10000)
    lora = args.get("lora", True)
    output_dir = args.get("output_dir", "workspace/groot_checkpoints")

    vram_note = (
        "# LoRA fine-tuning: ~25 GB VRAM (1x RTX 4090 sufficient)"
        if lora else
        "# Full fine-tuning: ~48 GB VRAM (2x RTX 4090 or 1x A100 recommended)"
    )

    lora_flags = (
        "    '--use-lora',\n"
        "    '--lora-rank', '16',\n"
        "    '--lora-alpha', '32',\n"
    ) if lora else ""

    return f"""\
import subprocess
import sys
import os

model_id = '{model_id}'
demo_data = '{demo_data}'
num_steps = {num_steps}
output_dir = '{output_dir}'
{vram_note}

os.makedirs(output_dir, exist_ok=True)

# VRAM check
try:
    import torch
    if torch.cuda.is_available():
        vram_gb = torch.cuda.get_device_properties(0).total_mem / (1024**3)
        min_vram = {'25' if lora else '48'}
        if vram_gb < min_vram:
            print(f'WARNING: {{vram_gb:.1f}} GB VRAM detected, {{min_vram}} GB recommended.')
            print('Consider using NVIDIA Cloud (brev.dev/nvidia) or multi-GPU setup.')
except ImportError:
    pass

# Launch fine-tuning
cmd = [
    sys.executable, '-m', 'gr00t.finetune.train',
    '--model-id', model_id,
    '--demo-data', demo_data,
    '--num-steps', str(num_steps),
    '--output-dir', output_dir,
{lora_flags}]
print(f'Launching GR00T fine-tuning: {{" ".join(cmd)}}')
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print(f'Fine-tuning started (PID: {{proc.pid}}). Checkpoints → {{output_dir}}')
"""


def _gen_clone_envs(args: Dict) -> str:
    source_path = args["source_path"]
    num_envs = args["num_envs"]
    spacing = args.get("spacing", 2.5)
    collision_filter = args.get("collision_filter", True)

    lines = [
        "from isaacsim.core.cloner import GridCloner",
        "",
        f"cloner = GridCloner(spacing={spacing})",
        'cloner.define_base_env("/World/envs")',
        f'prim_paths = cloner.generate_paths("/World/envs/env", {num_envs})',
        "positions = cloner.clone(",
        f"    source_prim_path='{source_path}',",
        "    prim_paths=prim_paths,",
        "    replicate_physics=True,  # CRITICAL for performance",
        ")",
    ]
    if collision_filter:
        lines.extend([
            "# Collision filtering is a SEPARATE step:",
            "cloner.filter_collisions(",
            "    physicsscene_path='/World/PhysicsScene',",
            "    collision_root_path='/World/collisionGroups',",
            "    prim_paths=prim_paths,",
            ")",
        ])
    lines.append(f"print(f'Cloned {num_envs} environments from {source_path}')")
    return "\n".join(lines)


def _gen_setup_loco_manipulation_training(args: Dict) -> str:
    """Generate training scaffolding + reward-mixing advisor for loco-manipulation."""
    task = args["task_description"]
    robot = args["robot"]
    approach = args.get("approach", "decoupled")
    reward_terms = args.get("reward_terms", []) or []

    # Categorize and sum weights to detect imbalance
    loco_weight = 0.0
    manip_weight = 0.0
    for term in reward_terms:
        cat = (term.get("category") or "").lower()
        w = float(term.get("weight", 0.0))
        if cat == "locomotion":
            loco_weight += w
        elif cat == "manipulation":
            manip_weight += w

    advisor_lines = []
    if reward_terms:
        advisor_lines.append("# Reward mixing advisor:")
        for term in reward_terms:
            advisor_lines.append(
                f"#   - {term.get('name', '?')}: weight {term.get('weight', '?')}"
                f" ({term.get('category', 'unknown')})"
            )
        if manip_weight > loco_weight and loco_weight > 0:
            advisor_lines.extend([
                "#",
                f"# WARNING: manipulation weight ({manip_weight}) exceeds locomotion ({loco_weight}).",
                "# Early training will optimize grasping at the expense of balance.",
                "#",
                "# Recommended 3-phase schedule:",
                "#   Phase 1 (0-2000 iters):    locomotion_weight=5.0, manipulation_weight=0.5",
                "#   Phase 2 (2000-5000 iters): locomotion_weight=2.0, manipulation_weight=1.0",
                "#   Phase 3 (5000+ iters):     locomotion_weight=1.0, manipulation_weight=2.0",
            ])
        else:
            advisor_lines.append("# Reward weights look balanced for early training.")

    if approach == "decoupled":
        approach_blurb = (
            "# Approach: DECOUPLED (HOVER locomotion + Pink-IK arm).\n"
            "# Best for slow deliberate tasks. Lowest complexity — already in IsaacLab."
        )
    elif approach == "hierarchical":
        approach_blurb = (
            "# Approach: HIERARCHICAL dual-agent (SoFTA / FALCON pattern).\n"
            "# Best for dynamic tasks. Medium complexity."
        )
    else:  # joint
        approach_blurb = (
            "# Approach: JOINT end-to-end RL.\n"
            "# Maximum performance, highest complexity — needs reward curriculum."
        )

    lines = [
        '"""Loco-manipulation training scaffold.',
        f"Task: {task}",
        f"Robot: {robot}",
        f"Approach: {approach}",
        '"""',
        approach_blurb,
        "",
        f"task_description = {task!r}",
        f"robot = {robot!r}",
        f"approach = {approach!r}",
        "",
    ]
    if advisor_lines:
        lines.extend(advisor_lines)
        lines.append("")
    lines.extend([
        "# Configure the env builder according to the chosen approach.",
        "# (See create_isaaclab_env / launch_training tools to wire the pieces.)",
    ])
    return "\n".join(lines)


def _gen_export_policy(args: Dict) -> str:
    """Generate code to export GR00T checkpoint to TensorRT."""
    from ..tool_executor import _EXPORT_TARGETS  # noqa: PLC0415
    checkpoint = args["checkpoint"]
    target = args["target_device"]
    budget_ms = args.get("inference_budget_ms")

    target_info = _EXPORT_TARGETS.get(target, _EXPORT_TARGETS["x86_rtx4090"])

    return f"""\
# Export GR00T policy to TensorRT for {target}
import os
import json

checkpoint_path = {checkpoint!r}
target_device = {target!r}
target_info = {target_info!r}
budget_ms = {budget_ms!r}

print(f"Exporting {{checkpoint_path}} for {{target_device}}")
print(f"  Format: {{target_info['format']}}")
print(f"  Expected throughput: {{target_info['expected_hz']}} Hz")
print(f"  FP8 supported: {{target_info['fp8_supported']}}")
print(f"  Note: {{target_info['note']}}")

if not target_info['fp8_supported']:
    print("⚠ FP8/NVFP4 unsupported on this device — capped at bf16")

# Actual export pipeline:
# 1. Load checkpoint via gr00t.policy.dit_policy.DiTPolicy
# 2. Convert to ONNX via torch.onnx.export
# 3. Build TensorRT engine via trtexec or polygraphy:
#    trtexec --onnx=policy.onnx --saveEngine=policy.engine --bf16

output_engine = checkpoint_path.replace('.pt', f'.{{target_device}}.engine')
print(f"Output engine path: {{output_engine}}")

if budget_ms:
    if 1000 / budget_ms > target_info['expected_hz']:
        print(f"⚠ Budget {{budget_ms}}ms requires {{1000/budget_ms:.1f}} Hz but device max is {{target_info['expected_hz']}} Hz")
"""



# ---------------------------------------------------------------------------
# Phase 6 wave 24 — stragglers


def _gen_cloud_download_results(args: Dict) -> str:
    """Generate code to download results from a cloud instance."""
    job_id = args["job_id"]
    output_dir = args.get("output_dir", "workspace/cloud_results")

    return f'''\
import subprocess
import os

job_id = "{job_id}"
output_dir = "{output_dir}"
os.makedirs(output_dir, exist_ok=True)

# IsaacAutomator stores results on the cloud instance at /results/
# Retrieve the instance IP from the deployment state
state_file = f"deployments/{{job_id}}/state.json"
if os.path.exists(state_file):
    import json
    with open(state_file) as f:
        state = json.load(f)
    instance_ip = state.get("instance_ip", "UNKNOWN_IP")
    key_path = state.get("ssh_key", "~/.ssh/isaacautomator")
else:
    instance_ip = "UNKNOWN_IP"
    key_path = "~/.ssh/isaacautomator"
    print(f"WARNING: State file not found at {{state_file}}. Set instance_ip manually.")

# Download results via rsync
cmd = [
    "rsync", "-avz", "--progress",
    "-e", f"ssh -i {{key_path}} -o StrictHostKeyChecking=no",
    f"ubuntu@{{instance_ip}}:/results/",
    output_dir + "/",
]
print(f"Downloading results: {{' '.join(cmd)}}")
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
stdout, _ = proc.communicate()
print(stdout.decode() if stdout else "")
if proc.returncode == 0:
    print(f"Results downloaded to {{output_dir}}/")
else:
    print(f"Download failed (exit code {{proc.returncode}}). Check IP and SSH key.")
'''


def _gen_create_calibration_experiment(args: Dict) -> str:
    """Generate calibration grid search code."""
    parameter = args.get("parameter", "friction")
    param_range = args.get("range", [0.0, 1.0])
    num_samples = args.get("num_samples", 7)
    real_data_path = args.get("real_data_path", "")

    return f"""\
# Calibration experiment: {parameter} grid search ({num_samples} samples)
import numpy as np
import json
import omni.usd
from pxr import UsdPhysics

values = np.linspace({param_range[0]}, {param_range[1]}, {num_samples}).tolist()
real_data_path = {real_data_path!r}
parameter = {parameter!r}

results = []
for i, value in enumerate(values):
    print(f"\\n=== Trial {{i+1}}/{{len(values)}}: {{parameter}} = {{value:.3f}} ===")

    stage = omni.usd.get_context().get_stage()

    if parameter == "friction":
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.MaterialAPI):
                mat = UsdPhysics.MaterialAPI(prim)
                mat.GetDynamicFrictionAttr().Set(float(value))
    elif parameter == "damping":
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.DriveAPI):
                drive = UsdPhysics.DriveAPI(prim, "angular")
                drive.GetDampingAttr().Set(float(value))
    elif parameter == "stiffness":
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.DriveAPI):
                drive = UsdPhysics.DriveAPI(prim, "angular")
                drive.GetStiffnessAttr().Set(float(value))

    print(f"  Running sim trajectory with {{parameter}} = {{value:.3f}}...")
    # ... execute trajectory, record sim_data ...
    # Compare with real_data_path via measure_sim_real_gap
    # Replace placeholder score below with real gap score:
    score = abs(value - 0.6)  # placeholder

    results.append({{"value": value, "gap_score": score}})

best = min(results, key=lambda r: r["gap_score"])
print(f"\\n✓ Best {{parameter}} value: {{best['value']:.3f}} (gap score: {{best['gap_score']:.4f}})")
print(json.dumps(results, indent=2))
"""


def _gen_eval_harness(args: Dict) -> str:
    """Generate a reproducible RL evaluation script."""
    task_name = args["task_name"]
    num_episodes = int(args.get("num_episodes", 100))
    output_dir = args.get("output_dir") or f"workspace/eval/{task_name}"
    checkpoint_path = args.get("checkpoint_path", "")
    record_video = bool(args.get("record_video", False))
    max_steps = int(args.get("max_steps_per_episode", 1000))

    # Use repr() so user-supplied paths get safely quoted in the generated code.
    return f'''"""Evaluation harness for {task_name}.
Auto-generated by Isaac Assist (Phase 7A Addendum).
Runs {num_episodes} deterministic rollouts and saves per-episode metrics.
"""
import json
import os
from pathlib import Path

import gymnasium as gym

TASK_NAME = {task_name!r}
NUM_EPISODES = {num_episodes}
OUTPUT_DIR = Path({output_dir!r})
CHECKPOINT_PATH = {checkpoint_path!r}
RECORD_VIDEO = {record_video}
MAX_STEPS_PER_EPISODE = {max_steps}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _load_policy(checkpoint_path: str):
    """Load a trained RL policy from a checkpoint, or return a random fallback."""
    if not checkpoint_path or not os.path.exists(checkpoint_path):
        print(f"[eval] No checkpoint at {{checkpoint_path!r}} — using random policy")
        return None
    try:
        import torch
        state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        return state.get("model_state_dict", state)
    except Exception as exc:
        print(f"[eval] Failed to load checkpoint: {{exc}} — falling back to random")
        return None


def main() -> None:
    env = gym.make(TASK_NAME)
    if RECORD_VIDEO:
        from gymnasium.wrappers import RecordVideo
        env = RecordVideo(env, video_folder=str(OUTPUT_DIR / "videos"))

    policy = _load_policy(CHECKPOINT_PATH)

    results = []
    for episode in range(NUM_EPISODES):
        obs, info = env.reset(seed=episode)
        episode_reward = 0.0
        terminated = False
        truncated = False
        step = 0
        while not (terminated or truncated) and step < MAX_STEPS_PER_EPISODE:
            if policy is None:
                action = env.action_space.sample()
            else:
                # Placeholder forward pass — replace with your actor module.
                action = env.action_space.sample()
            obs, reward, terminated, truncated, info = env.step(action)
            episode_reward += float(reward)
            step += 1
        results.append({{
            "episode": episode,
            "reward": episode_reward,
            "success": bool(info.get("is_success", terminated and not truncated)),
            "length": step,
        }})
        print(f"[eval] ep {{episode + 1}}/{{NUM_EPISODES}} reward={{episode_reward:.3f}} "
              f"success={{results[-1]['success']}} len={{step}}")

    out_file = OUTPUT_DIR / "eval_results.json"
    out_file.write_text(json.dumps({{
        "task_name": TASK_NAME,
        "num_episodes": NUM_EPISODES,
        "checkpoint_path": CHECKPOINT_PATH,
        "results": results,
        "summary": {{
            "mean_reward": sum(r["reward"] for r in results) / max(len(results), 1),
            "success_rate": sum(1 for r in results if r["success"]) / max(len(results), 1),
            "mean_length": sum(r["length"] for r in results) / max(len(results), 1),
        }},
    }}, indent=2))
    print(f"[eval] wrote {{out_file}}")
    env.close()


if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Phase 7 wave 5 — training data-handlers (analyze + eureka + finetune + env + reward)


async def _handle_create_isaaclab_env(args: Dict) -> Dict:
    """Generate an IsaacLab env scaffold — returns config as data for the LLM to refine."""
    from ..tool_executor import _RL_TASK_TEMPLATES, _generate_isaaclab_env_code
    task_name = args["task_name"]
    robot_path = args["robot_path"]
    task_type = args.get("task_type", "manipulation")
    num_envs = args.get("num_envs", 64)
    env_spacing = args.get("env_spacing", 2.0)
    reward_terms = args.get("reward_terms")

    template = _RL_TASK_TEMPLATES.get(task_type, _RL_TASK_TEMPLATES["custom"])
    if reward_terms:
        template = {**template, "rewards": reward_terms}

    env_config = {
        "task_name": task_name,
        "robot_path": robot_path,
        "task_type": task_type,
        "num_envs": num_envs,
        "env_spacing": env_spacing,
        "observation_space": template["obs"],
        "action_space": template["actions"],
        "reward_terms": template["rewards"],
        "episode_length": 500,
        "decimation": 2,
        "physics_dt": 1.0 / 120.0,
    }

    # Generate the Python env class code
    env_code = _generate_isaaclab_env_code(env_config)

    return {
        "type": "isaaclab_env",
        "task_name": task_name,
        "config": env_config,
        "generated_code": env_code,
        "instructions": (
            f"IsaacLab env '{task_name}' scaffolded with {num_envs} parallel envs. "
            f"Observations: {template['obs']}. Actions: {template['actions']}. "
            f"Rewards: {template['rewards']}. "
            "You can now call launch_training to start training, or refine the config."
        ),
    }


async def _handle_generate_reward(args: Dict) -> Dict:
    """Generate Eureka reward configuration and initial prompt for a DirectRLEnv."""
    from pathlib import Path
    task_description = args["task_description"]
    env_source_path = args["env_source_path"]
    num_candidates = args.get("num_candidates", 4)
    num_iterations = args.get("num_iterations", 5)

    # Read environment source code
    env_path = Path(env_source_path)
    if env_path.exists():
        env_source = env_path.read_text()
    else:
        env_source = f"# [File not found: {env_source_path}]\n# Provide the DirectRLEnv source code manually."

    # Validate it's a DirectRLEnv (not ManagerBasedRLEnv)
    if "ManagerBasedRLEnv" in env_source:
        return {
            "error": "Eureka reward generation only works with DirectRLEnv, not ManagerBasedRLEnv. "
                     "DirectRLEnv exposes compute_reward() which Eureka can override.",
        }

    # Build the initial reward generation prompt
    initial_prompt = f"""You are a reward function engineer for reinforcement learning.

Task description: {task_description}

Environment source code:
```python
{env_source}
```

Generate {num_candidates} diverse reward function candidates.
Each candidate must:
1. Be a standalone Python function: def compute_reward(self) -> torch.Tensor
2. Use only tensors available in self (observations, actions, targets, etc.)
3. Return a scalar reward tensor of shape (num_envs,)
4. Include per-component breakdown as a dict for analysis
5. Avoid sparse rewards — use dense, shaped rewards

Return each candidate as a separate code block.
"""

    eureka_config = {
        "task_description": task_description,
        "env_source_path": env_source_path,
        "num_candidates": num_candidates,
        "num_iterations": num_iterations,
        "env_type": "DirectRLEnv",
        "initial_prompt": initial_prompt,
        "env_source_included": env_path.exists(),
    }

    return eureka_config


async def _handle_iterate_reward(args: Dict) -> Dict:
    """Generate a mutation prompt for the next Eureka iteration."""
    from ..tool_executor import _build_mutation_prompt
    prev_reward_code = args["prev_reward_code"]
    metrics = args["metrics"]
    user_feedback = args.get("user_feedback")

    mutation_prompt = _build_mutation_prompt(prev_reward_code, metrics, user_feedback)

    return {
        "mutation_prompt": mutation_prompt,
        "prev_fitness": metrics.get("fitness", "N/A"),
        "prev_success_rate": metrics.get("task_success_rate", "N/A"),
        "components_analyzed": list(metrics.get("components", {}).keys()),
        "has_user_feedback": user_feedback is not None,
    }


async def _handle_eureka_status(args: Dict) -> Dict:
    """Return current status of a Eureka optimization run."""
    from ..tool_executor import _eureka_runs
    run_id = args["run_id"]

    if run_id in _eureka_runs:
        run = _eureka_runs[run_id]
        return {
            "run_id": run_id,
            "status": run.get("status", "unknown"),
            "current_iteration": run.get("current_iteration", 0),
            "total_iterations": run.get("total_iterations", 0),
            "candidates_evaluated": run.get("candidates_evaluated", 0),
            "best_fitness": run.get("best_fitness", 0.0),
            "best_reward_code": run.get("best_reward_code"),
        }

    return {
        "run_id": run_id,
        "status": "not_found",
        "message": f"No Eureka run found with ID '{run_id}'. Start one with generate_reward first.",
    }


async def _handle_load_groot_policy(args: Dict) -> Dict:
    """Return download/launch commands for GR00T N1 policy server."""
    from ..tool_executor import _GROOT_EMBODIMENTS
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    robot_path = args["robot_path"]
    embodiment_key = args.get("embodiment", "custom")

    embodiment = _GROOT_EMBODIMENTS.get(embodiment_key, _GROOT_EMBODIMENTS["custom"])

    # VRAM check — estimate based on model size
    estimated_vram = embodiment.get("vram_gb", 24)

    return {
        "model_id": model_id,
        "robot_path": robot_path,
        "embodiment": embodiment_key,
        "embodiment_config": embodiment,
        "download_command": (
            f"from huggingface_hub import snapshot_download; "
            f"snapshot_download('{model_id}', local_dir='workspace/groot_models/{model_id.split('/')[-1]}')"
        ),
        "launch_command": (
            f"python -m gr00t.deploy.policy_server "
            f"--model-path workspace/groot_models/{model_id.split('/')[-1]} "
            f"--embodiment {embodiment_key} "
            f"--port 50051"
        ),
        "vram_required_gb": estimated_vram,
        "vram_check": "ok" if estimated_vram <= 24 else "insufficient",
        "error": (
            f"Insufficient VRAM: GR00T N1 requires >= 24 GB VRAM. "
            f"Consider using NVIDIA Cloud (brev.dev/nvidia) or a multi-GPU setup."
        ) if estimated_vram > 24 else None,
        "instructions": (
            f"1. Download model: {model_id}\n"
            f"2. Launch policy server on port 50051\n"
            f"3. Robot at {robot_path} will connect via gRPC\n"
            f"4. Embodiment: {embodiment_key} ({embodiment['description']})"
        ),
    }


async def _handle_compare_policies(args: Dict) -> Dict:
    """Format a comparison table from multiple GR00T policy evaluation results."""
    results = args.get("results", [])

    if not results:
        return {
            "comparison_table": "No results to compare.",
            "entries": [],
            "count": 0,
        }

    # Determine all metric columns
    metric_cols = set()
    for r in results:
        tm = r.get("task_metrics", {})
        metric_cols.update(tm.keys())
    metric_cols = sorted(metric_cols)

    # Build comparison entries
    entries = []
    for r in results:
        entry = {
            "policy_name": r.get("policy_name", "unnamed"),
            "model_id": r.get("model_id", "N/A"),
            "success_rate": r.get("success_rate", 0.0),
            "training_data_size": r.get("training_data_size", "N/A"),
            "observation_type": r.get("observation_type", "N/A"),
        }
        for col in metric_cols:
            entry[col] = r.get("task_metrics", {}).get(col, "N/A")
        entries.append(entry)

    # Sort by success_rate descending
    entries.sort(key=lambda e: -e["success_rate"])

    # Build formatted table
    header_cols = ["Policy", "Model", "Success Rate", "Train Data", "Obs Type"]
    header_cols.extend(metric_cols)

    rows = []
    for e in entries:
        row = [
            e["policy_name"],
            e["model_id"],
            f"{e['success_rate']:.1%}",
            e["training_data_size"],
            e["observation_type"],
        ]
        for col in metric_cols:
            val = e.get(col, "N/A")
            if isinstance(val, float):
                row.append(f"{val:.3f}")
            else:
                row.append(str(val))
        rows.append(row)

    # Calculate column widths
    col_widths = [len(h) for h in header_cols]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    # Format table
    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(header_cols, col_widths)) + " |"
    table_lines = [sep, header_line, sep]
    for row in rows:
        table_lines.append("| " + " | ".join(v.ljust(w) for v, w in zip(row, col_widths)) + " |")
    table_lines.append(sep)

    return {
        "comparison_table": "\n".join(table_lines),
        "entries": entries,
        "count": len(entries),
        "metric_columns": metric_cols,
        "dimensions": [
            "zero-shot generalization (success_rate without task-specific training)",
            "single-task performance (success_rate with fine-tuning)",
            "training data needed (training_data_size)",
            "observation type (observation_type: rgb, rgb+proprio, proprio)",
        ],
    }


async def _handle_export_finetune_data(args: Dict) -> Dict:
    """Export recorded turns to a provider-specific fine-tuning format."""
    from ..tool_executor import _turn_recorder
    fmt = args["format"]
    min_quality = args.get("min_quality", "approved_successful")
    output_path = args.get("output_path")
    return _turn_recorder.export(
        fmt=fmt,
        min_quality=min_quality,
        output_path=output_path,
    )


async def _handle_finetune_stats(args: Dict) -> Dict:
    """Return aggregate statistics about recorded fine-tuning data."""
    from ..tool_executor import _turn_recorder
    return _turn_recorder.get_stats()


async def _handle_analyze_randomization(args: Dict) -> Dict:
    """Analyze domain randomization parameter distributions from an SDG run.

    Returns per-parameter statistics and flags near-constant or collapsed
    distributions that indicate DR misconfiguration.
    """
    from .. import kit_tools
    num_samples = args.get("num_samples", 50)

    code = f"""\
import json, os, glob, random
import numpy as np

output_dirs = glob.glob('/tmp/sdg_output*') + glob.glob('workspace/sdg_output*')
if not output_dirs:
    print(json.dumps({{"error": "No SDG output directories found"}}))
else:
    out_dir = sorted(output_dirs)[-1]

    # Look for DR log / randomization parameter files
    dr_files = glob.glob(os.path.join(out_dir, '**', '*random*'), recursive=True)
    dr_files += glob.glob(os.path.join(out_dir, '**', '*param*'), recursive=True)
    dr_files += glob.glob(os.path.join(out_dir, '**', '*.json'), recursive=True)
    dr_files = list(set(dr_files))
    samples = dr_files[:{num_samples}] if len(dr_files) <= {num_samples} else random.sample(dr_files, {num_samples})

    param_values = {{}}  # param_name -> list of values

    for f in samples:
        try:
            data = json.loads(open(f).read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        params = data.get('randomization_params') or data.get('params') or data.get('dr_params') or {{}}
        if isinstance(params, dict):
            for k, v in params.items():
                if isinstance(v, (int, float)):
                    param_values.setdefault(k, []).append(v)
                elif isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
                    for i, x in enumerate(v):
                        param_values.setdefault(f"{{k}}[{{i}}]", []).append(x)

    stats = {{}}
    warnings = []
    for pname, vals in param_values.items():
        arr = np.array(vals, dtype=float)
        s = {{
            "min": float(arr.min()),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
            "std": float(arr.std()),
            "count": len(vals),
        }}
        stats[pname] = s

        # Flag near-constant distributions
        if s["std"] < 1e-6 and s["count"] > 5:
            warnings.append({{
                "param": pname,
                "warning": "near_constant",
                "detail": f"{{s['count']}} samples all ~{{s['mean']:.4f}} — DR may be misconfigured",
            }})
        # Flag extremely narrow range
        range_val = s["max"] - s["min"]
        if range_val > 0 and s["std"] / range_val < 0.01 and s["count"] > 10:
            warnings.append({{
                "param": pname,
                "warning": "narrow_range",
                "detail": f"std/range = {{s['std']/range_val:.4f}} — 99%+ values are the same angle/position",
            }})

    print(json.dumps({{
        "samples_analyzed": len(samples),
        "parameters": stats,
        "warnings": warnings,
        "total_params": len(stats),
    }}))
"""
    result = await kit_tools.queue_exec_patch(code, f"Analyze DR randomization ({num_samples} samples)")
    return {"type": "data", "queued": result.get("queued", False)}


async def _handle_apply_dr_preset(args: Dict) -> Dict:
    """Look up a DR preset by name."""
    from ..tool_executor import _DR_PRESETS
    preset = (args.get("preset") or "").strip().lower()
    if not preset:
        return {"error": "preset is required", "available": sorted(_DR_PRESETS.keys())}
    if preset not in _DR_PRESETS:
        return {
            "error": f"unknown preset '{preset}'",
            "available": sorted(_DR_PRESETS.keys()),
        }
    cfg = _DR_PRESETS[preset]
    return {
        "preset": preset,
        "description": cfg.get("description", ""),
        "parameters": {k: v for k, v in cfg.items() if k != "description"},
        "message": f"Loaded DR preset '{preset}' — feed `parameters` into configure_correlated_dr or your IsaacLab EventManager.",
    }


async def _handle_detect_ood(args: Dict) -> Dict:
    """Detect OOD via action variance/autocorrelation (Tier 1) or higher tiers."""
    tier = args.get("tier", 1)

    if tier == 1:
        action_seq = args.get("action_sequence", [])
        if not action_seq or len(action_seq) < 2:
            return {"error": "Tier 1 requires action_sequence with >= 2 entries"}

        n_dims = len(action_seq[0]) if isinstance(action_seq[0], (list, tuple)) else 1
        variances = []
        autocorrs = []
        for j in range(n_dims):
            values = [step[j] if isinstance(step, (list, tuple)) else step for step in action_seq]
            mean = sum(values) / len(values)
            var = sum((v - mean) ** 2 for v in values) / len(values)
            variances.append(var)
            if len(values) >= 3:
                v0, v1 = values[:-1], values[1:]
                m0, m1 = sum(v0) / len(v0), sum(v1) / len(v1)
                num = sum((a - m0) * (b - m1) for a, b in zip(v0, v1))
                den0 = sum((a - m0) ** 2 for a in v0) ** 0.5
                den1 = sum((b - m1) ** 2 for b in v1) ** 0.5
                autocorrs.append(num / max(den0 * den1, 1e-10))
            else:
                autocorrs.append(0.0)

        max_var = max(variances)
        min_autocorr = min(autocorrs) if autocorrs else 1.0
        is_ood = max_var > 1.0 or min_autocorr < 0.3
        return {
            "tier": 1,
            "is_ood": is_ood,
            "max_action_variance": round(max_var, 4),
            "min_autocorrelation": round(min_autocorr, 4),
            "thresholds": {"variance": 1.0, "autocorr": 0.3},
            "warning": "Action instability detected — policy may be extrapolating" if is_ood else None,
        }
    elif tier == 2:
        return {
            "tier": 2,
            "method": "4-sample DiT variance",
            "overhead_ms": 15,
            "instructions": "Run 4 forward passes with dropout, compute action variance",
            "checkpoint_needed": args.get("checkpoint_path"),
        }
    elif tier == 3:
        return {
            "tier": 3,
            "method": "Mahalanobis distance on 12th-layer embeddings",
            "instructions": "Pre-compute mean+covariance over training data; inference distance > threshold = OOD",
            "calibration_path": args.get("calibration_path"),
        }
    else:
        return {"error": f"Invalid tier {tier} — must be 1, 2, or 3"}


async def _handle_analyze_checkpoint(args: Dict) -> Dict:
    """Analyze GR00T checkpoint: embodiment, drift, action stats, risk."""
    from pathlib import Path
    checkpoint_path = args.get("checkpoint_path", "")
    base_path = args.get("base_model_path")

    if not Path(checkpoint_path).exists():
        return {"error": f"Checkpoint not found: {checkpoint_path}"}

    analysis = {
        "checkpoint_path": checkpoint_path,
        "instructions": [
            "1. Load checkpoint with torch.load(weights_only=False)",
            "2. Read metadata: embodiment, training_steps from checkpoint['config']",
            "3. If base_model provided, compute per-layer Frobenius norm",
            "4. Aggregate action statistics from training logs",
        ],
        "expected_structure": {
            "embodiment": "UNITREE_G1 / LIBERO_PANDA / OXE_WIDOWX / CUSTOM",
            "training_steps": "int",
            "layer_drift": {
                "vision_encoder": "low (<0.05) = frozen, good",
                "dit_layers": "high (>0.3) = well-targeted",
                "adapter_mlps": "high (>0.3) = expected",
                "language_model": "near-zero (<0.01) = frozen, good",
            },
            "action_statistics": {
                "mean_per_joint": "[float, ...]",
                "std_per_joint": "[float, ...]",
            },
        },
    }
    if base_path:
        analysis["compare_against"] = base_path
    return analysis


async def _handle_get_training_status(args: Dict) -> Dict:
    """Read TensorBoard event files + subprocess state for an RL run."""
    import os
    from pathlib import Path
    from ..tool_executor import _WORKSPACE

    run_id = args["run_id"]
    log_dir = args.get("log_dir") or str(_WORKSPACE / "rl_checkpoints" / run_id)

    log_path = Path(log_dir)
    result: Dict[str, Any] = {
        "run_id": run_id,
        "log_dir": str(log_path),
        "state": "unknown",
        "step": None,
        "total_steps": None,
        "latest_reward": None,
        "events_found": 0,
    }

    if not log_path.exists():
        result["state"] = "missing"
        result["error"] = f"log dir does not exist: {log_path}"
        return result

    # Look for TensorBoard event files (events.out.tfevents.*)
    event_files = sorted(log_path.glob("**/events.out.tfevents.*"))
    result["events_found"] = len(event_files)

    if not event_files:
        result["state"] = "starting"
        return result

    # Try to parse the latest event file. tensorboard isn't a hard dep, so we
    # fall back gracefully on import failure.
    latest = event_files[-1]
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator  # type: ignore
        acc = EventAccumulator(str(latest), size_guidance={"scalars": 0})
        acc.Reload()
        scalars = acc.Tags().get("scalars", [])
        # Prefer common reward / step tag names
        for tag in ("reward", "Train/reward", "train/reward",
                    "rollout/ep_rew_mean", "Episode_Reward/Mean"):
            if tag in scalars:
                events = acc.Scalars(tag)
                if events:
                    result["latest_reward"] = events[-1].value
                    result["step"] = events[-1].step
                    break
        if result["step"] is None and scalars:
            events = acc.Scalars(scalars[0])
            if events:
                result["step"] = events[-1].step
    except ImportError:
        result["note"] = "tensorboard not installed — install with `pip install tensorboard`"
    except Exception as exc:
        result["error"] = f"failed to parse event file: {exc}"

    # Subprocess state via the launcher's pid file (if launch_training wrote one)
    pid_file = log_path / "launcher.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            # Cheap liveness check: send signal 0
            try:
                os.kill(pid, 0)
                result["state"] = "running"
                result["pid"] = pid
            except ProcessLookupError:
                result["state"] = "finished"
                result["pid"] = pid
            except PermissionError:
                # Process exists but owned by another user — still treat as running
                result["state"] = "running"
                result["pid"] = pid
        except Exception as exc:
            result["state"] = "unknown"
            result["error"] = f"could not read launcher.pid: {exc}"
    elif result["events_found"] > 0:
        result["state"] = "running"

    return result


async def _handle_get_env_observations(args: Dict) -> Dict:
    """Read the observation tensor for one env in a running IsaacLab worker."""
    import time
    from ..tool_executor import _resolve_run_id, _validate_env_id, _query_run_ipc
    t0 = time.perf_counter()
    env_id = args.get("env_id")
    run_id_arg = args.get("run_id")

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    err = _validate_env_id(env_id, entry.get("num_envs", 0))
    if err:
        return {"error": err, "run_id": run_id, "num_envs": entry.get("num_envs")}

    try:
        ipc_result = await _query_run_ipc(entry, {"op": "get_observations", "env_id": env_id})
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id, "env_id": env_id}

    return {
        "run_id": run_id,
        "env_id": env_id,
        "step": ipc_result.get("step", entry.get("last_known_step", 0)),
        "episode_step": ipc_result.get("episode_step", 0),
        "observations": ipc_result.get("observations", {}),
        "dtype": ipc_result.get("dtype", "float32"),
        "shape": ipc_result.get("shape", []),
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }


async def _handle_get_env_rewards(args: Dict) -> Dict:
    """Read per-term reward breakdown for one env at the current step."""
    import time
    from ..tool_executor import _resolve_run_id, _validate_env_id, _query_run_ipc
    t0 = time.perf_counter()
    env_id = args.get("env_id")
    run_id_arg = args.get("run_id")

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    err = _validate_env_id(env_id, entry.get("num_envs", 0))
    if err:
        return {"error": err, "run_id": run_id, "num_envs": entry.get("num_envs")}

    try:
        ipc_result = await _query_run_ipc(entry, {"op": "get_rewards", "env_id": env_id})
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id, "env_id": env_id}

    terms = ipc_result.get("terms", [])
    total = ipc_result.get("total_reward")
    if total is None:
        total = sum(t.get("weighted", 0.0) for t in terms)

    return {
        "run_id": run_id,
        "env_id": env_id,
        "step": ipc_result.get("step", entry.get("last_known_step", 0)),
        "total_reward": total,
        "terms": terms,
        "episode_return": ipc_result.get("episode_return", 0.0),
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }


async def _handle_get_env_termination_state(args: Dict) -> Dict:
    """Report termination flags (success / timeout / crashed / done) for one env."""
    import time
    from ..tool_executor import _resolve_run_id, _validate_env_id, _query_run_ipc
    t0 = time.perf_counter()
    env_id = args.get("env_id")
    run_id_arg = args.get("run_id")

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    err = _validate_env_id(env_id, entry.get("num_envs", 0))
    if err:
        return {"error": err, "run_id": run_id, "num_envs": entry.get("num_envs")}

    try:
        ipc_result = await _query_run_ipc(entry, {"op": "get_termination", "env_id": env_id})
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id, "env_id": env_id}

    term_terms = ipc_result.get("termination_terms", {}) or {}
    success = bool(ipc_result.get("success", term_terms.get("success", False)))
    timeout = bool(ipc_result.get("timeout", term_terms.get("time_out", False)))
    crashed = bool(ipc_result.get("crashed", any(
        v for k, v in term_terms.items()
        if k not in ("success", "time_out") and isinstance(v, bool) and v
    )))
    done = bool(ipc_result.get("done", success or timeout or crashed))

    return {
        "run_id": run_id,
        "env_id": env_id,
        "done": done,
        "success": success,
        "timeout": timeout,
        "crashed": crashed,
        "termination_terms": term_terms,
        "episode_step": ipc_result.get("episode_step", 0),
        "max_episode_steps": ipc_result.get("max_episode_steps", entry.get("max_episode_steps", 0)),
        "last_reset_step": ipc_result.get("last_reset_step", 0),
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }


async def _handle_checkpoint_training(args: Dict) -> Dict:
    """Trigger an out-of-band checkpoint save on a running training subprocess."""
    import time
    from ..tool_executor import _resolve_run_id, _query_run_ipc
    t0 = time.perf_counter()
    run_id_arg = args.get("run_id")
    include_replay = bool(args.get("include_replay_buffer", False))
    tag = args.get("tag", "manual") or "manual"

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    state = entry.get("state", "unknown")
    if state not in ("running", "paused"):
        return {
            "error": f"Cannot checkpoint run in state '{state}'. Run must be running or paused.",
            "run_id": run_id,
            "state": state,
        }

    try:
        ipc_result = await _query_run_ipc(entry, {
            "op": "checkpoint",
            "include_replay_buffer": include_replay,
            "tag": tag,
        })
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id}

    # Without a concrete checkpoint_path the IPC ack can't be trusted as a
    # real save — training subprocesses sometimes ack the op but fail to
    # write the file (disk full, permission denied, policy-state race).
    # Return an explicit error rather than success with checkpoint_path=""
    # so the agent doesn't narrate "saved to '' ".
    ckpt_path = ipc_result.get("checkpoint_path") or ""
    if not ckpt_path:
        return {
            "error": (
                "IPC ack did not include a checkpoint_path — training "
                "subprocess may have failed to write the file (disk full, "
                "permission denied, or policy-state race)."
            ),
            "run_id": run_id,
            "ipc_result": ipc_result,
        }
    return {
        "run_id": run_id,
        "checkpoint_path": ckpt_path,
        "step": ipc_result.get("step", entry.get("last_known_step", 0)),
        "iteration": ipc_result.get("iteration", entry.get("last_known_iteration", 0)),
        "size_bytes": ipc_result.get("size_bytes", 0),
        "includes_replay_buffer": bool(ipc_result.get("includes_replay_buffer", include_replay)),
        "save_duration_ms": ipc_result.get("save_duration_ms", 0.0),
        "tag": tag,
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 6 — dispatch lines in tool_executor.py still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
