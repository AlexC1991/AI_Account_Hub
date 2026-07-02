@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0ai_hub_calendar_gui.py"
    goto :done
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0ai_hub_calendar_gui.py"
    goto :done
)

echo Python was not found. Install Python 3, then run this launcher again.
pause
exit /b 1

:done
if errorlevel 1 (
    echo.
    echo AI Account Hub Calendar exited with an error.
    pause
)
