@echo off
setlocal
cd /d "%~dp0"
set "NIGHTFRAME_HOME=%~dp0"
if exist "%~dp0NightFrame.exe" (
    start "" "%~dp0NightFrame.exe"
) else if exist "%~dp0NightFrame\NightFrame.exe" (
    start "" "%~dp0NightFrame\NightFrame.exe"
) else if exist "%~dp0work\research_env\Scripts\pythonw.exe" (
    start "" "%~dp0work\research_env\Scripts\pythonw.exe" "%~dp0desktop\app.py"
) else (
    echo Run setup_windows.ps1 first.
    pause
)
