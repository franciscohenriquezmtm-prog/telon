# Instalador para Windows (PowerShell). Se ejecuta con doble clic en "instalar.bat".
# Qué hace: comprueba Python, crea un entorno aislado (.venv), instala las librerías,
# pide la clave de Gemini una sola vez (la guarda en .env) y crea las carpetas videos/ y salida/.

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -Path $PSScriptRoot

function Titulo($t) { Write-Host ""; Write-Host "== $t ==" -ForegroundColor Cyan }

Titulo "1/4  Python"
$python = $null
$candidatos = @(@("py", "-3"), @("python"), @("python3"))
foreach ($c in $candidatos) {
    $exe = $c[0]
    $extra = @()
    if ($c.Length -gt 1) { $extra = $c[1..($c.Length - 1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $version = & $exe @extra -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            $partes = "$version".Trim().Split(" ")
            if ([int]$partes[0] -eq 3 -and [int]$partes[1] -ge 10) { $python = $c; break }
        }
    } catch { }
}
if (-not $python) {
    Write-Host "No se encontró Python 3.10 o superior." -ForegroundColor Yellow
    Write-Host "Se abrirá la página de descarga. Instálalo marcando 'Add python.exe to PATH' y vuelve a ejecutar instalar.bat."
    Start-Process "https://www.python.org/downloads/windows/"
    exit 1
}
$pyExe = $python[0]
$pyArgs = @()
if ($python.Length -gt 1) { $pyArgs = $python[1..($python.Length - 1)] }
Write-Host "Python encontrado: $($python -join ' ')"

Titulo "2/4  Entorno y librerías (puede tardar unos minutos la primera vez)"
if (-not (Test-Path ".venv")) { & $pyExe @pyArgs -m venv .venv }
$pip = ".\.venv\Scripts\python.exe"
& $pip -m pip install --upgrade pip --quiet
& $pip -m pip install -r requirements.txt --quiet
Write-Host "Librerías instaladas (ffmpeg viene incluido, no hay que instalar nada más)."

$respuesta = Read-Host "¿Instalar también el modo local sin internet (Whisper, ~250 MB)? [s/N]"
if ($respuesta -match "^[sS]") { & $pip -m pip install -r requirements-local.txt --quiet; Write-Host "Modo local instalado." }

Titulo "3/4  Clave de la API de Gemini"
if (Test-Path ".env") {
    Write-Host "Ya existe un archivo .env; se conserva. (Bórralo si quieres cambiar la clave.)"
} else {
    Write-Host "Consíguela en https://aistudio.google.com/apikey (con un proyecto con facturación activa = plan de pago)."
    $clave = Read-Host "Pega aquí tu clave y pulsa Enter (o deja vacío para usar solo --local / --simular)"
    if ($clave.Trim() -ne "") {
        "GEMINI_API_KEY=$($clave.Trim())" | Out-File -FilePath ".env" -Encoding utf8
        Write-Host "Clave guardada en .env (ese archivo no se comparte ni se sube a ningún lado)."
    } else {
        Write-Host "Sin clave: podrás usar 'resumir.bat --simular' y 'resumir.bat --local'."
    }
}

Titulo "4/4  Carpetas"
New-Item -ItemType Directory -Force -Path "videos" | Out-Null
New-Item -ItemType Directory -Force -Path "salida" | Out-Null
Write-Host "Copia tus videos (.mov, .mp4, ...) en la carpeta:  $PSScriptRoot\videos"
Write-Host ""
Write-Host "LISTO. Para procesar: doble clic en resumir.bat  (los resultados quedan en salida\<nombre del video>\)" -ForegroundColor Green
