@echo off
setlocal
title Actualizar CineStock
if not exist "C:\WPy64-313\WPy64-313150\python\python.exe" (
    echo No se encuentra el Python que usa tu BAT habitual. Envia una foto de esta ventana en el chat.
    pause
    exit /b 1
)
"C:\WPy64-313\WPy64-313150\python\python.exe" "%~dp0actualizar.py"
set "resultado=%errorlevel%"
echo.
pause
exit /b %resultado%
