@echo off
rem Instalador de doble clic para Windows. Ejecuta instalar.ps1 con PowerShell.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
echo.
pause
