@echo off
setlocal
cd /d "%~dp0"
call ".\Launch-PKB-Web.cmd"
if errorlevel 1 pause
