@echo off
setlocal
set "POLIS_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POLIS_POWERSHELL%" (
    echo Windows PowerShell bulunamadi: %POLIS_POWERSHELL%
    pause
    exit /b 1
)
"%POLIS_POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_windows.ps1"
if errorlevel 1 pause