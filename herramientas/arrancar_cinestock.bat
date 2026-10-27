@echo off
chcp 65001 >nul
setlocal
title CineStock - PC del cine (WinPython)

REM ============================================================
REM  ARRANCA CineStock (ventana + receptor HTTP en puerto 8765).
REM
REM  Compatible con WINPYTHON: busca el python.exe automaticamente.
REM  Si no lo encuentra, puedes pasar la ruta como primer argumento:
REM
REM    arrancar_cinestock.bat "C:\WPy64-31410\python-3.14.amd64\python.exe"
REM
REM  o abrir el "WinPython Command Prompt" y ejecutar: python src\main.py
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

call "%~dp0_buscar_python.bat" %1

if not defined CINESTOCK_PY (
  echo.
  echo ERROR: no encuentro Python.
  echo.
  echo Haz UNA de estas dos cosas:
  echo   1^) Abre el "WinPython Command Prompt" y ejecuta: python src\main.py
  echo   2^) Pasa la ruta de python.exe a este .bat, por ejemplo:
  echo      arrancar_cinestock.bat "C:\WPy64-XXXX\python-3.XX.amd64\python.exe"
  echo.
  goto :fin
)

echo.
echo ============================================================
echo   ARRANCANDO CINESTOCK
echo   Python: %CINESTOCK_PY%
echo ============================================================
echo.
echo   Deja esta ventana ABIERTA mientras uses el programa.
echo   El receptor escucha en el puerto 8765.
echo   Para cerrar, cierra la ventana de CineStock y pulsa Ctrl+C aqui.
echo.
echo ============================================================
echo.

"%CINESTOCK_PY%" src\main.py

echo.
echo CineStock se ha cerrado.
echo.

:fin
pause
