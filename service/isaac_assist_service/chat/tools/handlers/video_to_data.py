"""Tool handlers for the optional NVIDIA Video to Data pipeline."""
from __future__ import annotations

from typing import Any, Callable, Dict

from ....integrations.video_to_data import V2DAdapter


async def _status(args: Dict[str, Any]) -> Dict[str, Any]:
    return V2DAdapter().status()


async def _ingest(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter = V2DAdapter()
    command = adapter.ingest(
        video_path=args["video_path"], output_dir=args["output_dir"],
        config_path=args["config_path"], verify=bool(args.get("verify", False)),
    )
    return await adapter.run(command, dry_run=bool(args.get("dry_run", True)))


async def _retrieve(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter = V2DAdapter()
    command = adapter.retrieve(
        query=args["query"], database_dir=args["database_dir"], config_path=args["config_path"],
    )
    return await adapter.run(command, dry_run=bool(args.get("dry_run", True)))


async def _reconstruct_depth(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter = V2DAdapter()
    command = adapter.reconstruct_depth(
        video_path=args["video_path"], output_dir=args["output_dir"], weights_path=args["weights_path"],
    )
    return await adapter.run(command, dry_run=bool(args.get("dry_run", True)))


async def _ground_motion(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter = V2DAdapter()
    command = adapter.ground_motion(
        dataset=args["dataset"], hmd_root=args["hmd_root"], mano_dir=args["mano_dir"],
        max_sequences=int(args.get("max_sequences", 2)),
    )
    return await adapter.run(command, dry_run=bool(args.get("dry_run", True)))


def register(data: Dict[str, Callable[..., Any]], codegen: Dict[str, Callable[..., Any]]) -> None:
    data["v2d_status"] = _status
    data["v2d_ingest_video"] = _ingest
    data["v2d_retrieve_clips"] = _retrieve
    data["v2d_reconstruct_depth"] = _reconstruct_depth
    data["v2d_ground_motion"] = _ground_motion
