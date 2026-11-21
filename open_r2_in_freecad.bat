@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "FREECAD_DIR=H:\FreeCAD-1.1.4"
set "STEP_ASSY=%~dp0workspace\r2_hybrid_showcase\00_cad_assembly\r2_hybrid_full_assembly.step"

if not exist "%FREECAD_DIR%\bin\freecad.exe" (
    echo FreeCAD 1.1.4 not found at %FREECAD_DIR%\bin\freecad.exe 1>&2
    exit /b 1
)

if not exist "%STEP_ASSY%" (
    echo Building native FreeCAD 1.1.4 B-Rep assembly and colored STEP assembly...
    "%FREECAD_DIR%\bin\python.exe" "%~dp0scripts\build_r2_freecad_assembly.py"
)

echo Launching FreeCAD 1.1.4 with the colored R2 3-Finger Tactile Gripper + Workcell B-Rep Assembly...
start "FreeCAD 1.1.4 - R2 Tactile Gripper CAD Authority" "%FREECAD_DIR%\bin\freecad.exe" "%STEP_ASSY%"
exit /b 0
