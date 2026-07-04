@echo off
setlocal enabledelayedexpansion

rem Always run relative to this checkout so the launcher also works from a
rem shortcut, Explorer, another drive, or a folder containing spaces.
cd /d "%~dp0"

rem AI Account Hub now runs on the PySide6 / Qt front-end. The old Tkinter UI
rem was retired; its logic lives in outputs\ai-hub-calendar-gui\hub_core.py as a
rem shared, Tk-free backend that the Qt app imports.
set "APP=%~dp0outputs\ai-hub-qt\main.py"
set "DISCOVERY=%~dp0outputs\ai-hub-calendar-gui\provider_discovery.py"
set "REQUIREMENTS=%~dp0requirements.txt"
set "AI_HUB_DISCOVERY_BOOTSTRAPPED="

if not exist "%APP%" (
    echo AI Account Hub is incomplete: "%APP%" was not found.
    pause
    exit /b 1
)

rem Pick a Python 3.10+ interpreter: prefer the py launcher, then python.exe.
set "PYRUN="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PYRUN=py -3"
if not defined PYRUN (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYRUN=python"
)
if not defined PYRUN (
    echo Python 3.10 or newer was not found. Install it, then run this launcher again.
    pause
    exit /b 1
)

rem Ensure PySide6 is available for the Qt front-end.
%PYRUN% -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo Installing PySide6, one time only. Please wait...
    if exist "%REQUIREMENTS%" (
        %PYRUN% -m pip install -r "%REQUIREMENTS%"
    ) else (
        %PYRUN% -m pip install "PySide6>=6.8,<7"
    )
    if errorlevel 1 (
        echo PySide6 could not be installed. Check Python and your network connection.
        pause
        exit /b 1
    )
)

rem Rescan provider locations on launch. The report is machine-local and holds
rem paths/versions only; it never reads or writes provider tokens.
if exist "%DISCOVERY%" (
    %PYRUN% "%DISCOVERY%" --write-report --quiet
    if not errorlevel 1 set "AI_HUB_DISCOVERY_BOOTSTRAPPED=1"
)

%PYRUN% "%APP%"

if errorlevel 1 (
    echo.
    echo AI Account Hub exited with an error.
    pause
)
