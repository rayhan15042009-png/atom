@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Atom is not set up yet. Run setup_atom.bat first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" atom.py %*
if errorlevel 1 pause
