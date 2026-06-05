"""Rendering handlers — target scope: light intensity/color/default,
HDRI skydome, render config + resolution, post-process, environment
background.

Phase 6 wave 17 — first rendering code generators move out of
tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-16.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Rendering-local constants (Phase 8 wave 2, 2026-05-13)
# Migrated from tool_executor.py:849. Used only by this module — kept
# theme-local rather than promoted to _shared.py.

_POST_PROCESS_PATHS = {
    "bloom": "/Render/PostProcess/Bloom",
    "tonemap": "/Render/PostProcess/Tonemap",
    "dof": "/Render/PostProcess/DoF",
    "motion_blur": "/Render/PostProcess/MotionBlur",
}


# ---------------------------------------------------------------------------
# Phase 6 wave 17 — lighting + render config + HDRI + post-process


def _gen_set_light_intensity(args: Dict) -> str:
    """Generate code to set inputs:intensity on a light prim.

    Args:
        args: Dict containing:
            - light_path (str): USD path to the light prim.
            - intensity (float): New intensity value (clamped to >= 0).

    Returns:
        Python source string for execution inside Kit.
    """
    light_path = args["light_path"]
    intensity = float(args["intensity"])
    if intensity < 0:
        intensity = 0.0
    return (
        "import omni.usd\n"
        "from pxr import Sdf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{light_path}')\n"
        "if not prim or not prim.IsValid():\n"
        f"    raise RuntimeError(\"Light prim not found: {light_path}\")\n"
        "attr = prim.GetAttribute('inputs:intensity')\n"
        "if not attr:\n"
        "    attr = prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float)\n"
        f"attr.Set({intensity})\n"
        f"print('Set intensity={intensity} on {light_path}')"
    )

def _gen_set_light_color(args: Dict) -> str:
    """Generate code to set inputs:color on a light prim.

    Args:
        args: Dict containing:
            - light_path (str): USD path to the light prim.
            - rgb (list): [r, g, b] colour components (0–1 range, clamped to >= 0).

    Returns:
        Python source string for execution inside Kit.
    """
    light_path = args["light_path"]
    rgb = args["rgb"]
    if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
        raise ValueError("rgb must be a 3-element list [r, g, b]")
    r, g, b = (max(0.0, float(rgb[0])), max(0.0, float(rgb[1])), max(0.0, float(rgb[2])))
    return (
        "import omni.usd\n"
        "from pxr import Sdf, Gf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{light_path}')\n"
        "if not prim or not prim.IsValid():\n"
        f"    raise RuntimeError(\"Light prim not found: {light_path}\")\n"
        "attr = prim.GetAttribute('inputs:color')\n"
        "if not attr:\n"
        "    attr = prim.CreateAttribute('inputs:color', Sdf.ValueTypeNames.Color3f)\n"
        f"attr.Set(Gf.Vec3f({r}, {g}, {b}))\n"
        f"print('Set color=({r}, {g}, {b}) on {light_path}')"
    )

def _gen_create_hdri_skydome(args: Dict) -> str:
    """Generate code to define or replace a UsdLux.DomeLight with an HDRI texture.

    Args:
        args: Dict containing:
            - hdri_path (str): Asset path to the .hdr or .exr texture.
            - dome_path (str, optional): USD path for the dome prim (default /Environment/DomeLight).
            - intensity (float, optional): Light intensity (default 1000, clamped to >= 0).

    Returns:
        Python source string for execution inside Kit.
    """
    hdri_path = args["hdri_path"]
    dome_path = args.get("dome_path", "/Environment/DomeLight")
    intensity = float(args.get("intensity", 1000.0))
    if intensity < 0:
        intensity = 0.0
    # Escape single quotes in the HDRI path so the literal stays valid
    safe_hdri = hdri_path.replace("'", "\\'")
    # Live-probed 2026-04-18: UsdLux.DomeLight.Define on /NoSuchParent/Dome
    # returned a DomeLight object whose .GetPrim() was technically valid
    # (USD auto-creates intermediate parents in DefinePrim's internal call)
    # but the .Set() calls on the Sdf.Asset attr silently fell through on
    # some Kit builds. The tool printed "Created HDRI skydome at ..."
    # regardless. Fix: post-check prim IsValid + the texture attribute
    # was actually authored, and pre-check the dome_path is a legal SdfPath.
    return (
        "import omni.usd\n"
        "from pxr import UsdLux, Sdf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"dome_path = '{dome_path}'\n"
        "# Reject paths that USD considers malformed (spaces, special chars, "
        "# leading digits) — Sdf.Path raises on these but DefinePrim silently "
        "# returns an invalid schema object in some Kit builds.\n"
        "_sdf_path = Sdf.Path(dome_path)\n"
        "if _sdf_path.isEmpty:\n"
        f"    raise ValueError('create_hdri_skydome: invalid dome_path: ' + {dome_path!r})\n"
        "# Idempotent: re-define replaces existing prim of the same type, leaves\n"
        "# parent Xforms untouched.\n"
        "dome = UsdLux.DomeLight.Define(stage, dome_path)\n"
        "prim = dome.GetPrim()\n"
        "if not prim.IsValid():\n"
        f"    raise RuntimeError('create_hdri_skydome: DomeLight.Define did not produce a valid prim at ' + {dome_path!r})\n"
        "tex_attr = prim.GetAttribute('inputs:texture:file')\n"
        "if not tex_attr:\n"
        "    tex_attr = prim.CreateAttribute('inputs:texture:file', Sdf.ValueTypeNames.Asset)\n"
        f"tex_attr.Set('{safe_hdri}')\n"
        "if not tex_attr.HasAuthoredValue():\n"
        f"    raise RuntimeError('create_hdri_skydome: inputs:texture:file did not author on ' + {dome_path!r})\n"
        "fmt_attr = prim.GetAttribute('inputs:texture:format')\n"
        "if not fmt_attr:\n"
        "    fmt_attr = prim.CreateAttribute('inputs:texture:format', Sdf.ValueTypeNames.Token)\n"
        "fmt_attr.Set('latlong')\n"
        "intensity_attr = prim.GetAttribute('inputs:intensity')\n"
        "if not intensity_attr:\n"
        "    intensity_attr = prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float)\n"
        f"intensity_attr.Set({intensity})\n"
        f"print('Created HDRI skydome at ' + dome_path + ' with texture {safe_hdri}')"
    )

def _gen_add_default_light(args: Dict) -> str:
    """Add a plain DomeLight so the viewport isn't black.

    Added 2026-04-19 after conveyor_pick_place scenario runs repeatedly
    produced correct geometry but unlit scenes — the text-only
    scene_needs_light cite was insufficient to force Gemini Flash to
    author a UsdLux.DomeLight. A registered tool gives the agent a
    concrete named action to take.

    Minimal: no HDRI texture, no environment setup. Use
    create_hdri_skydome for textured dome environments.
    """
    light_path = args.get("light_path", "/World/DomeLight")
    intensity = float(args.get("intensity", 1000.0))
    if intensity < 0:
        intensity = 0.0
    return f"""\
import omni.usd
from pxr import UsdLux, Sdf

stage = omni.usd.get_context().get_stage()
light_path = '{light_path}'

# Idempotent: re-define reuses the existing prim if present.
dome = UsdLux.DomeLight.Define(stage, light_path)
prim = dome.GetPrim()
if not prim.IsValid():
    raise RuntimeError('add_default_light: could not define DomeLight at ' + light_path)

intensity_attr = prim.GetAttribute('inputs:intensity')
if not intensity_attr or not intensity_attr.IsDefined():
    intensity_attr = prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float)
intensity_attr.Set({intensity})

import json
print(json.dumps({{
    "ok": True,
    "light_path": light_path,
    "intensity": {intensity},
    "type": "DomeLight",
    "note": "Plain DomeLight — no HDRI texture. For environment HDRI, use create_hdri_skydome instead.",
}}))
"""

def _gen_set_render_config(args: Dict) -> str:
    """Generate code to configure the active render mode and quality settings.

    Args:
        args: Dict containing:
            - renderer (str): Render mode — PathTracing | RaytracedLighting | RealTime.
            - samples_per_pixel (int, optional): Path-tracer spp.
            - max_bounces (int, optional): Maximum ray bounce depth.

    Returns:
        Python source string for execution inside Kit.
    """
    renderer = args["renderer"]
    spp = args.get("samples_per_pixel")
    max_bounces = args.get("max_bounces")

    # PathTracing is enabled by setting /Render/Vars.rendermode = 'PathTracing'
    # (the default rtx delegate is RaytracedLighting / RealTime).
    rendermode_attr_value = repr(renderer)

    lines = [
        "import omni.usd",
        "from pxr import Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        "",
        "# Ensure /Render/Vars container exists",
        "render_vars = stage.DefinePrim('/Render/Vars', 'Scope')",
        "",
        "# Renderer mode (PathTracing | RaytracedLighting | RealTime)",
        f"render_vars.CreateAttribute('rendermode', Sdf.ValueTypeNames.String).Set({rendermode_attr_value})",
    ]

    if spp is not None:
        lines.append(
            f"render_vars.CreateAttribute('samplesPerPixel', Sdf.ValueTypeNames.Int).Set({int(spp)})"
        )
    if max_bounces is not None:
        lines.append(
            f"render_vars.CreateAttribute('maxBounces', Sdf.ValueTypeNames.Int).Set({int(max_bounces)})"
        )

    lines.extend([
        "",
        "# Switch the active hydra engine on the viewport",
        "try:",
        "    import omni.kit.viewport.utility as vp_util",
        "    vp = vp_util.get_active_viewport()",
        "    if vp is not None:",
        "        vp.hydra_engine = 'rtx'",
        "except Exception as _e:",
        f"    print('Viewport switch skipped (headless?):', _e)",
        "",
        f"print('Render config updated: renderer={renderer}, spp={spp}, max_bounces={max_bounces}')",
    ])
    return "\n".join(lines)

def _gen_set_render_resolution(args: Dict) -> str:
    """Generate code to set the active viewport render resolution.

    Args:
        args: Dict containing:
            - width (int): Viewport width in pixels.
            - height (int): Viewport height in pixels.

    Returns:
        Python source string for execution inside Kit.
    """
    width = int(args["width"])
    height = int(args["height"])
    return (
        "import omni.kit.viewport.utility as vp_util\n"
        "vp = vp_util.get_active_viewport()\n"
        "if vp is None:\n"
        "    raise RuntimeError('No active viewport — running headless?')\n"
        f"vp.resolution = ({width}, {height})\n"
        f"print('Viewport resolution set to {width}x{height}')"
    )

def _gen_enable_post_process(args: Dict) -> str:
    """Generate code to enable or disable a post-process effect with optional parameters.

    Supported effects: bloom, tonemap, dof, motion_blur.

    Args:
        args: Dict containing:
            - effect (str): Effect name.
            - enabled (bool, optional): True to enable, False to disable (default True).
            - params (dict, optional): Effect-specific parameter overrides.

    Returns:
        Python source string for execution inside Kit.
    """
    effect = args["effect"]
    params = args.get("params", {}) or {}
    enabled = args.get("enabled", True)

    prim_path = _POST_PROCESS_PATHS.get(effect, f"/Render/PostProcess/{effect}")

    lines = [
        "import omni.usd",
        "from pxr import Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.DefinePrim({prim_path!r}, 'Scope')",
        "",
        f"# Toggle the {effect} effect",
        f"prim.CreateAttribute('enabled', Sdf.ValueTypeNames.Bool).Set({bool(enabled)})",
    ]

    # Effect-specific parameter writes — kept generic so future params slot in.
    if effect == "bloom":
        if "intensity" in params:
            lines.append(
                f"prim.CreateAttribute('intensity', Sdf.ValueTypeNames.Float).Set({float(params['intensity'])})"
            )
        if "threshold" in params:
            lines.append(
                f"prim.CreateAttribute('threshold', Sdf.ValueTypeNames.Float).Set({float(params['threshold'])})"
            )
    elif effect == "tonemap":
        if "operator" in params:
            lines.append(
                f"prim.CreateAttribute('operator', Sdf.ValueTypeNames.String).Set({str(params['operator'])!r})"
            )
        if "exposure" in params:
            lines.append(
                f"prim.CreateAttribute('exposure', Sdf.ValueTypeNames.Float).Set({float(params['exposure'])})"
            )
    elif effect == "dof":
        if "focus_distance" in params:
            lines.append(
                f"prim.CreateAttribute('focusDistance', Sdf.ValueTypeNames.Float).Set({float(params['focus_distance'])})"
            )
        if "f_stop" in params:
            lines.append(
                f"prim.CreateAttribute('fStop', Sdf.ValueTypeNames.Float).Set({float(params['f_stop'])})"
            )
    elif effect == "motion_blur":
        if "shutter_speed" in params:
            lines.append(
                f"prim.CreateAttribute('shutterSpeed', Sdf.ValueTypeNames.Float).Set({float(params['shutter_speed'])})"
            )
        if "samples" in params:
            lines.append(
                f"prim.CreateAttribute('samples', Sdf.ValueTypeNames.Int).Set({int(params['samples'])})"
            )

    lines.append(f"print('Post-process {effect} enabled={bool(enabled)}')")
    return "\n".join(lines)

def _gen_set_environment_background(args: Dict) -> str:
    """Generate code to set the scene background to an HDRI texture or solid colour.

    When both hdri_path and color are supplied, the HDRI takes precedence.

    Args:
        args: Dict containing:
            - hdri_path (str, optional): Asset path to .hdr/.exr texture.
            - color (list, optional): [r, g, b] solid background colour (0–1).
            - intensity (float, optional): Dome light intensity (default 1000).
            - rotation_deg (float, optional): Dome rotation in degrees (default 0).

    Returns:
        Python source string for execution inside Kit.
    """
    hdri_path = args.get("hdri_path")
    color = args.get("color")
    intensity = args.get("intensity", 1000.0)
    rotation_deg = args.get("rotation_deg", 0.0)

    if hdri_path and color:
        # Both provided — HDRI wins, but emit a comment so the user sees why.
        pass

    if hdri_path:
        return f"""\
import omni.usd
from pxr import UsdLux, UsdGeom, Sdf, Gf

stage = omni.usd.get_context().get_stage()
dome_path = '/World/EnvironmentLight'
dome = UsdLux.DomeLight.Define(stage, dome_path)
dome.CreateTextureFileAttr().Set({hdri_path!r})
dome.CreateIntensityAttr().Set({float(intensity)})
dome.CreateTextureFormatAttr().Set('latlong')

# Rotate dome around the up-axis
xf = UsdGeom.Xformable(dome.GetPrim())
xf.ClearXformOpOrder()
xf.AddRotateYOp().Set({float(rotation_deg)})

print('Environment HDRI set: {hdri_path} (intensity={intensity}, rotation={rotation_deg} deg)')
"""

    if color is not None:
        r, g, b = (float(color[0]), float(color[1]), float(color[2]))
        return f"""\
import omni.usd
from pxr import Sdf, Gf

stage = omni.usd.get_context().get_stage()
render_vars = stage.DefinePrim('/Render/Vars', 'Scope')
render_vars.CreateAttribute('clearColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f({r}, {g}, {b}))

# Remove dome if present so the solid color is actually visible
dome_prim = stage.GetPrimAtPath('/World/EnvironmentLight')
if dome_prim and dome_prim.IsValid():
    stage.RemovePrim('/World/EnvironmentLight')

print('Environment background color set to ({r}, {g}, {b})')
"""

    # Neither provided — clear to a neutral grey by default so this stays a
    # well-defined no-arg call.
    return """\
import omni.usd
from pxr import Sdf, Gf

stage = omni.usd.get_context().get_stage()
render_vars = stage.DefinePrim('/Render/Vars', 'Scope')
render_vars.CreateAttribute('clearColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.2, 0.2, 0.2))
print('Environment background reset to neutral grey (0.2, 0.2, 0.2)')
"""


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
    # Code-gen handlers (8)
    codegen["add_default_light"] = _gen_add_default_light
    codegen["create_hdri_skydome"] = _gen_create_hdri_skydome
    codegen["enable_post_process"] = _gen_enable_post_process
    codegen["set_environment_background"] = _gen_set_environment_background
    codegen["set_light_color"] = _gen_set_light_color
    codegen["set_light_intensity"] = _gen_set_light_intensity
    codegen["set_render_config"] = _gen_set_render_config
    codegen["set_render_resolution"] = _gen_set_render_resolution
