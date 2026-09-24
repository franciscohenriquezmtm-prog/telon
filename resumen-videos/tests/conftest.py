"""Fixtures compartidas por los tests.

- ``ffmpeg``: ruta al binario ffmpeg (o ``pytest.skip`` si no hay ninguno).
- ``video_prueba``: ruta a un video sintético de ~90 s con 8 escenas (cortes en
  12, 23, 35, 46, 58, 69 y 80 s), rótulos y tonos.  Se genera una vez por sesión
  con ``tools/crear_video_prueba.py``; si no se puede generar, se usa el video de
  ejemplo indicado por la variable de entorno ``VIDEO_PRUEBA`` (si existe).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
for ruta in (RAIZ, RAIZ / "tools"):
    if str(ruta) not in sys.path:
        sys.path.insert(0, str(ruta))


@pytest.fixture(scope="session")
def ffmpeg() -> str:
    try:
        from resumen_videos.video import localizar_ffmpeg
        return localizar_ffmpeg()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ffmpeg no disponible: {exc}")


@pytest.fixture(scope="session")
def video_prueba(tmp_path_factory, ffmpeg) -> Path:
    destino = tmp_path_factory.mktemp("videos") / "prueba_maquina.mp4"
    try:
        from crear_video_prueba import crear_video_prueba  # tools/crear_video_prueba.py
        ruta = Path(crear_video_prueba(destino, duracion=90, escenas=8))
        if ruta.exists() and ruta.stat().st_size > 10_000:
            return ruta
    except Exception as exc:  # noqa: BLE001
        print(f"[conftest] no se pudo generar el video de prueba: {exc}")
    alternativa = os.environ.get("VIDEO_PRUEBA")
    if alternativa and Path(alternativa).exists():
        return Path(alternativa)
    pytest.skip("no hay video de prueba disponible")
