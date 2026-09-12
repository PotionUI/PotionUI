@echo off
rem PotionUI bootstrap CLI (Windows) - mirrors the POSIX `./potionui` shim.
rem This batch file only locates a Python 3.12+ interpreter; everything else
rem - checks, process supervision, readiness polling - lives in
rem scripts\potionui_cli.py so it stays unit-testable. See README.md's
rem quickstart and CLAUDE.md for the project overview.
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYCHECK=import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)"

where py >nul 2>nul
if not errorlevel 1 (
    py -3.13 -c "%PYCHECK%" >nul 2>nul
    if not errorlevel 1 (
        py -3.13 "%SCRIPT_DIR%scripts\potionui_cli.py" %*
        exit /b %ERRORLEVEL%
    )
    py -3.12 -c "%PYCHECK%" >nul 2>nul
    if not errorlevel 1 (
        py -3.12 "%SCRIPT_DIR%scripts\potionui_cli.py" %*
        exit /b %ERRORLEVEL%
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "%PYCHECK%" >nul 2>nul
    if not errorlevel 1 (
        python "%SCRIPT_DIR%scripts\potionui_cli.py" %*
        exit /b %ERRORLEVEL%
    )
)

echo error: PotionUI needs Python 3.12 or newer, but none was found on PATH. 1>&2
echo Install it from https://www.python.org/downloads/windows/ (check "Add python.exe to PATH" 1>&2
echo during setup), or via the "py" launcher, then re-run: potionui doctor 1>&2
exit /b 1
