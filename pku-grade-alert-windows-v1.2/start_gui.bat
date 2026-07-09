@echo off
setlocal
cd /d "%~dp0"

set "GRADE_ALERT_SETUP_LOCK=%CD%\.setup-lock"
if not exist "%GRADE_ALERT_SETUP_LOCK%" goto acquire_setup_lock
powershell.exe -NoProfile -Command "$p=$env:GRADE_ALERT_SETUP_LOCK; if ((Test-Path -LiteralPath $p) -and (Get-Item -LiteralPath $p).LastWriteTime -lt (Get-Date).AddHours(-1)) { Remove-Item -LiteralPath $p -Force }"

:acquire_setup_lock
2>nul mkdir "%GRADE_ALERT_SETUP_LOCK%"
if errorlevel 1 goto setup_busy

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

if not exist "%CD%\data" mkdir "%CD%\data"
set "GRADE_ALERT_LAUNCHER_LOG=%CD%\data\launcher.log"
"%VENV_DIR%\Scripts\python.exe" -c "import datetime,os,platform,sys; from pathlib import Path; p=Path(os.environ['GRADE_ALERT_LAUNCHER_LOG']); p.open('a',encoding='utf-8').write(f'[{datetime.datetime.now().astimezone().isoformat(timespec=\"seconds\")}] batch selected | executable={sys.executable} | prefix={sys.prefix} | base_prefix={sys.base_prefix} | python={platform.python_version()}\n')"
if errorlevel 1 goto setup_failed

echo [2/4] Checking pip and dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo Repairing the local pip installation...
    "%VENV_DIR%\Scripts\python.exe" -m ensurepip --upgrade --default-pip
    if errorlevel 1 goto setup_failed
)
"%VENV_DIR%\Scripts\python.exe" -c "import playwright" >nul 2>nul
if errorlevel 1 (
    "%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed once. Repairing pip and retrying...
        "%VENV_DIR%\Scripts\python.exe" -m ensurepip --upgrade --default-pip
        if errorlevel 1 goto setup_failed
        "%VENV_DIR%\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input -r requirements.txt
        if errorlevel 1 goto setup_failed
    )
)

echo [3/4] Checking Windows, Edge, configuration, and disk space...
"%VENV_DIR%\Scripts\python.exe" environment_check.py --strict
if errorlevel 1 goto setup_failed

if "%GRADE_ALERT_SETUP_ONLY%"=="1" (
    "%VENV_DIR%\Scripts\python.exe" grade_alert_gui.py --smoke-test
    if errorlevel 1 goto setup_failed
    goto setup_succeeded
)

echo [4/4] Starting Grade Alert...
if not exist "%VENV_DIR%\Scripts\pythonw.exe" goto setup_failed
set "GRADE_ALERT_PYTHONW=%VENV_DIR%\Scripts\pythonw.exe"
set "GRADE_ALERT_GUI_SCRIPT=%CD%\launch_gui.pyw"
set "GRADE_ALERT_APP_DIR=%CD%"
powershell.exe -NoProfile -Command "Start-Process -FilePath $env:GRADE_ALERT_PYTHONW -ArgumentList @($env:GRADE_ALERT_GUI_SCRIPT) -WorkingDirectory $env:GRADE_ALERT_APP_DIR"
if errorlevel 1 goto setup_failed
goto setup_succeeded

:setup_succeeded
2>nul rmdir "%GRADE_ALERT_SETUP_LOCK%"
exit /b 0

:no_python
2>nul rmdir "%GRADE_ALERT_SETUP_LOCK%"
echo.
echo Python 3.10 or newer was not found. Install it from python.org and enable Add Python to PATH.
pause
exit /b 1

:setup_failed
2>nul rmdir "%GRADE_ALERT_SETUP_LOCK%"
echo.
echo Setup failed. Check the network, Python installation, and the error above.
pause
exit /b 1

:setup_busy
echo.
echo Another Grade Alert setup or startup is already running in this folder.
echo Wait for that window to finish before double-clicking again.
pause
exit /b 2
