"""FreeCAD 1.0 GUI startup script: opens `r2_hybrid_cad_assembly.FCStd`, applies distinct
engineering B-Rep colors (`ShapeColor`) and crisp `Flat Lines` (Shaded + Edges) display mode,
and fits an Isometric 3D CAD view onto the R2 3-Finger Tactile Gripper + Workcell assembly.
"""
from __future__ import annotations

from pathlib import Path
import FreeCAD as App
import FreeCADGui as Gui

FCSTD_PATH = (
    Path(__file__).resolve().parents[1]
    / "workspace"
    / "r2_hybrid_showcase"
    / "00_cad_assembly"
    / "r2_hybrid_cad_assembly.FCStd"
)

PART_COLORS = {
    "palm_body": (0.22, 0.25, 0.30),
    "turntable_a": (0.78, 0.80, 0.84),
    "turntable_b": (0.78, 0.80, 0.84),
    "thumb_prox": (0.75, 0.78, 0.82),
    "finger_a_prox": (0.75, 0.78, 0.82),
    "finger_b_prox": (0.75, 0.78, 0.82),
    "thumb_dist": (0.16, 0.64, 0.80),
    "finger_a_dist": (0.16, 0.64, 0.80),
    "finger_b_dist": (0.16, 0.64, 0.80),
    "r2_pickup_pedestal": (0.34, 0.37, 0.42),
    "r2_telemetry_cartridge": (0.18, 0.50, 0.88),
    "r2_mating_dock": (0.82, 0.84, 0.87),
}

doc = App.openDocument(str(FCSTD_PATH))
App.setActiveDocument(doc.Name)
Gui.setActiveDocument(doc.Name)

for name, rgb in PART_COLORS.items():
    obj = doc.getObject(name)
    if obj is not None and getattr(obj, "ViewObject", None) is not None:
        vo = obj.ViewObject
        vo.ShapeColor = rgb
        try:
            vo.DisplayMode = "Flat Lines"
            vo.LineWidth = 1.4
        except Exception:
            pass

Gui.activateWorkbench("PartWorkbench")


def _frame_view():
    view = Gui.activeDocument().activeView() if Gui.activeDocument() else None
    if view is not None:
        view.viewAxometric()
        Gui.SendMsgToActiveView("ViewFit")


_frame_view()
try:
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore
    QtCore.QTimer.singleShot(600, _frame_view)
except Exception:
    pass
