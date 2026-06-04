"""
viewport_capture.py
--------------------
Captures the active Isaac Sim viewport to a PNG and returns it as a
base64-encoded string suitable for sending to a vision LLM.

Must be called from inside the Kit process (the extension).
"""
from __future__ import annotations
import asyncio
import base64
import os
import tempfile
import carb
from typing import Dict, Any


async def capture_viewport_png(max_dim: int = 1280) -> Dict[str, Any]:
    """
    Captures the current viewport and returns:
        {image_b64: str, width: int, height: int, path: str}

    max_dim: largest dimension to scale to (preserves aspect ratio).
    Downsampling keeps vision LLM tokens reasonable.
    """
    try:
        from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file

        viewport = get_active_viewport()
        if viewport is None:
            return {"error": "No active viewport found"}

        # Write to a temp file
        tmp_path = os.path.join(tempfile.gettempdir(), "isaac_assist_capture.png")
        capture_helper = capture_viewport_to_file(viewport, file_path=tmp_path)

        # Wait up to 5 seconds for the render to land
        for _ in range(50):
            await asyncio.sleep(0.1)
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                break
        else:
            return {"error": "Viewport capture timed out"}

        # Optional downscale with Pillow (available in Kit's Python)
        width, height = _get_image_dims(tmp_path)
        if max(width, height) > max_dim:
            tmp_path = _downscale(tmp_path, max_dim)
            width, height = _get_image_dims(tmp_path)

        with open(tmp_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        return {
            "image_b64": image_b64,
            "width": width,
            "height": height,
            "path": tmp_path,
        }

    except Exception as e:
        carb.log_warn(f"[IsaacAssist] viewport_capture error: {e}")
        import traceback
        carb.log_warn(traceback.format_exc())
        return {"error": str(e)}


def _get_image_dims(path: str):
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return 0, 0


def _downscale(path: str, max_dim: int) -> str:
    try:
        from PIL import Image
        with Image.open(path) as img:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            out_path = path.replace(".png", "_scaled.png")
            img.save(out_path)
            return out_path
    except Exception:
        return path  # return original if PIL unavailable
