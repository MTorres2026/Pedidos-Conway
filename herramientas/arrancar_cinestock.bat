@echo off
chcp 65001 >nul
setlocal
title CineStock - PC del cine

REM ============================================================
REM  ARRANCA CineStock (ventana + receptor HTTP en puerto 8765).
REM
REM  No lleva rutas fijas: funciona en cualquier PC, porque usa
REM  la carpeta donde esta este mismo archivo.
REM
REM  IMPORTANTE: usar ESTE arranque, NO el CineStock.exe de dist/,
REM  porque el .exe es una compilacion antigua y NO tiene los
REM  arreglos del robot v6c ni las correcciones ERP.
REM ============================================================

cd /d "%~dp0.."

if not exist "src\main.py" (
  echo ERROR: no encuentro src\main.py
  echo Este .bat debe estar dentro de la carpeta herramientas\ del proyecto.
  goto :fin
)

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: no encuentro Python en el PATH.
  echo Instala Python 3 y marca "Add Python to PATH" durante la instalacion.
  goto :fin
)

echo.
echo ============================================================
echo   ARRANCANDO CINESTOCK
echo ============================================================
echo.
echo   Deja esta ventana ABIERTA mientras uses el programa.
echo   El receptor escucha en el puerto 8765.
echo   Para cerrar, cierra la ventana de CineStock y pulsa Ctrl+C aqui.
echo.
echo ============================================================
echo.

python src\main.py

echo.
echo CineStock se ha cerrado.
echo.

:fin
pause
