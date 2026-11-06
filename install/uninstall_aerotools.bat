@echo off
setlocal
title AeroTools - uninstall
echo.
if "%LOCALAPPDATA%"=="" goto err_env
set "AT_DST=%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts\aerotools_launcher.py"
if not exist "%AT_DST%" goto absent
del "%AT_DST%"
if exist "%AT_DST%" goto err_del
echo Launcher removed: %AT_DST%
echo The Aero menu will disappear the next time Metashape starts.
echo The code on the server and the workstation settings are not touched.
goto fin

:absent
echo No AeroTools launcher to remove: %AT_DST%
goto fin

:err_env
echo ERROR: the LOCALAPPDATA variable is not defined.
goto fin

:err_del
echo ERROR: could not delete %AT_DST%

:fin
echo.
pause
