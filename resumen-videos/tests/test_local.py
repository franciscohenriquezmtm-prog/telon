"""Tests de ``resumen_videos.local`` (modo ``--local`` y modo ``--simular``).

Los que necesitan ffmpeg usan las fixtures ``ffmpeg`` y ``video_prueba`` de ``conftest.py``
(se saltan si no hay ffmpeg).  Nada aquí llama a la red: ``faster_whisper`` se sustituye por un
módulo falso inyectado en ``sys.modules``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from resumen_videos import local, video
from resumen_videos.modelos import InfoVideo

CORTES = [12, 23, 35, 46, 58, 69, 80]          # cambios de escena del video de prueba (90 s, 8 escenas)
TOLERANCIA = 1.5
FRASES_CLAVE = ["primero debe verificar que el colimador esté a 10 cm",
                "nunca presione este botón durante la exposición"]
# (inicio, fin, texto) como los devolvería faster-whisper (con el espacio inicial típico).
SEGMENTOS_FALSOS = [
    (2.0, 5.5, " Hola a todos, bienvenidos a este video."),                   # trivial y lejos de un corte
    (13.0, 16.5, " Primero debe verificar que el colimador esté a 10 cm."),
    (25.0, 31.0, " Ahora vamos a encender el equipo. Presione el botón verde y espere 30 segundos."),
    (47.5, 50.5, " Nunca presione este botón durante la exposición."),
    (64.0, 67.5, " Esta pantalla muestra los datos del paciente y debe revisarlos siempre."),
]


def _info_falsa(duracion: float, nombre: str = "video_falso") -> InfoVideo:
    return InfoVideo(ruta=Path("/no/existe.mp4"), nombre=nombre, duracion=duracion, fps=30.0,
                     ancho=1920, alto=1080, tamano_bytes=1_000_000)


def _modulo_whisper_falso(segmentos, *, fallo_carga: Exception | None = None,
                          fallo_transcribir: Exception | None = None, registro: dict | None = None):
    """Módulo ``faster_whisper`` falso con la misma forma que el real (WhisperModel.transcribe)."""
    modulo = types.ModuleType("faster_whisper")

    class WhisperModel:
        def __init__(self, modelo, **kwargs):
            if registro is not None:
                registro["modelo"], registro["kwargs"] = modelo, kwargs
            if fallo_carga is not None:
                raise fallo_carga

        def transcribe(self, audio, **kwargs):
            if registro is not None:
                registro["transcribe"] = {"audio": audio, **kwargs}
            if fallo_transcribir is not None:
                raise fallo_transcribir

            def generador():
                for inicio, fin, texto in segmentos:
                    yield types.SimpleNamespace(start=inicio, end=fin, text=texto)

            return generador(), types.SimpleNamespace(language="es", language_probability=0.97, duration=90.0)

    modulo.WhisperModel = WhisperModel
    return modulo


@pytest.fixture
def registro():
    """Función ``log`` que acumula los mensajes en una lista."""
    lineas: list[str] = []

    def log(texto: str) -> None:
        lineas.append(texto)

    log.lineas = lineas  # type: ignore[attr-defined]
    return log


@pytest.fixture(scope="module")
def info_prueba(ffmpeg, video_prueba) -> InfoVideo:
    return video.obtener_info(video_prueba, ffmpeg, ffprobe=None)


def _cerca_de_corte(t: float) -> bool:
    return any(abs(t - corte) <= TOLERANCIA for corte in CORTES)


def _comprobar_momentos_basicos(momentos, duracion: float) -> None:
    tiempos = [m.tiempo_seg for m in momentos]
    assert tiempos == sorted(tiempos) and len(set(tiempos)) == len(tiempos)
    for m in momentos:
        assert 0 <= m.tiempo_seg <= duracion - 0.5
        assert 1 <= m.importancia <= 5 and m.fuente in {"visual", "audio", "ambos"}
        assert m.seccion and m.seccion.startswith("Parte ") and m.zona is None
        assert m.titulo and m.descripcion and m.puntaje is not None


# ----------------------------------------------------------------------------
# Funciones puras (sin ffmpeg)
# ----------------------------------------------------------------------------
def test_segmentar_frases_por_puntuacion_y_pausas():
    segmentos = [
        {"inicio": 0.0, "fin": 4.0, "texto": " Hola a todos. Hoy veremos el equipo"},
        {"inicio": 4.5, "fin": 7.0, "texto": "de fluoroscopía."},          # continúa la frase (pausa 0.5 s)
        {"inicio": 9.0, "fin": 12.0, "texto": "Vamos a empezar"},          # pausa de 2 s: frase nueva
        {"inicio": 12.5, "fin": 13.0, "texto": " ... "},                   # sin letras: se ignora
        {"inicio": 13.5, "fin": 15.0, "texto": "¿Está listo? Bien."},
    ]
    frases = local.segmentar_frases(segmentos)
    assert [f["texto"] for f in frases] == ["Hola a todos.", "Hoy veremos el equipo de fluoroscopía.",
                                            "Vamos a empezar", "¿Está listo?", "Bien."]
    assert frases[0]["inicio"] == 0.0 and 0 < frases[0]["fin"] < 4.0      # tiempo interpolado dentro del segmento
    assert frases[1]["inicio"] == frases[0]["fin"] and frases[1]["fin"] == 7.0
    assert (frases[2]["inicio"], frases[2]["fin"]) == (9.0, 12.0)
    assert local.segmentar_frases([]) == []


def test_puntuar_frase_y_escena():
    assert local.puntuar_frase("hola a todos") == pytest.approx(0.8)
    assert local.puntuar_frase("hola a todos", cerca_de_escena=True) == pytest.approx(1.8)
    # primero + debe + verificar + "10 cm" = 4 palabras clave; 10 palabras
    assert local.puntuar_frase(FRASES_CLAVE[0]) == pytest.approx(5.5)
    # nunca + presione (conjugación de presionar) = 2; 7 palabras
    assert local.puntuar_frase(FRASES_CLAVE[1]) == pytest.approx(3.2)
    # ajuste + "70 kV" + "2,5 mAs" = 3; 9 palabras
    assert local.puntuar_frase("ajuste el kilovoltaje a 70 kV y 2,5 mAs") == pytest.approx(0.5 + 0.9 + 3)
    assert local.puntuar_frase(" ".join(["palabra"] * 40)) == pytest.approx(2.5)   # 0.1·palabras acotado a 2
    assert local.puntuar_escena(30.3) == pytest.approx(4.0)         # score/10 acotado a 3, más 1
    assert local.puntuar_escena(4.29) == pytest.approx(1.429)
    assert local.puntuar_escena(0.0) == pytest.approx(1.0)


def test_analizar_simulado(registro):
    resultado = local.analizar_simulado(_info_falsa(90.0, "arco_en_c"), log=registro)
    momentos = resultado.momentos
    assert resultado.modo == "simulado" and resultado.uso is None and resultado.avisos
    assert "(SIMULADO)" in resultado.resumen and "(SIMULADO)" in resultado.titulo
    assert len(momentos) == 8                                        # cada max(10 s, 90/8) = 11.25 s
    tiempos = [m.tiempo_seg for m in momentos]
    assert tiempos == sorted(tiempos) and 0 <= tiempos[0] and tiempos[-1] <= 89.5
    assert {m.seccion for m in momentos} == {"Preparación", "Ejecución", "Cierre"}
    assert [m.seccion for m in momentos] == sorted([m.seccion for m in momentos],
                                                   key=["Preparación", "Ejecución", "Cierre"].index)
    zonas = [m.zona for m in momentos if m.zona is not None]
    assert zonas and any(m.zona is None for m in momentos)
    assert all(0 <= z["x"] <= 1 and 0 <= z["y"] <= 1 for z in zonas)
    assert all("(SIMULADO)" in m.titulo and "(SIMULADO)" in m.descripcion for m in momentos)
    assert all(1 <= m.importancia <= 5 and m.fuente in {"visual", "audio", "ambos"} for m in momentos)
    assert registro.lineas
    corto = local.analizar_simulado(_info_falsa(5.0), log=registro)
    assert len(corto.momentos) == 1 and 0 <= corto.momentos[0].tiempo_seg <= 4.5


# ----------------------------------------------------------------------------
# Modo local con el video de prueba
# ----------------------------------------------------------------------------
def test_analizar_local_solo_escenas(ffmpeg, video_prueba, info_prueba, registro, capsys):
    resultado = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo=None, log=registro)
    momentos = resultado.momentos
    assert resultado.modo == "local" and resultado.modelo == "scdet" and resultado.uso is None
    assert resultado.transcripcion is None and resultado.avisos == [] and resultado.resumen
    assert len(momentos) >= 5, [m.tiempo_seg for m in momentos]
    assert all(_cerca_de_corte(m.tiempo_seg) for m in momentos), [m.tiempo_seg for m in momentos]
    assert all(m.fuente == "visual" and m.titulo.startswith("Cambio de plano") for m in momentos)
    _comprobar_momentos_basicos(momentos, info_prueba.duracion)
    assert len({m.seccion for m in momentos}) > 1
    assert registro.lineas and capsys.readouterr().out == ""      # solo informa a través de log


def test_analizar_local_con_whisper_falso(ffmpeg, video_prueba, info_prueba, monkeypatch, registro, capsys):
    llamadas: dict = {}
    monkeypatch.setitem(sys.modules, "faster_whisper", _modulo_whisper_falso(SEGMENTOS_FALSOS, registro=llamadas))
    monkeypatch.setenv("WHISPER_CACHE", str(video_prueba.parent))
    resultado = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo="base", log=registro)
    momentos = resultado.momentos

    assert llamadas["modelo"] == "base"
    assert llamadas["kwargs"] == {"device": "cpu", "compute_type": "int8",
                                  "download_root": str(video_prueba.parent), "local_files_only": False}
    assert llamadas["transcribe"] == {"audio": str(video_prueba), "language": "es", "vad_filter": True}
    assert resultado.modelo == "scdet+faster-whisper:base" and resultado.avisos == []
    assert resultado.transcripcion == [{"inicio": a, "fin": b, "texto": t.strip()} for a, b, t in SEGMENTOS_FALSOS]
    _comprobar_momentos_basicos(momentos, info_prueba.duracion)

    for frase in FRASES_CLAVE:
        encontrados = [m for m in momentos if frase in m.descripcion.lower()]
        assert encontrados, f"falta la frase {frase!r} en {[m.descripcion for m in momentos]}"
        m = encontrados[0]
        assert m.fuente in {"audio", "ambos"} and m.importancia >= 4
        assert m.titulo[0].isupper() and len(m.titulo.split()) <= 9 and not m.titulo.endswith(".")
    assert "ambos" in {m.fuente for m in momentos}
    assert not any("bienvenidos" in m.descripcion for m in momentos)     # frase trivial descartada
    # Un segmento con dos frases se parte por la puntuación: la relevante queda sola en su momento.
    assert any(m.descripcion.startswith("Presione el botón verde") for m in momentos)
    assert capsys.readouterr().out == ""


def test_analizar_local_offline_y_transcripcion_fallida(ffmpeg, video_prueba, info_prueba, monkeypatch, registro):
    llamadas: dict = {}
    modulo = _modulo_whisper_falso([], fallo_transcribir=RuntimeError("audio ilegible"), registro=llamadas)
    monkeypatch.setitem(sys.modules, "faster_whisper", modulo)
    monkeypatch.delenv("WHISPER_CACHE", raising=False)
    resultado = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo="tiny", offline=True,
                                     log=registro)
    assert llamadas["kwargs"]["local_files_only"] is True and llamadas["kwargs"]["download_root"] is None
    assert resultado.modelo == "scdet" and resultado.transcripcion is None
    assert len(resultado.avisos) == 1 and "audio ilegible" in resultado.avisos[0]
    assert len(resultado.momentos) >= 5 and all(m.fuente == "visual" for m in resultado.momentos)


def test_analizar_local_modelo_no_disponible(ffmpeg, video_prueba, info_prueba, monkeypatch, registro):
    error = OSError("Unable to open file 'model.bin' in model 'Systran/faster-whisper-base'")
    monkeypatch.setitem(sys.modules, "faster_whisper", _modulo_whisper_falso([], fallo_carga=error))
    resultado = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo="base", log=registro)
    assert resultado.modelo == "scdet" and resultado.transcripcion is None
    assert len(resultado.avisos) == 1
    aviso = resultado.avisos[0]
    assert "OSError" in aviso and "model.bin" in aviso and "download_model('base')" in aviso
    assert len(resultado.momentos) >= 5 and all(_cerca_de_corte(m.tiempo_seg) for m in resultado.momentos)
    assert any("AVISO" in linea for linea in registro.lineas)


def test_analizar_local_sin_faster_whisper(ffmpeg, video_prueba, info_prueba, monkeypatch, registro):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)      # provoca ImportError en el import perezoso
    resultado = local.analizar_local(video_prueba, info_prueba, ffmpeg, log=registro)
    assert resultado.modelo == "scdet" and resultado.transcripcion is None
    assert len(resultado.avisos) == 1 and "pip install faster-whisper" in resultado.avisos[0]
    assert len(resultado.momentos) >= 5


def test_analizar_local_filtros(ffmpeg, video_prueba, info_prueba, registro):
    completo = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo=None, log=registro).momentos
    limitado = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo=None, max_momentos=3,
                                    log=registro)
    assert len(limitado.momentos) == 3 and limitado.avisos
    tiempos = [m.tiempo_seg for m in limitado.momentos]
    assert tiempos == sorted(tiempos)
    mejores = sorted(completo, key=lambda m: (-m.puntaje, m.tiempo_seg))[:3]
    assert sorted(tiempos) == sorted(m.tiempo_seg for m in mejores)
    minimo = max(m.importancia for m in completo)
    exigente = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo=None,
                                    importancia_minima=minimo, log=registro)
    assert exigente.momentos and all(m.importancia >= minimo for m in exigente.momentos)
    assert len(exigente.momentos) < len(completo) and exigente.avisos


def test_analizar_local_rejilla_sin_candidatos(ffmpeg, video_prueba, info_prueba, registro):
    resultado = local.analizar_local(video_prueba, info_prueba, ffmpeg, whisper_modelo=None, umbral_escena=100.0,
                                     log=registro)
    momentos = resultado.momentos
    assert [m.tiempo_seg for m in momentos] == [15.0, 45.0, 75.0]   # cada max(30 s, 90/12)
    assert all(m.titulo == f"Vista a los {m.tiempo}" and m.importancia == 1 for m in momentos)
    assert len(resultado.avisos) == 1 and "intervalos regulares" in resultado.avisos[0]
    _comprobar_momentos_basicos(momentos, info_prueba.duracion)


def test_deteccion_de_escenas_privada(ffmpeg, video_prueba):
    """La copia privada (por si ``video.py`` no está) coincide con ``video.detectar_escenas``."""
    escenas = local._detectar_escenas(video_prueba, ffmpeg, 3.0)
    assert escenas == video.detectar_escenas(video_prueba, ffmpeg, 3.0)
    assert sum(1 for t, _ in escenas if _cerca_de_corte(t)) >= 6
    assert local._detectar_escenas(video_prueba, ffmpeg, 100.0) == []
    with pytest.raises(FileNotFoundError):
        local._detectar_escenas(video_prueba.parent / "no.mp4", ffmpeg)
    falso = video_prueba.parent / "falso.mp4"
    falso.write_text("no es un video", encoding="utf-8")
    with pytest.raises(RuntimeError):
        local._detectar_escenas(falso, ffmpeg, 3.0)
