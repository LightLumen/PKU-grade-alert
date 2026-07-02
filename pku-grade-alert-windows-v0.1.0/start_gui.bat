@echo off
setlocal
cd /d "%~dp0"

if defined GRADE_ALERT_VENV (
    set "VENV_DIR=%GRADE_ALERT_VENV%"
) else (
    set "VENV_DIR=%CD%\.venv"
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/4] Creating the local Python environment. First run may take a few minutes...
    py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%VENV_DIR%"
    ) else (
        python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
        if errorlevel 1 goto no_python
        python -m venv "%VENV_DIR%"
    )
    if errorlevel 1 goto setup_failed
)

echo [2/4] Checking dependencies...
"%VENV_DIR%\Scripts\python.exe" -c "import playwright" >nul 2>nul
if errorlevel 1 (
    "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_failed
)

echo [3/4] Checking Windows, Edge, configuration, and disk space...
"%VENV_DIR%\Scripts\python.exe" environment_check.py --strict
if errorlevel 1 goto setup_failed

if "%GRADE_ALERT_SETUP_ONLY%"=="1" (
    "%VENV_DIR%\Scripts\python.exe" grade_alert_gui.py --smoke-test
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo [4/4] Starting Grade Alert...
start "" "%VENV_DIR%\Scripts\pythonw.exe" "%CD%\grade_alert_gui.py"
exit /b 0

:no_python
echo.
echo Python 3.10 or newer was not found. Install it from python.org and enable Add Python to PATH.
pause
exit /b 1

:setup_failed
echo.
echo Setup failed. Check the network, Python installation, and the error above.
pause
exit /b 1
