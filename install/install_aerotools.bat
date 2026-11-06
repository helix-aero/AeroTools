@echo off
setlocal
title AeroTools - installation
rem Single installer: Aero menu in Metashape + AI detection (Python, onnxruntime, about 100 MB).
rem Everything is done by install_all.ps1, which shows "Please wait..." during the long steps.
rem Root = parent folder of install\ (without a trailing backslash).
for %%I in ("%~dp0..") do set "AT_ROOT=%%~fI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_all.ps1" -Root "%AT_ROOT%" %*
set "AT_RC=%ERRORLEVEL%"
echo.
pause
exit /b %AT_RC%
