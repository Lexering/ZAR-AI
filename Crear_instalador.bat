@echo off
title ZAR - Crear instalador
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build_local.ps1"
if errorlevel 1 pause
