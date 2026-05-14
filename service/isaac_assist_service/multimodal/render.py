"""
Server-side LayoutSpec preview renderer.

Produces PNG snapshots from a LayoutSpec for the Kit canvas-mirror panel.
Pure Python via PIL — no browser dependency. Mirror panel reloads `ui.Image`
on SSE `canvas/preview_updated` events.

Visual style follows spec §12 design tokens for the canvas surface (top-down
2D with NVIDIA-dark background + agency-tier class colors). Mirror panel
renders the COMMITTED + PROPOSED states; ghost styling for proposed objects.
"""
from __future__ import annotations

import io
import math
import logging
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .types import LayoutSpec, TypedObject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Visual tokens — mirrors spec §12.3 design tokens
# ---------------------------------------------------------------------------

CANVAS_BG = (17, 18, 20)       # #111214
GRID_MAJOR = (39, 46, 56)      # #272E38 — 1m grid
GRID_MINOR = (30, 34, 40)      # #1E2228 — 0.25m grid
ORIGIN = (118, 185, 0)         # NVIDIA green
TEXT_PRIMARY = (221, 221, 221) # #DDDDDD
TEXT_SECONDARY = (138, 142, 146)  # #8A8E92

# Agency-tier class colors per spec §12.5
CLASS_COLORS = {
    # Tier A — autonomous (cool blues)
    "franka_panda":  (90, 141, 238),   # #5A8DEE
    "ur5e":          (74, 125, 206),   # #4A7DCE
    "ur10e":         (74, 125, 206),
    "kinova_gen3":   (74, 125, 206),
    "iiwa":          (74, 125, 206),
    "jaco7":         (74, 125, 206),
    "nova_carter":   (58, 109, 174),   # #3A6DAE
    # Tier B — powered (amber/teal)
    "conveyor":      (255, 168, 0),    # #FFA800
    "camera_sensor": (0, 200, 180),    # #00C8B4
    "lidar_sensor":  (0, 200, 180),
    "station_marker":(0, 200, 180),
    # Tier C — passive (desaturated)
    "bin":           (94, 101, 113),   # #5E6571
    "cube":          (139, 115, 85),   # #8B7355
    "table":         (74, 85, 96),     # #4A5560
    "ramp":          (74, 85, 96),
    # Tier D — boundaries (neutral)
    "wall":          (52, 57, 64),
    "boundary":      (52, 57, 64),
}
DEFAULT_CLASS_COLOR = (140, 140, 140)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_layout_spec_to_png(
    spec: LayoutSpec,
    width_px: int = 800,
    height_px: int = 600,
    world_extent_m: float = 5.0,
) -> bytes:
    """Render a LayoutSpec to a PNG byte buffer.

    Args:
        spec: the LayoutSpec to render. `objects` may be None (for
            text-prompt modality) — in that case we render a placeholder.
        width_px, height_px: output canvas size in pixels.
        world_extent_m: half-width of the rendered world in meters. The
            view is centered at (0,0); world spans [-extent, +extent] on
            both axes. Auto-zooms to fit if objects exceed extent.

    Returns:
        PNG bytes ready to be written to disk or served via HTTP.
    """
    img = Image.new("RGB", (width_px, height_px), CANVAS_BG)
    draw = ImageDraw.Draw(img, "RGBA")

    # Auto-zoom to fit objects if they exceed the default extent
    if spec.objects:
        max_coord = max(
            (abs(o.position.x) + o.size.w / 2 for o in spec.objects),
            default=0.0,
        )
        max_coord = max(max_coord, max(
            (abs(o.position.y) + o.size.h / 2 for o in spec.objects),
            default=0.0,
        ))
        if max_coord > world_extent_m * 0.85:
            world_extent_m = max_coord / 0.85

    # World→pixel transform
    px_per_m = min(width_px, height_px) / (2.0 * world_extent_m)
    cx = width_px / 2.0
    cy = height_px / 2.0

    def w2p(x_m: float, y_m: float) -> Tuple[float, float]:
        """Convert world-space coordinates (metres) to pixel coordinates."""
        return (cx + x_m * px_per_m, cy - y_m * px_per_m)

    # ── Grid ──────────────────────────────────────────────────────────
    _draw_grid(draw, width_px, height_px, world_extent_m, w2p)

    # ── Origin marker (NVIDIA-green axis cross) ───────────────────────
    ox, oy = w2p(0, 0)
    arm = 28
    draw.line([(ox - arm, oy), (ox + arm, oy)], fill=ORIGIN, width=1)
    draw.line([(ox, oy - arm), (ox, oy + arm)], fill=ORIGIN, width=1)

    # ── Objects ───────────────────────────────────────────────────────
    if spec.objects:
        for obj in spec.objects:
            _draw_object(draw, obj, w2p, px_per_m, spec)
    else:
        # Empty-state hint
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        msg = "LayoutSpec.intent only — no objects to render"
        if font is not None:
            tw, th = draw.textbbox((0, 0), msg, font=font)[2:]
            draw.text(
                ((width_px - tw) / 2, (height_px - th) / 2),
                msg,
                fill=TEXT_SECONDARY,
                font=font,
            )

    # ── Header strip (spec metadata) ──────────────────────────────────
    _draw_header(draw, spec, width_px)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_layout_spec_to_file(
    spec: LayoutSpec,
    path: Path,
    **kwargs,
) -> Path:
    """Render to a file on disk, returning the path. Convenience wrapper for
    canvas-mirror panel which reads PNG via `ui.Image(path)`."""
    png_bytes = render_layout_spec_to_png(spec, **kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)
    return path


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _draw_grid(draw, width_px, height_px, world_extent_m, w2p):
    """Two-tier grid: minor at 0.25m, major at 1m."""
    # Minor
    step = 0.25
    n = int(world_extent_m / step) + 1
    for i in range(-n, n + 1):
        x_m = i * step
        x_px, _ = w2p(x_m, 0)
        if 0 <= x_px <= width_px:
            draw.line([(x_px, 0), (x_px, height_px)], fill=GRID_MINOR, width=1)
        y_m = i * step
        _, y_px = w2p(0, y_m)
        if 0 <= y_px <= height_px:
            draw.line([(0, y_px), (width_px, y_px)], fill=GRID_MINOR, width=1)
    # Major
    step = 1.0
    n = int(world_extent_m / step) + 1
    for i in range(-n, n + 1):
        x_m = i * step
        x_px, _ = w2p(x_m, 0)
        if 0 <= x_px <= width_px:
            draw.line([(x_px, 0), (x_px, height_px)], fill=GRID_MAJOR, width=1)
        y_m = i * step
        _, y_px = w2p(0, y_m)
        if 0 <= y_px <= height_px:
            draw.line([(0, y_px), (width_px, y_px)], fill=GRID_MAJOR, width=1)


def _draw_object(draw, obj: TypedObject, w2p, px_per_m, spec: LayoutSpec):
    """Render a single typed object with class-colored outlined-light-fill
    rectangle + reach circle for robots."""
    color = CLASS_COLORS.get(obj.object_class, DEFAULT_CLASS_COLOR)
    # Light fill = same hue at 15% alpha, outline = full color
    fill_rgba = (color[0], color[1], color[2], 38)  # ~15% alpha
    outline_rgba = (color[0], color[1], color[2], 255)

    # Compute rotated rectangle corners in world space
    cx_w, cy_w = obj.position.x, obj.position.y
    half_w, half_h = obj.size.w / 2, obj.size.h / 2
    theta = math.radians(obj.rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    corners_w = [
        (-half_w, -half_h), (half_w, -half_h),
        (half_w, half_h), (-half_w, half_h),
    ]
    corners_pix = []
    for lx, ly in corners_w:
        wx = cx_w + lx * cos_t - ly * sin_t
        wy = cy_w + lx * sin_t + ly * cos_t
        corners_pix.append(w2p(wx, wy))

    # Filled polygon (light wash) + outline
    draw.polygon(corners_pix, fill=fill_rgba)
    for i in range(4):
        a, b = corners_pix[i], corners_pix[(i + 1) % 4]
        draw.line([a, b], fill=outline_rgba, width=2)

    # Robot reach circle (dashed appearance via short segments)
    is_robot_arm = obj.object_class in {
        "franka_panda", "ur5e", "ur10e",
        "kinova_gen3", "iiwa", "jaco7",
    }
    if is_robot_arm:
        reach_radii = {
            "franka_panda": 0.855,
            "ur5e": 0.850, "ur10e": 1.300,
            "kinova_gen3": 0.902, "iiwa": 0.820, "jaco7": 0.902,
        }
        radius_m = reach_radii.get(obj.object_class, 0.855)
        radius_px = radius_m * px_per_m
        cx_px, cy_px = w2p(cx_w, cy_w)
        # Hollow circle, low-opacity stroke
        ring_color = (color[0], color[1], color[2], 96)
        draw.ellipse(
            [cx_px - radius_px, cy_px - radius_px,
             cx_px + radius_px, cy_px + radius_px],
            outline=ring_color,
            width=1,
        )

    # Label below the object
    label_x, label_y = w2p(cx_w, cy_w - half_h - 0.05)
    try:
        font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), obj.name, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (label_x - tw / 2, label_y),
            obj.name,
            fill=TEXT_PRIMARY,
            font=font,
        )
    except Exception:
        pass


def _draw_header(draw, spec: LayoutSpec, width_px: int):
    """Top-left header showing pattern_hint, counts, revision."""
    intent = spec.intent
    text = (
        f"pattern={intent.pattern_hint}  "
        f"robots={intent.counts.robots}  "
        f"conveyors={intent.counts.conveyors}  "
        f"bins={intent.counts.bins}  "
        f"cubes={intent.counts.cubes}  "
        f"rev={spec.revision}"
    )
    try:
        font = ImageFont.load_default()
        draw.text((8, 6), text, fill=TEXT_SECONDARY, font=font)
    except Exception:
        pass
