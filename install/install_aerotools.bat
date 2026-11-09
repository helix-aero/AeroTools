@echo off
setlocal
title AeroTools - installation
rem Installs the Aero menu in Metashape and the code the tools share, nothing else (about 1 MB on the stable version,
rem about 26 MB on the dev version). The tools are picked afterwards in Metashape,
rem with Aero > Install plugins...; the AI detection of Aero-GCP is set up from there too.
rem Root = parent folder of install\ (without a trailing backslash).
for %%I in ("%~dp0..") do set "AT_ROOT=%%~fI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_core.ps1" -Root "%AT_ROOT%" %*
set "AT_RC=%ERRORLEVEL%"
echo.
pause
exit /b %AT_RC%
