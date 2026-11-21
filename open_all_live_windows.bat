@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo [1/5] Opening Mission Control Dashboard in your browser...
start "" "http://127.0.0.1:8000/api/v1/cad_pipeline/dashboard"

echo [2/5] Launching FreeCAD 1.1.4 B-Rep CAD Authority (R2 Tactile Gripper + Workcell)...
call "%~dp0open_r2_in_freecad.bat"

echo [3/5] Launching Live MuJoCo 3.11.0 3D Viewer (R2 3-Finger Tactile Gripper Grasp + Mate)...
start "MuJoCo 3.11 R2 Tactile Gripper" "H:\isaacsim7-venv\Scripts\python.exe" -m scripts.launch_r2_mujoco_live_viewer

echo [4/5] Launching Blender 5.2.1 LTS + ovrtx 0.2.0 Animated Scene...
start "Blender 5.2.1 + ovrtx R2 Workcell" cmd /c ""H:\ov-blender-example\launch_blender_ovrtx.bat" "C:\Users\Owner\Documents\Github\Omniverse_Nemotron_Ext\workspace\r2_hybrid_showcase\04_blender_ovrtx\r2_hybrid_workcell_ovrtx.blend" --python "C:\Users\Owner\Documents\Github\Omniverse_Nemotron_Ext\scripts\blender_autoplay_ui.py""

echo [5/5] Launching Isaac Sim 7.0.0a1 + ROS 2 Jazzy + Nemotron Extension and auto-loading R2 Workcell...
start "Isaac Sim 7.0.0a1 + Nemotron Ext" cmd /c ""%~dp0launch_isaac.bat""
"H:\isaacsim7-venv\Scripts\python.exe" -m scripts.autoload_r2_into_live_isaac

echo All 5 windows (FreeCAD + MuJoCo + Blender ovrtx + Isaac Sim 7.0a + Dashboard) are live on your desktop!
exit /b 0
