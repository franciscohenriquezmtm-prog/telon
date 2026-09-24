#!/bin/bash
# Procesa todos los videos de videos/ y deja los resultados en salida/. Acepta las opciones de resumir_videos.py.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then echo "Primero ejecuta instalar.command (no existe .venv)."; exit 2; fi
./.venv/bin/python resumir_videos.py "$@"
codigo=$?
echo "Código de salida: $codigo (0 = todo bien, 1 = algún video falló, 2 = error de configuración)"
exit $codigo
