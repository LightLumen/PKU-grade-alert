@echo off
cd /d "%~dp0"
echo Close the Grade Alert GUI before continuing.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_local.ps1"
pause
