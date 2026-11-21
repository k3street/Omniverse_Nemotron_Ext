@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "FREECAD_DIR=H:\FreeCAD-1.1.4"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=right"

if /I "%MODE%"=="left" (
    set "STEP_ASSY=%~dp0workspace\amber_gripper_r4\cad\amber_gripper_r4_left_with_changer.step"
    set "TITLE=FreeCAD 1.1.4 - Amber v0.5 Gripper R4 (LEFT) + Wrist Tool Changer R2"
) else if /I "%MODE%"=="pair" (
    set "STEP_ASSY=%~dp0workspace\amber_gripper_r4\cad\amber_gripper_r4_bilateral_pair_with_changers.step"
    set "TITLE=FreeCAD 1.1.4 - Amber v0.5 Gripper R4 (BILATERAL PAIR) + Wrist Tool Changers"
) else (
    set "STEP_ASSY=%~dp0workspace\amber_gripper_r4\cad\amber_gripper_r4_right_with_changer.step"
    set "TITLE=FreeCAD 1.1.4 - Amber v0.5 Gripper R4 (RIGHT) + Wrist Tool Changer R2"
)

if not exist "%FREECAD_DIR%\bin\freecad.exe" (
    echo FreeCAD 1.1.4 not found at %FREECAD_DIR%\bin\freecad.exe 1>&2
    exit /b 1
)

if not exist "%STEP_ASSY%" (
    echo Building parametric Amber v0.5 Gripper R4 + Wrist Tool Changer R2 B-Rep assembly...
    set "VIRTUAL_ENV="
    set "PYTHONPATH=C:\Users\Owner\Documents\Github\cad_creator"
    "C:\Users\Owner\AppData\Local\Microsoft\WindowsApps\python.exe" "%~dp0scripts\build_amber_gripper_r4.py" --mode all --pose open
    "%FREECAD_DIR%\bin\python.exe" "%~dp0scripts\build_r4_freecad_doc.py"
)

echo Launching %TITLE%...
echo   Colored AP214 STEP (FreeCAD 1.1.4 ^& Fusion 360): %STEP_ASSY%
start "%TITLE%" "%FREECAD_DIR%\bin\freecad.exe" "%STEP_ASSY%"
exit /b 0
