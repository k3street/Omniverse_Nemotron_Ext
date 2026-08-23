# NVIDIA Video to Data integration

Isaac Assist integrates with NVIDIA's external
[Video to Data (V2D)](https://github.com/nvidia-isaac/video_to_data) pipeline
without installing its CUDA, PyTorch, or model dependencies into the sidecar.
V2D owns video ingestion, reconstruction containers, and robotic-grounding
containers; Isaac Assist validates and launches its documented entrypoints.

## Install

Install V2D separately and follow its component-specific setup instructions.
Then configure the sidecar:

```bash
export V2D_ROOT=/absolute/path/to/video_to_data
export V2D_PYTHON="$V2D_ROOT/video_ingestion_agent/.venv/bin/python"
```

`v2d_status` reports whether all three component directories and the configured
Python executable are available.

## Safety and execution

All V2D tools default to `dry_run=true`, returning an argv array, working
directory, and expected artifact locations without starting a process. Live
execution requires both `dry_run=false` and:

```bash
export ISAAC_ASSIST_V2D_EXECUTE=1
```

Commands use direct subprocess argv invocation, never a shell. Set
`V2D_TIMEOUT_SECONDS` to change the default one-hour timeout. Output returned to
the agent is tail-capped at 20,000 characters; full artifacts remain under the
chosen V2D output directory.

## Tools

- `v2d_ingest_video`: video to action segments, entity graph, and embeddings.
- `v2d_retrieve_clips`: natural-language retrieval over an ingestion database.
- `v2d_reconstruct_depth`: containerized MoGe depth and camera intrinsics.
- `v2d_ground_motion`: dataset preprocessing through the robotic-grounding Docker pipeline.

V2D's reconstruction and grounding stages require Docker, NVIDIA Container
Toolkit, compatible GPU hardware, model weights, and datasets as documented by
the upstream project. These are deliberately not Python package dependencies of
Isaac Assist.
