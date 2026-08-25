"""Tool handlers for isolated NVIDIA Isaac GR00T N1.7 execution."""
from __future__ import annotations

from typing import Any, Callable, Dict

from ....integrations.groot_n17 import GrootN17Adapter


async def _status(args: Dict[str, Any]) -> Dict[str, Any]:
    return GrootN17Adapter().status()


async def _infer(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter = GrootN17Adapter()
    command = adapter.infer(
        dataset_path=args["dataset_path"],
        embodiment_tag=args["embodiment_tag"],
        model_path=args.get("model_path", "nvidia/GR00T-N1.7-3B"),
        trajectory_ids=args.get("trajectory_ids"),
        inference_mode=args.get("inference_mode", "pytorch"),
        execution_horizon=int(args.get("execution_horizon", 8)),
    )
    return await adapter.run(command, dry_run=bool(args.get("dry_run", True)))


async def _serve(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter = GrootN17Adapter()
    command = adapter.serve(
        embodiment_tag=args["embodiment_tag"],
        model_path=args.get("model_path", "nvidia/GR00T-N1.7-3B"),
        port=int(args.get("port", 5555)),
        device=args.get("device", "cuda:0"),
    )
    return await adapter.run(command, dry_run=bool(args.get("dry_run", True)))


async def _finetune(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter = GrootN17Adapter()
    command = adapter.finetune(
        dataset_path=args["dataset_path"],
        modality_config_path=args["modality_config_path"],
        output_dir=args["output_dir"],
        embodiment_tag=args.get("embodiment_tag", "NEW_EMBODIMENT"),
        model_path=args.get("model_path", "nvidia/GR00T-N1.7-3B"),
        max_steps=int(args.get("max_steps", 2000)),
        global_batch_size=int(args.get("global_batch_size", 32)),
    )
    return await adapter.run(command, dry_run=bool(args.get("dry_run", True)))


def register(data: Dict[str, Callable[..., Any]], codegen: Dict[str, Callable[..., Any]]) -> None:
    data["groot_n17_status"] = _status
    data["groot_n17_infer"] = _infer
    data["groot_n17_serve"] = _serve
    data["groot_n17_finetune"] = _finetune
