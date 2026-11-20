@echo off
setlocal
REM Isaac Assist - Windows FastAPI Service Launcher
REM Usage:
REM   launch_service.bat [local|anthropic|google|openai|grok]

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

set "MODE=%~1"
if "%MODE%"=="" (
    if not "%LLM_MODE%"=="" (
        set "MODE=%LLM_MODE%"
    ) else (
        set "MODE=local"
    )
)
set "LLM_MODE=%MODE%"

if "%ISAAC_ASSIST_HOST%"=="" set "ISAAC_ASSIST_HOST=127.0.0.1"
if "%ISAAC_ASSIST_PORT%"=="" set "ISAAC_ASSIST_PORT=8000"

if "%ISAACSIM7_VENV_PYTHON%"=="" (
    if exist "H:\isaacsim7-venv\Scripts\python.exe" (
        set "ISAACSIM7_VENV_PYTHON=H:\isaacsim7-venv\Scripts\python.exe"
    )
)

set "RUNNER_PY="
if not "%SERVICE_PYTHON%"=="" (
    set "RUNNER_PY=%SERVICE_PYTHON%"
) else if exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
    set "RUNNER_PY=%SCRIPT_DIR%\.venv\Scripts\python.exe"
) else if not "%ISAACSIM7_VENV_PYTHON%"=="" (
    set "RUNNER_PY=%ISAACSIM7_VENV_PYTHON%"
) else if exist "H:\isaacsim7\_build\windows-x86_64\release\python.bat" (
    set "RUNNER_PY=H:\isaacsim7\_build\windows-x86_64\release\python.bat"
) else (
    set "RUNNER_PY=python"
)

echo Starting Isaac Assist FastAPI service on http://%ISAAC_ASSIST_HOST%:%ISAAC_ASSIST_PORT% (LLM_MODE=%LLM_MODE%)...
call "%RUNNER_PY%" -m uvicorn service.isaac_assist_service.main:app --host %ISAAC_ASSIST_HOST% --port %ISAAC_ASSIST_PORT%
