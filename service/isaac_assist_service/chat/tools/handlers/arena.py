"""Arena handlers — target scope: scenario arena creation, arena
variant generation, benchmark runs against the arena library.

Phase 6 wave 9 — arena code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-8.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict
from service.isaac_assist_service.observability.handler_telemetry import with_telemetry


# ---------------------------------------------------------------------------
# Arena-local constants + helpers (Phase 8 wave 1, 2026-05-13)
# Migrated from tool_executor.py:106 (_ARENA_SCENE_MAP) and :3300
# (_arena_env_id). Used only by this module — kept theme-local rather
# than promoted to _shared.py.

_ARENA_SCENE_MAP = {
    "tabletop_pick_and_place": "isaaclab_tasks.envs.arena.scenes.tabletop",
    "kitchen": "isaaclab_tasks.envs.arena.scenes.kitchen",
    "galileo": "isaaclab_tasks.envs.arena.scenes.galileo",
    "custom": None,
}


def _arena_env_id(scene_type: str, robot_asset: str, task: str) -> str:
    """Generate a gymnasium-style env_id from arena components."""
    scene_part = scene_type.replace("_", " ").title().replace(" ", "")
    robot_part = robot_asset.split("/")[-1].replace(".usd", "").replace("_", " ").title().replace(" ", "")
    task_part = task.replace("_", " ").title().replace(" ", "")
    return f"Arena-{scene_part}{task_part}-{robot_part}-v0"


# ---------------------------------------------------------------------------
# Phase 6 wave 9 — arena creation + variants + benchmark


def _gen_create_arena(args: Dict) -> str:
    """Generate code to compose and register an Isaac Lab Arena environment.

    Args:
        args: Dict containing:
            - scene_type (str): Arena scene identifier (e.g. "tabletop").
            - robot_asset (str): Nucleus asset path for the robot.
            - task (str): Task identifier (e.g. "pick_and_place").
            - num_envs (int, optional): Number of parallel environments (default 64).
            - env_spacing (float, optional): Grid spacing in metres (default 2.5).

    Returns:
        Python source string for execution inside Kit.
    """
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
    """Generate code to create a robot-swapped variant of an existing Arena env.

    Args:
        args: Dict containing:
            - base_env_id (str): Gymnasium ID of the base environment.
            - robot_asset (str): Nucleus asset path for the replacement robot.

    Returns:
        Python source string for execution inside Kit.
    """
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
    """Generate code to launch an Arena benchmark subprocess and write results JSON.

    Args:
        args: Dict containing:
            - env_id (str): Gymnasium environment ID to benchmark.
            - num_episodes (int, optional): Episodes to evaluate (default 100).
            - metrics (list, optional): Metric names to collect (default success_rate + episode_length).
            - checkpoint (str, optional): Policy checkpoint path; None runs random policy.

    Returns:
        Python source string for execution inside Kit.
    """
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
# Phase 7 wave 16 — final data-handler stragglers (COMPLETES data-handler migration)


@with_telemetry
async def _handle_arena_leaderboard(args: Dict) -> Dict:
    """Format a leaderboard table from benchmark results."""
    results = args.get("results", [])

    if not results:
        return {
            "leaderboard": "No results to display.",
            "entries": [],
        }

    # Collect all unique metric keys across results
    all_metrics = set()
    for r in results:
        all_metrics.update(r.get("metrics", {}).keys())
    metric_cols = sorted(all_metrics)

    # Build leaderboard entries
    entries = []
    for i, r in enumerate(results):
        entry = {
            "rank": i + 1,
            "env_id": r.get("env_id", "unknown"),
            "robot": r.get("robot", "unknown"),
        }
        for m in metric_cols:
            entry[m] = r.get("metrics", {}).get(m, "N/A")
        entries.append(entry)

    # Sort by success_rate descending if available, else by first metric
    sort_key = "success_rate" if "success_rate" in metric_cols else (metric_cols[0] if metric_cols else None)
    if sort_key:
        entries.sort(
            key=lambda e: e.get(sort_key, 0) if isinstance(e.get(sort_key), (int, float)) else 0,
            reverse=True,
        )
        for i, e in enumerate(entries):
            e["rank"] = i + 1

    # Format as text table
    header_cols = ["Rank", "Robot", "Env ID"] + metric_cols
    rows = []
    for e in entries:
        row = [str(e["rank"]), e["robot"], e["env_id"]]
        for m in metric_cols:
            val = e.get(m, "N/A")
            if isinstance(val, float):
                row.append(f"{val:.4f}")
            else:
                row.append(str(val))
        rows.append(row)

    # Calculate column widths
    col_widths = [len(h) for h in header_cols]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build formatted table
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_line = "|" + "|".join(f" {h:<{col_widths[i]}} " for i, h in enumerate(header_cols)) + "|"
    table_lines = [sep, header_line, sep]
    for row in rows:
        line = "|" + "|".join(f" {cell:<{col_widths[i]}} " for i, cell in enumerate(row)) + "|"
        table_lines.append(line)
    table_lines.append(sep)
    table_text = "\n".join(table_lines)

    return {
        "leaderboard": table_text,
        "entries": entries,
        "metric_columns": metric_cols,
        "count": len(entries),
    }


# ---------------------------------------------------------------------------
# Registration


def register(
    data: Dict[str, Callable[..., Any]],
    codegen: Dict[str, Callable[..., Any]],
) -> None:
    """Phase 9 — populate dispatch dicts with this module's handlers.

    Called by `handlers/_dispatch.py:register_handlers()` which is the
    sole dispatch entry point from `tool_executor.py`.
    """
    # Data handlers (1)
    data["arena_leaderboard"] = _handle_arena_leaderboard

    # Code-gen handlers (3)
    codegen["create_arena"] = _gen_create_arena
    codegen["create_arena_variant"] = _gen_create_arena_variant
    codegen["run_arena_benchmark"] = _gen_run_arena_benchmark

