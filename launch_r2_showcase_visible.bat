@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Running CAD Creator (R2 Tactile Gripper) -^> MuJoCo 3.11 -^> Isaac Sim 7.0.0a1 -^> Blender 5.2.1 ovrtx...
"H:\isaacsim7-venv\Scripts\python.exe" -m scripts.r2_hybrid_ecosystem_pipeline showcase %*
exit /b %errorlevel%
