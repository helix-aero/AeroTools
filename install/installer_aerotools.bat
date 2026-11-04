@echo off
setlocal
title Installation AeroTools
echo.
echo === Installation d'AeroTools sur ce poste ===
echo.

rem Racine = dossier parent de install\ (sans antislash final).
for %%I in ("%~dp0..") do set "AT_ROOT=%%~fI"
set "AT_SRC=%~dp0aerotools_launcher.py.tpl"

if "%LOCALAPPDATA%"=="" goto err_env
set "AT_DIR=%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts"
set "AT_DST=%AT_DIR%\aerotools_launcher.py"

if not exist "%AT_ROOT%\aerotools\bootstrap.py" goto err_root
if not exist "%AT_SRC%" goto err_tpl

if not exist "%AT_DIR%" mkdir "%AT_DIR%"
if not exist "%AT_DIR%" goto err_dir

rem Le remplacement est litteral (String.Replace), donc sans souci pour les
rem antislashs ou les chemins UNC. Ecriture en UTF-8 sans BOM.
rem Codes de retour : 0 ecrit, 10 identique (rien fait), 11 ecrit apres sauvegarde .bak
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $t=[IO.File]::ReadAllText($env:AT_SRC); $c=$t.Replace('@@AEROTOOLS_ROOT@@',$env:AT_ROOT); $u=New-Object Text.UTF8Encoding($false); $rc=0; if(Test-Path -LiteralPath $env:AT_DST){ if([IO.File]::ReadAllText($env:AT_DST) -ceq $c){ exit 10 }; Copy-Item -LiteralPath $env:AT_DST -Destination ($env:AT_DST+'.bak') -Force; $rc=11 }; [IO.File]::WriteAllText($env:AT_DST,$c,$u); exit $rc"
if errorlevel 12 goto err_ps
if errorlevel 11 goto ok_bak
if errorlevel 10 goto ok_same
if errorlevel 1 goto err_ps
echo Lanceur ecrit      : %AT_DST%
goto fin_ok

:ok_bak
echo Lanceur mis a jour : %AT_DST%
echo Ancienne version   : %AT_DST%.bak
goto fin_ok

:ok_same
echo Lanceur deja a jour, rien a faire : %AT_DST%
goto fin_ok

:fin_ok
echo Racine AeroTools   : %AT_ROOT%
echo.
echo Etape suivante : fermer puis relancer Metashape, menu "Aero".
echo.
echo La detection IA a besoin de Python et de torch (environ 3 Go a telecharger, une seule fois).
echo Le pointage a la main marche sans.
choice /c ON /m "Installer aussi la detection IA maintenant ? O = oui, N = non (plus tard : install\installer_ia.bat)"
if not errorlevel 2 call "%~dp0installer_ia.bat"
echo.
if exist "%AT_DIR%\active_marker_loader.py" echo ATTENTION : l'ancien script active_marker_loader.py est present dans le dossier scripts.
if exist "%AT_DIR%\metashape_active_marker" echo ATTENTION : l'ancien dossier metashape_active_marker est present dans le dossier scripts.
if exist "%AT_DIR%\active_marker_loader.py" echo Il peut coexister avec AeroTools, mais il vaut mieux le retirer a la main
if exist "%AT_DIR%\active_marker_loader.py" echo pour ne pas avoir deux docks. Rien n'a ete supprime par cet installeur.
echo.
pause
exit /b 0

:err_env
echo ERREUR : la variable LOCALAPPDATA n'est pas definie.
goto fin_err
:err_root
echo ERREUR : %AT_ROOT%\aerotools\bootstrap.py introuvable.
echo Lancer l'installeur depuis le dossier install\ du depot AeroTools.
goto fin_err
:err_tpl
echo ERREUR : modele introuvable : %AT_SRC%
goto fin_err
:err_dir
echo ERREUR : impossible de creer %AT_DIR%
goto fin_err
:err_ps
echo ERREUR : l'ecriture du lanceur a echoue (PowerShell).
goto fin_err

:fin_err
echo.
pause
exit /b 1
