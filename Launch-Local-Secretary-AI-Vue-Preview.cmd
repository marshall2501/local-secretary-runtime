@echo off
rem Provenance-Origin: original; Provenance-AI: AI-generated
rem Vue移行中の実Task参照専用ランチャー。既存NiceGUIの通常起動は変更しない。
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\ui\launch_vue_daily_readonly.ps1"
if errorlevel 1 pause
