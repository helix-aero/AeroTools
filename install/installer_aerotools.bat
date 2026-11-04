@echo off
setlocal
title AeroTools - installation
rem Un seul installeur : menu Aero dans Metashape + detection IA (Python, torch, ultralytics).
rem Tout est fait par install_all.ps1, qui affiche "Please wait..." pendant les etapes longues.
rem Racine = dossier parent de install\ (sans antislash final).
for %%I in ("%~dp0..") do set "AT_ROOT=%%~fI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_all.ps1" -Root "%AT_ROOT%" %*
set "AT_RC=%ERRORLEVEL%"
echo.
pause
exit /b %AT_RC%
