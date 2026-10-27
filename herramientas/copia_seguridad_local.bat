@echo off
chcp 65001 >nul
setlocal
title Copia de seguridad LOCAL - Pedidos-Conway

REM ============================================================
REM  COPIA DE SEGURIDAD LOCAL (NO sube nada a internet / GitHub).
REM
REM  Guarda el codigo y data\ (pedidos y catalogo) en un ZIP
REM  fechado, en C:\Users\Marku\Desktop\BACKUPS-CineStock
REM
REM  NO incluye .git, build\, dist\ ni __pycache__.
REM  NO incluye .env: contiene ERP_USER / ERP_PASS.
REM  Guarda el .env tu aparte, en un sitio seguro y privado.
REM
REM  Es seguro ejecutarlo ANTES de una carrera del robot: no toca Git.
REM ============================================================

set "PROYECTO=C:\Users\Marku\Desktop\PROYECTOS-IA\CineStock-Pedidos\claude-prueba\Pedidos-Conway"
set "DESTINO=C:\Users\Marku\Desktop\BACKUPS-CineStock"
set "STAGE=%TEMP%\CineStock_copia_tmp"

if not exist "%PROYECTO%\.git" (
  echo ERROR: no encuentro el proyecto en:
  echo   %PROYECTO%
  echo Revisa la ruta PROYECTO al principio de este archivo.
  goto :fin
)

cd /d "%PROYECTO%"
if not exist "%DESTINO%" mkdir "%DESTINO%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "MARCA=%%i"
set "ZIP=%DESTINO%\copia_%MARCA%.zip"

echo.
echo [1/3] Preparando archivos...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%"

robocopy "%PROYECTO%" "%STAGE%" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD .git build dist __pycache__ .venv venv node_modules /XF .env .env.* *.pyc >nul
if errorlevel 8 (
  echo ERROR al copiar los archivos. No se ha creado la copia.
  goto :fin
)

echo [2/3] Creando el ZIP...
powershell -NoProfile -Command "Compress-Archive -Path '%STAGE%\*' -DestinationPath '%ZIP%' -CompressionLevel Optimal -Force"
if errorlevel 1 (
  echo ERROR al crear el ZIP.
  goto :fin
)

echo [3/3] Limpiando...
rmdir /s /q "%STAGE%"

echo.
echo ============================================================
echo   COPIA CREADA (solo en tu PC, NO subida a GitHub):
echo.
echo   %ZIP%
echo.
echo   Incluye data\ (pedidos y catalogo).
echo   NO incluye .env: guarda tus credenciales aparte.
echo ============================================================

:fin
echo.
pause
