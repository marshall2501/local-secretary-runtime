@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\pkb_proto\launch_web_workbench.ps1"
if errorlevel 1 pause
