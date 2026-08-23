"""Command-line entry point for the Isaac Assist service."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Isaac Assist service")
    parser.add_argument("--mode", choices=("local", "google", "anthropic", "openai", "grok"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.mode:
        os.environ["LLM_MODE"] = args.mode

    import uvicorn

    uvicorn.run(
        "isaac_assist_service.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
