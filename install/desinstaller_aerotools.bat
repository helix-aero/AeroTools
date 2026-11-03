@echo off
setlocal
title Desinstallation AeroTools
echo.
if "%LOCALAPPDATA%"=="" goto err_env
set "AT_DST=%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts\aerotools_launcher.py"
if not exist "%AT_DST%" goto absent
del "%AT_DST%"
if exist "%AT_DST%" goto err_del
echo Lanceur supprime : %AT_DST%
echo Le menu Aero disparaitra au prochain demarrage de Metashape.
echo Le code du serveur et les reglages du poste ne sont pas touches.
goto fin

:absent
echo Aucun lanceur AeroTools a supprimer : %AT_DST%
goto fin

:err_env
echo ERREUR : la variable LOCALAPPDATA n'est pas definie.
goto fin

:err_del
echo ERREUR : impossible de supprimer %AT_DST%

:fin
echo.
pause
