@echo off
chcp 65001 >nul
setlocal
title CineStock - Aplicar actualizacion de hoy

REM ============================================================
REM  APLICA SOLO los archivos cambiados hoy sobre una instalacion
REM  existente de CineStock (por ejemplo la del cine con WinPython).
REM
REM  NO toca: .env, data/, WinPython, dist/, ni ninguna credencial.
REM  NO instala nada. NO hace git pull.
REM
REM  Como usarlo:
REM    1) Descomprime ACTUALIZACION_CINE.zip DENTRO de la carpeta
REM       del proyecto del cine (donde esta src\main.py).
REM    2) Haz doble clic en APLICAR.bat.
REM    3) Cuando acabe, ejecuta COMPROBAR.bat.
REM ============================================================

set "AQUI=%~dp0"
set "ORIGEN=%~dp0archivos"

REM Detectar la raiz del proyecto: la carpeta que contiene src\main.py
set "RAIZ="
if exist "%~dp0..\src\main.py" set "RAIZ=%~dp0.."
if not defined RAIZ if exist "%~dp0src\main.py" set "RAIZ=%~dp0"

if not defined RAIZ (
  echo.
  echo ERROR: no encuentro la carpeta del proyecto.
  echo.
  echo Coloca la carpeta ACTUALIZACION_CINE DENTRO de la carpeta de
  echo CineStock ^(esa donde esta src\main.py^) y vuelve a ejecutar este .bat.
  echo.
  goto :fin
)

if not exist "%ORIGEN%\src\robot_erp.py" (
  echo ERROR: no encuentro la carpeta "archivos" con la actualizacion.
  echo El ZIP debe descomprimirse completo, manteniendo las carpetas.
  goto :fin
)

for /f "tokens=1-3 delims=/ " %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "MARCA=%%a"
set "RESPALDO=%RAIZ%\RESPALDO_antes_actualizar_%MARCA%"

echo.
echo ============================================================
echo   ACTUALIZACION DE HOY - CineStock
echo ============================================================
echo   Proyecto : %RAIZ%
echo   Origen   : %ORIGEN%
echo   Respaldo : %RESPALDO%
echo ============================================================
echo.
echo Se van a copiar SOLO los archivos cambiados hoy.
echo NO se tocara .env, data/, WinPython ni dist/.
echo.
pause

echo.
echo [1/3] Guardando copia de seguridad de los archivos que se van a pisar...
mkdir "%RESPALDO%" 2>nul
for /r "%ORIGEN%" %%F in (*) do (
  set "REL=%%~pnxF"
  call :respaldar "%%F"
)
echo       Copia guardada en: %RESPALDO%

echo.
echo [2/3] Copiando los archivos nuevos...
xcopy /E /Y /I /Q "%ORIGEN%\*" "%RAIZ%\" >nul
if errorlevel 1 (
  echo.
  echo ERROR al copiar. No sigas; avisa y revisa el respaldo.
  goto :fin
)
echo       Archivos copiados.

echo.
echo [3/3] Comprobando la version del robot...
call "%RAIZ%\herramientas\_buscar_python.bat"
if defined CINESTOCK_PY (
  "%CINESTOCK_PY%" "%RAIZ%\src\comprobar_version_robot.py"
) else (
  echo       No encuentro Python aqui. Abre el "WinPython Command Prompt"
  echo       y ejecuta: python src\comprobar_version_robot.py
)

echo.
echo ============================================================
echo   ACTUALIZACION APLICADA.
echo.
echo   Debe decir: v6c
echo.
echo   Siguiente paso: ejecuta COMPROBAR.bat
echo   ^(o: python src\comprobar_instalacion.py^)
echo.
echo   Si algo va mal, los archivos anteriores estan en:
echo   %RESPALDO%
echo ============================================================
goto :fin

:respaldar
set "RUTA=%~1"
set "RELATIVA=%RUTA:*archivos\=%"
if not exist "%RAIZ%\%RELATIVA%" exit /b 0
for %%D in ("%RESPALDO%\%RELATIVA%") do mkdir "%%~dpD" 2>nul
copy /Y "%RAIZ%\%RELATIVA%" "%RESPALDO%\%RELATIVA%" >nul
exit /b 0

:fin
echo.
pause
