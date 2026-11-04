@echo off
setlocal
title Detection IA d'AeroTools
rem Racine = dossier parent de install\ (sans antislash final).
for %%I in ("%~dp0..") do set "AT_ROOT=%%~fI"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer_ia.ps1" -Root "%AT_ROOT%" %*
set "AT_RC=%ERRORLEVEL%"
echo.
pause
exit /b %AT_RC%
