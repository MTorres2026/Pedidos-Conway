@echo off
chcp 65001 >nul
setlocal
title CineStock - Comprobar instalacion (WinPython)

REM ============================================================
REM  Ejecuta src\comprobar_instalacion.py con el Python correcto.
REM  Compatible con WINPYTHON (busca python.exe automaticamente).
REM
REM  Opcional: pasar la ruta de python.exe como primer argumento.
REM ============================================================

cd /d "%~dp0.."

if not exist "src\comprobar_instalacion.py" (
  echo ERROR: no encuentro src\comprobar_instalacion.py
  echo Trae primero los archivos de hoy desde GitHub / USB.
  goto :fin
)

call "%~dp0_buscar_python.bat" %1

if not defined CINESTOCK_PY (
  echo ERROR: no encuentro Python.
  echo Abre el "WinPython Command Prompt" y ejecuta:
  echo     python src\comprobar_instalacion.py
  goto :fin
)

echo.
echo Python usado: %CINESTOCK_PY%
echo.
"%CINESTOCK_PY%" src\comprobar_instalacion.py

echo.
:fin
pause
