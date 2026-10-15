@echo off
rem Provenance-Origin: original; Provenance-AI: AI-generated
rem Explicit read-only Vue preview; never replaces the normal NiceGUI launcher.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\ui\launch_vue_daily_readonly.ps1"
if errorlevel 1 pause
