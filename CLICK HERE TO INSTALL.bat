@echo off
rem Double-click here to install AeroTools (Aero menu in Metashape).
title AeroTools - installation
if not exist "%~dp0install\install_core.ps1" (
    echo.
    echo   The installer cannot find its files next to this one.
    echo   You are probably running it from inside the zip file: extract the WHOLE zip first
    echo   ^(right-click the zip, Extract All^), then double-click this file in the extracted folder.
    echo.
    pause
    exit /b 1
)
call "%~dp0install\install_aerotools.bat" %*
