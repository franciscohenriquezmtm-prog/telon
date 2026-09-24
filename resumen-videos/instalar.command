#!/bin/bash
# Instalador para macOS/Linux (doble clic en macOS). Crea .venv, instala librerías, pide la clave y crea carpetas.
cd "$(dirname "$0")" || exit 1
echo "== 1/4  Python =="
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "No se encontró Python 3.10 o superior. Instálalo desde https://www.python.org/downloads/ y vuelve a ejecutar."
  exit 1
fi
echo "Python encontrado: $PY"
echo "== 2/4  Entorno y librerías (puede tardar unos minutos la primera vez) =="
[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install -r requirements.txt --quiet
echo "Librerías instaladas (ffmpeg viene incluido)."
read -r -p "¿Instalar también el modo local sin internet (Whisper, ~250 MB)? [s/N] " resp
case "$resp" in s|S) ./.venv/bin/python -m pip install -r requirements-local.txt --quiet; echo "Modo local instalado.";; esac
echo "== 3/4  Clave de la API de Gemini =="
if [ -f .env ]; then
  echo "Ya existe .env; se conserva (bórralo para cambiar la clave)."
else
  echo "Consíguela en https://aistudio.google.com/apikey (proyecto con facturación activa = plan de pago)."
  read -r -p "Pega aquí tu clave y pulsa Enter (o deja vacío para usar solo --local / --simular): " clave
  if [ -n "$clave" ]; then printf 'GEMINI_API_KEY=%s\n' "$clave" > .env; echo "Clave guardada en .env."; else echo "Sin clave: usa ./resumir.command --simular o --local."; fi
fi
echo "== 4/4  Carpetas =="
mkdir -p videos salida
echo "Copia tus videos en: $(pwd)/videos"
echo "LISTO. Para procesar: doble clic en resumir.command (resultados en salida/<nombre del video>/)"
