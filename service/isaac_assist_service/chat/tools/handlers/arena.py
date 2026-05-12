"""Arena handlers — target scope: scenario arena creation, arena
variant generation, benchmark runs against the arena library.

Phase 6 wave 9 — arena code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-8.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 6 wave 9 — arena creation + variants + benchmark


def _gen_create_arena(args: Dict) -> str:
    from ..tool_executor import _ARENA_SCENE_MAP, _arena_env_id  # noqa: E402
    scene_type = args["scene_type"]
    robot_asset = args["robot_asset"]
    task = args["task"]
    num_envs = args.get("num_envs", 64)
    env_spacing = args.get("env_spacing", 2.5)

    env_id = _arena_env_id(scene_type, robot_asset, task)
    scene_module = _ARENA_SCENE_MAP.get(scene_type)

    scene_import = ""
    scene_cfg = f"'{scene_type}'"
    if scene_module:
        scene_import = f"from {scene_module} import SceneCfg"
        scene_cfg = "SceneCfg()"

    lines = [
        "# NOTE: isaaclab_tasks.envs.arena.* was never shipped in Isaac Lab.",
        "# Detect at import time and guide the caller to the actual API path.",
        "try:",
        "    from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder  # noqa: F401",
        "except ModuleNotFoundError as _e:",
        "    raise ModuleNotFoundError(",
        "        'isaaclab_tasks.envs.arena is not available in this Isaac Lab install. '",
        "        'Use isaaclab_tasks.manager_based.<domain>.<task> directly, or pick a preset '",
        "        \"from isaaclab_tasks.direct (e.g. 'cartpole', 'franka_cabinet').\"",
        "    )",
        "import gymnasium",
        "from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder",
        "from isaaclab_tasks.envs.arena.configs.embodiment import EmbodimentCfg",
        "from isaaclab_tasks.envs.arena.configs.task import TaskCfg",
    ]
    if scene_import:
        lines.append(scene_import)
    lines.extend([
        "",
        f"# Compose Arena environment: {scene_type} + {robot_asset} + {task}",
        f"scene_cfg = {scene_cfg}",
        f"embodiment_cfg = EmbodimentCfg(robot_asset='{robot_asset}')",
        f"task_cfg = TaskCfg(task='{task}')",
        "",
        "# Compile-time composition — combine scene + embodiment + task",
        "env_cfg = ArenaEnvBuilder.combine(",
        "    scene=scene_cfg,",
        "    embodiment=embodiment_cfg,",
        "    task=task_cfg,",
        f"    num_envs={num_envs},",
        f"    env_spacing={env_spacing},",
        ")",
        "",
        f"# Register with gymnasium",
        f"env_id = '{env_id}'",
        "gymnasium.register(",
        f"    id=env_id,",
        "    entry_point='isaaclab.envs:ManagerBasedRLEnv',",
        "    kwargs={'cfg': env_cfg},",
        ")",
        f"print(f'Arena environment registered: {{env_id}}')",
        f"print(f'  Scene: {scene_type}, Robot: {robot_asset}, Task: {task}')",
        f"print(f'  Envs: {num_envs}, Spacing: {env_spacing}m')",
    ])
    return "\n".join(lines)


def _gen_create_arena_variant(args: Dict) -> str:
    base_env_id = args["base_env_id"]
    robot_asset = args["robot_asset"]

    # Derive new env_id by replacing robot name in the base ID
    robot_part = robot_asset.split("/")[-1].replace(".usd", "").replace("_", " ").title().replace(" ", "")
    # Replace the robot part between last '-' and '-v0'
    parts = base_env_id.rsplit("-", 2)  # e.g. ['Arena-TabletopPickAndPlace', 'Franka', 'v0']
    new_env_id = f"{parts[0]}-{robot_part}-v0" if len(parts) >= 3 else f"{base_env_id}-{robot_part}"

    lines = [
        "import gymnasium",
        "from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder",
        "from isaaclab_tasks.envs.arena.configs.embodiment import EmbodimentCfg",
        "",
        f"# Create variant of '{base_env_id}' with robot '{robot_asset}'",
        f"base_env_id = '{base_env_id}'",
        f"base_spec = gymnasium.spec(base_env_id)",
        f"base_cfg = base_spec.kwargs['cfg']",
        "",
        f"# Replace embodiment config with new robot",
        f"new_embodiment = EmbodimentCfg(robot_asset='{robot_asset}')",
        "variant_cfg = ArenaEnvBuilder.combine(",
        "    scene=base_cfg.scene,",
        "    embodiment=new_embodiment,",
        "    task=base_cfg.task,",
        "    num_envs=base_cfg.scene.num_envs,",
        "    env_spacing=base_cfg.scene.env_spacing,",
        ")",
        "",
        f"variant_env_id = '{new_env_id}'",
        "gymnasium.register(",
        f"    id=variant_env_id,",
        "    entry_point='isaaclab.envs:ManagerBasedRLEnv',",
        "    kwargs={'cfg': variant_cfg},",
        ")",
        f"print(f'Arena variant registered: {{variant_env_id}}')",
        f"print(f'  Based on: {base_env_id}')",
        f"print(f'  New robot: {robot_asset}')",
    ]
    return "\n".join(lines)


def _gen_run_arena_benchmark(args: Dict) -> str:
    env_id = args["env_id"]
    num_episodes = args.get("num_episodes", 100)
    metrics = args.get("metrics", ["success_rate", "episode_length"])
    checkpoint = args.get("checkpoint")

    metrics_str = repr(metrics)

    lines = [
        "import subprocess",
        "import sys",
        "import os",
        "import json",
        "",
        f"env_id = '{env_id}'",
        f"num_episodes = {num_episodes}",
        f"metrics = {metrics_str}",
        "",
        "# Create results directory",
        f"results_dir = 'workspace/arena_benchmarks/{env_id}'",
        "os.makedirs(results_dir, exist_ok=True)",
        "results_file = os.path.join(results_dir, 'results.json')",
        "",
        "# Build benchmark command (runs as separate IsaacLab process)",
        "cmd = [",
        "    sys.executable, '-m',",
        "    'isaaclab_tasks.envs.arena.benchmark',",
        f"    '--env_id', env_id,",
        f"    '--num_episodes', str(num_episodes),",
        "    '--metrics', ','.join(metrics),",
        "    '--results_file', results_file,",
    ]
    if checkpoint:
        lines.extend([
            f"    '--checkpoint', '{checkpoint}',",
        ])
    lines.extend([
        "]",
        "",
        "print(f'Launching Arena benchmark: {env_id}')",
        f"print(f'  Episodes: {num_episodes}, Metrics: {{metrics}}')",
    ])
    if checkpoint:
        lines.append(f"print(f'  Checkpoint: {checkpoint}')")
    lines.extend([
        "",
        "proc = subprocess.Popen(",
        "    cmd,",
        "    stdout=subprocess.PIPE,",
        "    stderr=subprocess.STDOUT,",
        ")",
        "print(f'Benchmark started (PID: {proc.pid})')",
        "print(f'Results will be saved to: {results_file}')",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 6 wave 9 — dispatch lines in tool_executor.py still
    reference these names via re-import. Phase 9 swaps to register()
    being authoritative; until then this is intentionally a no-op.
    """
    return None
