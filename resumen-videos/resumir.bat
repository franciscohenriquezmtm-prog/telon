@echo off
rem Procesa todos los videos de la carpeta videos\ y deja los resultados en salida\.
rem Acepta las mismas opciones que resumir_videos.py, por ejemplo:  resumir.bat --local
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Primero ejecuta instalar.bat ^(no existe el entorno .venv^).
    pause
    exit /b 2
)
".venv\Scripts\python.exe" resumir_videos.py %*
echo.
echo Codigo de salida: %ERRORLEVEL%  ^(0 = todo bien, 1 = algun video fallo, 2 = error de configuracion^)
pause
