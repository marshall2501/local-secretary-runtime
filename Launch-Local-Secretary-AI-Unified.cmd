@echo off
rem Provenance-Origin: original; Provenance-AI: AI-generated
rem Official future Secretary Server (Vue + Task + PKB + Finance + Settings + API).
rem Does not stop existing NiceGUI / 8010 / 8097 services; fails on port conflict.
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\ui\launch_secretary_server.ps1"
if errorlevel 1 pause
