@echo off
setlocal
REM Isaac Assist - Windows Isaac Sim 7.0a + ROS 2 Jazzy + omni.isaac.assist Launcher
REM Usage:
REM   launch_isaac.bat [optional_scene.usd] [extra kit args...]

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "PATH=%LOCALAPPDATA%\pixi\bin;%PATH%"
if "%PIXI_CACHE_DIR%"=="" set "PIXI_CACHE_DIR=H:\pixi-cache"
set "OMNI_KIT_ACCEPT_EULA=YES"

if "%ISAAC_SIM_ROOT%"=="" (
    if exist "H:\isaacsim7\_build\windows-x86_64\release\isaac-sim.bat" (
        set "ISAAC_SIM_ROOT=H:\isaacsim7\_build\windows-x86_64\release"
    ) else if exist "C:\isaacsim\isaac-sim.bat" (
        set "ISAAC_SIM_ROOT=C:\isaacsim"
    )
)

if "%ISAAC_ASSIST_EXT_FOLDER%"=="" set "ISAAC_ASSIST_EXT_FOLDER=%SCRIPT_DIR%\exts\isaac_6.0"

REM Start ROS 2 Jazzy Zenoh router if available and not already running
if exist "H:\IsaacSim-ros_workspaces\jazzy_ws\pixi.toml" (
    tasklist /FI "IMAGENAME eq rmw_zenohd.exe" 2>NUL | find /I "rmw_zenohd.exe" >NUL
    if errorlevel 1 (
        echo Starting ROS 2 Jazzy Zenoh router...
        start "ROS 2 Zenoh Router" /MIN cmd /c "cd /d H:\IsaacSim-ros_workspaces\jazzy_ws && pixi run zenoh"
        timeout /t 2 /nobreak >NUL
    )
    echo Launching Isaac Sim with ROS 2 Jazzy + Isaac Assist extension...
    cd /d H:\IsaacSim-ros_workspaces\jazzy_ws
    pixi run "%ISAAC_SIM_ROOT%\isaac-sim.bat" --/isaac/startup/ros_bridge_extension=isaacsim.ros2.bridge --ext-folder "%ISAAC_ASSIST_EXT_FOLDER%" --enable omni.isaac.assist %*
    exit /b %ERRORLEVEL%
)

echo Launching Isaac Sim with Isaac Assist extension...
call "%ISAAC_SIM_ROOT%\isaac-sim.bat" --/isaac/startup/ros_bridge_extension=isaacsim.ros2.bridge --ext-folder "%ISAAC_ASSIST_EXT_FOLDER%" --enable omni.isaac.assist %*
