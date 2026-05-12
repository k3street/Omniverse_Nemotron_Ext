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
