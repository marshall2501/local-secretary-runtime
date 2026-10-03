@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ui\launch_gui.ps1"
if errorlevel 1 (
    echo.
    echo PKB GUI launch failed. Review the error above.
    pause
)
