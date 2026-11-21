@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python setup_atom.py %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 setup_atom.py %*
    ) else (
        echo Python was not found.
        echo Install Python 3.11 from https://python.org/downloads
        echo and tick "Add python.exe to PATH", then run this again.
    )
)

echo.
pause
