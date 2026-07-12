@echo off
setlocal enabledelayedexpansion

rem Always run relative to this checkout so the launcher also works from a
rem shortcut, Explorer, another drive, or a folder containing spaces.
cd /d "%~dp0"

rem AI Account Hub runs on the PySide6 / Qt front-end. The app is the
rem ai_account_hub package: the UI lives in ai_account_hub\ui\ and the shared,
rem Tk-free backend in ai_account_hub\core\ (hub_core.py). main.py is a thin
rem launcher equivalent to "python -m ai_account_hub".
set "APP=%~dp0main.py"
set "DISCOVERY=%~dp0ai_account_hub\core\provider_discovery.py"
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

rem Ensure the Qt front-end, signed community-upload crypto, and Python 3.10
rem TOML compatibility packages are available. The signing private key is
rem protected separately by Windows DPAPI; cryptography only performs P-256.
%PYRUN% -c "import importlib.util, sys; import PySide6, cryptography; raise SystemExit(0 if sys.version_info >= (3, 11) or importlib.util.find_spec('tomli') else 1)" >nul 2>nul
if errorlevel 1 (
    echo Installing AI Account Hub Python requirements. Please wait...
    if exist "%REQUIREMENTS%" (
        %PYRUN% -m pip install -r "%REQUIREMENTS%"
    ) else (
        %PYRUN% -m pip install "PySide6>=6.8,<7" "cryptography>=44,<47" "tomli>=2.0.1"
    )
    if errorlevel 1 (
        echo Python requirements could not be installed. Check Python and your network connection.
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

rem Keep an opt-in console path for development and troubleshooting. Normal
rem launches hand off to pythonw.exe so this bootstrap CMD process can exit and
rem does not remain in the Windows taskbar while the Qt app is running.
if /I "%AI_HUB_CONSOLE%"=="1" goto run_console

set "PYTHONW="
for /f "usebackq delims=" %%I in (`%PYRUN% -c "import sys; from pathlib import Path; print(Path(sys.executable).with_name('pythonw.exe'))"`) do set "PYTHONW=%%I"
if not defined PYTHONW goto pythonw_missing
if not exist "%PYTHONW%" goto pythonw_missing

if not defined AI_HUB_LAUNCH_LOG (
    if defined AI_HUB_LAUNCHER_ROOT (
        set "AI_HUB_LAUNCH_LOG=%AI_HUB_LAUNCHER_ROOT%\logs\ai-account-hub.log"
    ) else (
        set "AI_HUB_LAUNCH_LOG=%USERPROFILE%\.codex-account-launcher\logs\ai-account-hub.log"
    )
)

start "" "%PYTHONW%" "%APP%"
if errorlevel 1 (
    echo AI Account Hub could not start with "%PYTHONW%".
    pause
    exit /b 1
)
exit /b 0

:pythonw_missing
echo pythonw.exe was not found beside the selected Python interpreter.
echo Starting in console mode so the error remains visible.

:run_console
%PYRUN% "%APP%"

if errorlevel 1 (
    echo.
    echo AI Account Hub exited with an error.
    pause
)
