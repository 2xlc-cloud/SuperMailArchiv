@echo off
setlocal
title SuperMailArchiv
where py >nul 2>nul
if errorlevel 1 (
    python "%~dp0app.py"
) else (
    py -3 "%~dp0app.py"
)
if errorlevel 1 pause
