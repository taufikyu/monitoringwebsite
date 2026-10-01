@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

:: Deteksi python dari virtual environment (env) atau shared flask env atau system python
if exist "env\Scripts\python.exe" (
    set "PYTHON_CMD=env\Scripts\python.exe"
) else if exist "..\flask\env\Scripts\python.exe" (
    set "PYTHON_CMD=..\flask\env\Scripts\python.exe"
) else (
    set "PYTHON_CMD=python"
)

:: Cek argument
set "FIRST_ARG=%~1"

if /i "%FIRST_ARG%"=="reload" (
    echo ====================================================================
    echo [RUNWEB] Menjalankan Flask Server dengan AUTO-RELOAD Aktif...
    echo ====================================================================
    "%PYTHON_CMD%" -u app.py reload %*
) else if /i "%FIRST_ARG%"=="--reload" (
    echo ====================================================================
    echo [RUNWEB] Menjalankan Flask Server dengan AUTO-RELOAD Aktif...
    echo ====================================================================
    "%PYTHON_CMD%" -u app.py reload %*
) else (
    echo ====================================================================
    echo [RUNWEB] Menjalankan Flask Server (DEFAULT: TANPA RELOAD / STATIC MODE)
    echo  Tip: Jalankan "runweb.bat reload" jika ingin auto-reload saat edit code.
    echo ====================================================================
    "%PYTHON_CMD%" -u app.py %*
)

if errorlevel 1 (
    echo.
    echo [ERROR] Server berhenti dengan error code: %errorlevel%
    pause
)
