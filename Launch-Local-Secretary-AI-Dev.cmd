@echo off
setlocal
cd /d "%~dp0"
echo [Development] NiceGUI auto-reload is enabled for Python source changes.
echo [Important] Update source only after running MAGI Tasks have finished.
echo [Important] Browser tab is not opened automatically. Use the usual RITSUKO URL.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\ui\launch_daily_pkb.ps1" -DatabaseMode production -AutoReload
if errorlevel 1 pause
