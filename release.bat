@echo off
rem Publishes a new version - the real work is in release.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release.ps1"
if errorlevel 1 pause
