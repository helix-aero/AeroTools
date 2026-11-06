@echo off
setlocal
title AeroTools - training tools (optional)
rem Additionally installs torch, ultralytics and onnx (about 3 GB; no questions asked, the light engine is left untouched): only needed to retrain the model
rem (the "Refine my model using my data" button in Settings). Detection does not need them.
for %%I in ("%~dp0..") do set "AT_ROOT=%%~fI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_all.ps1" -Root "%AT_ROOT%" -Engine none -Training %*
set "AT_RC=%ERRORLEVEL%"
echo.
pause
exit /b %AT_RC%
