@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title SteadyLink
cd /d "%~dp0"
python steadylink.py
if errorlevel 1 (
  echo.
  echo [error] exit code: %errorlevel%
  pause >nul
)
