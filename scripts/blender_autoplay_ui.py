"""Startup UI helper for Blender 5.2.1 + ovrtx: switches the 3D Viewport to Camera + Material Preview
and starts continuous timeline animation playback so the R2 Gripper grasp-lift-mate cycle plays immediately.
"""
from __future__ import annotations

import bpy


def _configure_viewports_and_play():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.shading.type = "MATERIAL"
                        if space.region_3d is not None:
                            space.region_3d.view_perspective = "CAMERA"
    if not any(s.is_animation_playing for s in bpy.data.screens):
        try:
            bpy.ops.screen.animation_play()
        except Exception:
            pass
    return None


bpy.app.timers.register(_configure_viewports_and_play, first_interval=0.35)
