@echo off
setlocal
title AeroTools - outils d'entrainement (optionnel)
rem Installe en plus torch, ultralytics et onnx (environ 3 Go) : seulement pour reentrainer le modele
rem (bouton "Refine my model using my data" des Parametres). La detection n'en a pas besoin.
for %%I in ("%~dp0..") do set "AT_ROOT=%%~fI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_all.ps1" -Root "%AT_ROOT%" -Training %*
set "AT_RC=%ERRORLEVEL%"
echo.
pause
exit /b %AT_RC%
