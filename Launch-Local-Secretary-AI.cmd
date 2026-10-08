@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\ui\launch_daily_pkb.ps1" -DatabaseMode production
if errorlevel 1 pause
