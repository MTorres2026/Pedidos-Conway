@echo off
REM ============================================================
REM  Busca el python.exe que debe usar CineStock y lo deja en la
REM  variable CINESTOCK_PY. Pensado para PCs con WINPYTHON, donde
REM  "python" NO suele estar en el PATH del sistema.
REM
REM  Uso desde otro .bat:
REM      call "%~dp0_buscar_python.bat"
REM      "%CINESTOCK_PY%" src\main.py
REM
REM  Orden de busqueda:
REM    1) Primer argumento del .bat que nos llama (ruta a python.exe)
REM    2) Variable de entorno CINESTOCK_PYTHON
REM    3) "python" en el PATH
REM    4) Carpetas WPy* de WinPython en sitios habituales
REM ============================================================

set "CINESTOCK_PY="

REM 1) Primer argumento (ruta directa a python.exe)
if not "%~1"=="" if exist "%~1" (
  set "CINESTOCK_PY=%~1"
  goto :encontrado
)

REM 2) Variable de entorno CINESTOCK_PYTHON
if defined CINESTOCK_PYTHON if exist "%CINESTOCK_PYTHON%" (
  set "CINESTOCK_PY=%CINESTOCK_PYTHON%"
  goto :encontrado
)

REM 3) python en el PATH (funciona si se abre desde "WinPython Command Prompt")
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined CINESTOCK_PY set "CINESTOCK_PY=%%P"
)
if defined CINESTOCK_PY goto :encontrado

REM 4) WinPython: buscar python.exe dentro de carpetas WPy*
for %%R in (
  "%~dp0.."
  "%~dp0..\.."
  "%USERPROFILE%"
  "%USERPROFILE%\Downloads"
  "%USERPROFILE%\Desktop"
  "C:\"
  "D:\"
) do (
  for /d %%D in ("%%~R\WPy*") do (
    for /f "delims=" %%P in ('dir /b /s "%%D\python.exe" 2^>nul') do (
      if not defined CINESTOCK_PY set "CINESTOCK_PY=%%P"
    )
  )
)

:encontrado
exit /b 0
