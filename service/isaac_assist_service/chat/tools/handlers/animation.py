"""Animation + audio handlers — target scope: timeline range,
keyframe authoring, animation playback, audio prim creation +
properties.

Phase 6 wave 19 — first animation/audio code generators move out
of tool_executor.py. Same migration pattern as Phase 3 / Phase 5 /
Phase 6 waves 1-18.

Per specs/IA_FULL_SPEC_2026-05-10.md Phases 2 + 6.
"""
from __future__ import annotations

from typing import Any, Callable, Dict


# ---------------------------------------------------------------------------
# Phase 6 wave 19 — timeline + keyframe + animation + audio


def _gen_set_timeline_range(args: Dict) -> str:
    start = args["start"]
    end = args["end"]
    fps = args.get("fps")
    lines = [
        "import omni.usd",
        "import omni.timeline",
        "",
        "stage = omni.usd.get_context().get_stage()",
        "if stage is None:",
        "    raise RuntimeError('No stage is open — cannot set timeline range')",
        "",
        f"start_code = float({start!r})",
        f"end_code = float({end!r})",
        "if not (start_code < end_code):",
        "    raise ValueError(f'start ({start_code}) must be < end ({end_code})')",
        "",
    ]
    if fps is not None:
        lines += [
            f"fps = float({fps!r})",
            "if fps <= 0:",
            "    raise ValueError(f'fps must be > 0, got {fps}')",
            "stage.SetTimeCodesPerSecond(fps)",
        ]
    else:
        lines += [
            "fps = float(stage.GetTimeCodesPerSecond() or 24.0)",
        ]
    lines += [
        "stage.SetStartTimeCode(start_code)",
        "stage.SetEndTimeCode(end_code)",
        "",
        "# Push the new range into the timeline interface so the viewport scrubber updates.",
        "tl = omni.timeline.get_timeline_interface()",
        "try:",
        "    tl.set_start_time(start_code / fps)",
        "    tl.set_end_time(end_code / fps)",
        "except Exception:",
        "    # Older Kit versions accept time codes directly.",
        "    if hasattr(tl, 'set_start_time_code'):",
        "        tl.set_start_time_code(start_code)",
        "    if hasattr(tl, 'set_end_time_code'):",
        "        tl.set_end_time_code(end_code)",
        "",
        "print(f'Timeline range set: [{start_code}, {end_code}] codes @ {fps} fps')",
    ]
    return "\n".join(lines)


def _gen_set_keyframe(args: Dict) -> str:
    prim_path = args["prim_path"]
    attr = args["attr"]
    time = args["time"]
    value = args["value"]
    return (
        "import omni.usd\n"
        "from pxr import Sdf, Usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot set keyframe')\n"
        "\n"
        f"prim_path = {prim_path!r}\n"
        f"attr_name = {attr!r}\n"
        f"time_seconds = float({time!r})\n"
        f"value = {value!r}\n"
        "\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {prim_path}')\n"
        "\n"
        "fps = float(stage.GetTimeCodesPerSecond() or 24.0)\n"
        "time_code = Usd.TimeCode(time_seconds * fps)\n"
        "\n"
        "attr_handle = prim.GetAttribute(attr_name)\n"
        "if not attr_handle or not attr_handle.IsValid():\n"
        "    raise RuntimeError(\n"
        "        f'attribute not found on {prim_path}: {attr_name}. '\n"
        "        f'Use list_attributes() to see available attributes.'\n"
        "    )\n"
        "\n"
        "# Cast lists/tuples to Vt-friendly types when the attribute expects an array.\n"
        "try:\n"
        "    attr_handle.Set(value, time_code)\n"
        "except Exception as e:\n"
        "    # Common case: value is a Python list but attribute wants Gf.Vec3f / Vec3d.\n"
        "    from pxr import Gf\n"
        "    if isinstance(value, (list, tuple)) and len(value) == 3:\n"
        "        attr_handle.Set(Gf.Vec3f(*value), time_code)\n"
        "    elif isinstance(value, (list, tuple)) and len(value) == 4:\n"
        "        attr_handle.Set(Gf.Vec4f(*value), time_code)\n"
        "    else:\n"
        "        raise\n"
        "\n"
        "print(f'Keyframe written: {prim_path}.{attr_name} @ frame {time_code.GetValue()} '\n"
        "      f'(t={time_seconds}s, fps={fps}) = {value}')\n"
    )


def _gen_play_animation(args: Dict) -> str:
    start = args["start"]
    end = args["end"]
    return (
        "import omni.timeline\n"
        "import omni.usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot play animation')\n"
        "\n"
        f"start_seconds = float({start!r})\n"
        f"end_seconds = float({end!r})\n"
        "if not (start_seconds < end_seconds):\n"
        "    raise ValueError(f'start ({start_seconds}) must be < end ({end_seconds})')\n"
        "\n"
        "fps = float(stage.GetTimeCodesPerSecond() or 24.0)\n"
        "start_code = start_seconds * fps\n"
        "end_code = end_seconds * fps\n"
        "\n"
        "tl = omni.timeline.get_timeline_interface()\n"
        "# Configure the playback window. Modern Kit uses seconds; older Kit uses time codes.\n"
        "try:\n"
        "    tl.set_start_time(start_seconds)\n"
        "    tl.set_end_time(end_seconds)\n"
        "    tl.set_current_time(start_seconds)\n"
        "except Exception:\n"
        "    if hasattr(tl, 'set_start_time_code'):\n"
        "        tl.set_start_time_code(start_code)\n"
        "    if hasattr(tl, 'set_end_time_code'):\n"
        "        tl.set_end_time_code(end_code)\n"
        "    if hasattr(tl, 'set_current_time_code'):\n"
        "        tl.set_current_time_code(start_code)\n"
        "\n"
        "tl.play()\n"
        "print(f'Playing animation [{start_seconds}s, {end_seconds}s] '\n"
        "      f'(frames {start_code}-{end_code} @ {fps} fps)')\n"
    )


def _gen_create_audio_prim(args: Dict) -> str:
    from ..tool_executor import _SAFE_XFORM_SNIPPET
    pos = args["position"]
    audio_file = args["audio_file"]
    prim_path = args.get("prim_path", "")
    start_time = float(args.get("start_time", 0.0))
    auto_play = bool(args.get("auto_play", True))
    if len(pos) < 3:
        pos = list(pos) + [0.0] * (3 - len(pos))
    px, py, pz = pos[0], pos[1], pos[2]
    return f"""\
import omni.usd
from pxr import UsdGeom, UsdMedia, Sdf, Gf
{_SAFE_XFORM_SNIPPET}
stage = omni.usd.get_context().get_stage()

# Pick a unique path under /World/Audio_<n> if none provided
desired = {repr(prim_path)}
if not desired:
    n = 0
    while True:
        candidate = f"/World/Audio_{{n}}"
        if not stage.GetPrimAtPath(candidate).IsValid():
            desired = candidate
            break
        n += 1

audio = UsdMedia.SpatialAudio.Define(stage, Sdf.Path(desired))
prim = audio.GetPrim()
_safe_set_translate(prim, ({px}, {py}, {pz}))

# Set the audio asset path
try:
    audio.CreateFilePathAttr().Set(Sdf.AssetPath({repr(audio_file)}))
except Exception:
    attr = prim.CreateAttribute("filePath", Sdf.ValueTypeNames.Asset)
    attr.Set(Sdf.AssetPath({repr(audio_file)}))

# Optional playback hints
try:
    audio.CreateStartTimeAttr().Set({start_time})
except Exception:
    prim.CreateAttribute("startTime", Sdf.ValueTypeNames.Double).Set({start_time})
try:
    audio.CreateAuralModeAttr().Set(UsdMedia.Tokens.spatial)
except Exception:
    pass
try:
    audio.CreatePlaybackModeAttr().Set(
        UsdMedia.Tokens.onceFromStart if {auto_play} else UsdMedia.Tokens.noPlayback
    )
except Exception:
    prim.CreateAttribute("auto_play", Sdf.ValueTypeNames.Bool).Set({auto_play})

print(f"create_audio_prim: defined SpatialAudio at {{desired}} -> {audio_file}")
"""


def _gen_set_audio_property(args: Dict) -> str:
    prim_path = args["prim_path"]
    prop = args["prop"]
    value = args["value"]
    # Map the friendly prop name to the SpatialAudio attr
    PROP_MAP = {
        "volume": "gain",
        "gain": "gain",
        "pitch": "pitch",
        "attenuation_start": "startTime",  # not a real attenuation attr in UsdMedia, mapped numerically
        "attenuation_end": "endTime",
        "auto_play": "auto_play",
        "start_time": "startTime",
    }
    if prop not in PROP_MAP:
        return f"# set_audio_property: unknown prop '{prop}'"
    attr_name = PROP_MAP[prop]
    return f"""\
import omni.usd
from pxr import UsdMedia, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({repr(prim_path)})
if not prim or not prim.IsValid():
    raise RuntimeError(f"set_audio_property: prim not found: {prim_path!r}")
if not prim.IsA(UsdMedia.SpatialAudio):
    raise RuntimeError(
        f"set_audio_property: prim {prim_path!r} is a {{prim.GetTypeName()!r}}, not UsdMedia.SpatialAudio"
    )
if True:
    audio = UsdMedia.SpatialAudio(prim)
    prop_name = {repr(prop)}
    attr_name = {repr(attr_name)}
    value = {repr(value)}
    try:
        if prop_name in ("volume", "gain"):
            try:
                audio.CreateGainAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("gain", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "pitch":
            try:
                audio.CreatePitchAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("pitch", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "auto_play":
            try:
                mode = UsdMedia.Tokens.onceFromStart if bool(value) else UsdMedia.Tokens.noPlayback
                audio.CreatePlaybackModeAttr().Set(mode)
            except Exception:
                prim.CreateAttribute("auto_play", Sdf.ValueTypeNames.Bool).Set(bool(value))
        elif prop_name == "start_time":
            try:
                audio.CreateStartTimeAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("startTime", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "attenuation_end":
            try:
                audio.CreateEndTimeAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("endTime", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "attenuation_start":
            prim.CreateAttribute("attenuationStart", Sdf.ValueTypeNames.Double).Set(float(value))
        print(f"set_audio_property: {{prop_name}} -> {{value}} on {prim_path}")
    except Exception as e:
        print(f"set_audio_property: failed to set {{prop_name}}: {{e}}")
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
    # Code-gen handlers (5)
    codegen["create_audio_prim"] = _gen_create_audio_prim
    codegen["play_animation"] = _gen_play_animation
    codegen["set_audio_property"] = _gen_set_audio_property
    codegen["set_keyframe"] = _gen_set_keyframe
    codegen["set_timeline_range"] = _gen_set_timeline_range
