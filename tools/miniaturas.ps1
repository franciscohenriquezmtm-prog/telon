# Genera miniaturas livianas de los afiches para poder incrustarlas dentro del
# archivo único (dist/telon.html), que no puede cargar imágenes externas.
#
#   powershell -ExecutionPolicy Bypass -File tools\miniaturas.ps1
#   ... -Ancho 480 -Calidad 70      (más nítidas y más pesadas)
#
# Lee assets/img/*.jpg|png y escribe assets/img/mini/<mismo-nombre>.jpg
# Usa System.Drawing (viene con Windows): no instala nada.

param(
  [int]$Ancho = 400,
  [int]$Calidad = 62
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$Proyecto = Split-Path -Parent $PSScriptRoot
$Origen   = Join-Path $Proyecto 'assets\img'
$Destino  = Join-Path $Origen 'mini'

if (-not (Test-Path $Origen)) { Write-Host "No hay afiches todavia. Corre: node tools/actualizar.mjs"; return }
if (-not (Test-Path $Destino)) { New-Item -ItemType Directory -Path $Destino | Out-Null }

# codificador JPEG con calidad ajustable
$codec  = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$params = New-Object System.Drawing.Imaging.EncoderParameters(1)
$params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [int]$Calidad)

# limpia miniaturas de afiches que ya no existen
Get-ChildItem $Destino -Filter *.jpg -ErrorAction SilentlyContinue | ForEach-Object {
  $base = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
  if (-not (Get-ChildItem $Origen -File | Where-Object { [System.IO.Path]::GetFileNameWithoutExtension($_.Name) -eq $base })) {
    Remove-Item $_.FullName -Force
  }
}

$total = 0; $bytes = 0
Get-ChildItem $Origen -File | Where-Object { $_.Extension -match '^\.(jpg|jpeg|png|webp)$' } | ForEach-Object {
  $salida = Join-Path $Destino ([System.IO.Path]::GetFileNameWithoutExtension($_.Name) + '.jpg')

  # se salta las que ya están al día
  if ((Test-Path $salida) -and ((Get-Item $salida).LastWriteTime -gt $_.LastWriteTime)) {
    $total++; $bytes += (Get-Item $salida).Length; return
  }

  try {
    $img = [System.Drawing.Image]::FromFile($_.FullName)
    $alto = [int][Math]::Round($img.Height * ($Ancho / $img.Width))
    $lienzo = New-Object System.Drawing.Bitmap($Ancho, $alto)
    $g = [System.Drawing.Graphics]::FromImage($lienzo)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $g.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    # fondo oscuro: los PNG con transparencia quedan a tono con la app
    $g.Clear([System.Drawing.ColorTranslator]::FromHtml('#170a10'))
    $g.DrawImage($img, 0, 0, $Ancho, $alto)
    $lienzo.Save($salida, $codec, $params)
    $g.Dispose(); $lienzo.Dispose(); $img.Dispose()
    $total++; $bytes += (Get-Item $salida).Length
  } catch {
    Write-Host ("  ! " + $_.Exception.Message)
  }
}

Write-Host ""
Write-Host ("  {0} miniaturas · {1:N0} KB en total · {2}px de ancho, calidad {3}" -f $total, ($bytes/1KB), $Ancho, $Calidad)
Write-Host ""
