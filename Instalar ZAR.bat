@echo off
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0installer\install_direct.ps1" -Pkg "%~dp0."
