# Programa la actualización automática de la cartelera en Windows.
#
#   Instalar:    powershell -ExecutionPolicy Bypass -File tools\programar-actualizacion.ps1
#   Cambiar hora: ... -File tools\programar-actualizacion.ps1 -Hora 09:30
#   Quitar:      ... -File tools\programar-actualizacion.ps1 -Quitar
#
# Crea una tarea que corre todos los días a la hora indicada (y al iniciar sesión si
# el equipo estaba apagado a esa hora), lee los sitios de los teatros y regenera
# assets/cartelera.json. No necesita permisos de administrador.

param(
  [string]$Hora = '08:00',
  [switch]$Quitar
)

$ErrorActionPreference = 'Stop'
$Tarea   = 'Telon - actualizar cartelera'
$Proyecto = Split-Path -Parent $PSScriptRoot
$Node    = (Get-Command node).Source

if ($Quitar) {
  try { Unregister-ScheduledTask -TaskName $Tarea -Confirm:$false; Write-Host "Tarea eliminada." }
  catch { Write-Host "No habia ninguna tarea programada." }
  return
}

$accion = New-ScheduledTaskAction -Execute $Node `
  -Argument 'tools\actualizar.mjs' -WorkingDirectory $Proyecto

$diario  = New-ScheduledTaskTrigger -Daily -At $Hora
$inicio  = New-ScheduledTaskTrigger -AtLogOn

$opciones = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -DontStopIfGoingOnBatteries `
  -AllowStartIfOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $Tarea -Action $accion -Trigger $diario, $inicio `
  -Settings $opciones -Description 'Lee los sitios de los teatros y regenera la cartelera de Telon.' -Force | Out-Null

Write-Host ""
Write-Host "  Listo. La cartelera se actualizara todos los dias a las $Hora."
Write-Host "  Proyecto: $Proyecto"
Write-Host ""
Write-Host "  Probar ahora:   Start-ScheduledTask -TaskName '$Tarea'"
Write-Host "  Ver estado:     Get-ScheduledTaskInfo -TaskName '$Tarea'"
Write-Host "  Quitar:         powershell -ExecutionPolicy Bypass -File tools\programar-actualizacion.ps1 -Quitar"
Write-Host ""
