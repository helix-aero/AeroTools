@echo off
setlocal
title AeroTools - installation
rem Installs the Aero menu in Metashape and the code the tools share, nothing else (about 1 MB on the stable version,
rem about 26 MB on the dev version). The tools are picked afterwards in Metashape,
rem with Aero > Install plugins...; the AI detection of Aero-GCP is set up from there too.
rem The root is not passed on the command line: a path ending with a backslash (D:\) would swallow the closing quote; install_core.ps1 finds it itself.
if not exist "%~dp0install_core.ps1" (
    echo.
    echo   install_core.ps1 not found: extract the WHOLE zip first, then run this installer from the extracted folder.
    echo.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_core.ps1" %*
set "AT_RC=%ERRORLEVEL%"
echo.
pause
exit /b %AT_RC%
