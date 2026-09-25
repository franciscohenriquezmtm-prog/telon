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
    # primero + debe + verificar + "10 cm" = 4 palabras clave; 10 palabras (longitud acotada a 0.9)
    assert local.puntuar_frase(FRASES_CLAVE[0]) == pytest.approx(5.4)
    # nunca + presione (conjugación de presionar) = 2; 7 palabras
    assert local.puntuar_frase(FRASES_CLAVE[1]) == pytest.approx(3.2)
    # ajuste + "70 kV" + "2,5 mAs" = 3; 9 palabras
    assert local.puntuar_frase("ajuste el kilovoltaje a 70 kV y 2,5 mAs") == pytest.approx(0.5 + 0.9 + 3)
    # P-H7: la longitud sola nunca llega a PUNTAJE_MINIMO (0.1·palabras acotado a 0.9)
    assert local.puntuar_frase(" ".join(["palabra"] * 40)) == pytest.approx(1.4)
    assert local.puntuar_frase(" ".join(["palabra"] * 40)) < local.PUNTAJE_MINIMO
    assert local.puntuar_frase(" ".join(["palabra"] * 40), cerca_de_escena=True) >= local.PUNTAJE_MINIMO
    # "no" suelto ya no es palabra clave; como prohibición sí
    assert local.puntuar_frase("esto no cambia nada y no es lo mismo") == pytest.approx(0.5 + 0.9)   # 9 palabras, sin clave
    assert local.puntuar_frase("no presione este botón") == pytest.approx(0.5 + 0.4 + 1)
    assert local.puntuar_frase("no debe tocar el pedal") == pytest.approx(0.5 + 0.5 + 1)
    assert local.puntuar_frase("no hay que forzar el brazo") == pytest.approx(0.5 + 0.6 + 1)
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


# ----------------------------------------------------------------------------
# Modo local sin ffmpeg: escenas inyectadas (P-H7, P-H8)
# ----------------------------------------------------------------------------
FRASES_NEUTRAS = [
    "Entonces lo que hacemos acá es simplemente mirar la pantalla y ver qué nos muestra el equipo en este momento.",
    "Como les decía, esto es lo mismo que vimos antes, no cambia nada, solo se ve desde otro lado.",
    "Bueno, ahora vamos a seguir con la explicación de esta parte que ya conocen de la clase anterior.",
    "Esto lo menciono de pasada para que lo tengan presente cuando lo vean en la práctica.",
    "Acá la persona que graba se acerca un poco más para que se vea mejor lo que estoy mostrando.",
    "Y básicamente eso es todo lo que hay en esta parte, no tiene mucho más misterio.",
]
FRASES_CON_CONTENIDO = {
    300.0: "Primero debe verificar que el colimador esté a 10 cm.",
    600.0: "Nunca presione este botón durante la exposición.",
    900.0: "No debe apagar el equipo con el pedal pisado.",
}


def _segmentos_charla_neutra(duracion: float) -> list:
    """20 min de charla continua (una frase cada ~6 s) con solo tres frases con contenido."""
    segmentos, t, k = [], 0.0, 0
    while t < duracion:
        texto = FRASES_NEUTRAS[k % len(FRASES_NEUTRAS)]
        for inicio, frase in FRASES_CON_CONTENIDO.items():
            if inicio <= t < inicio + 6.5:
                texto = frase
        segmentos.append((t, t + 5.5, " " + texto))
        t += 6.0
        k += 1
    return segmentos


def test_charla_neutra_sin_cortes_produce_pocos_momentos(monkeypatch, registro):
    """P-H7: en una toma continua, una frase sin palabra clave ni cambio de plano no es un momento."""
    monkeypatch.setitem(sys.modules, "faster_whisper", _modulo_whisper_falso(_segmentos_charla_neutra(1200.0)))
    monkeypatch.setattr(local, "_detectar_escenas_video", lambda ruta, ffmpeg, umbral: [])
    info = _info_falsa(1200.0, "charla")
    resultado = local.analizar_local(Path("/no/existe.mp4"), info, "ffmpeg", whisper_modelo="base", log=registro)
    momentos = resultado.momentos
    assert len(resultado.transcripcion) == 200
    assert len(momentos) == len(FRASES_CON_CONTENIDO), [m.descripcion for m in momentos]
    for inicio, frase in FRASES_CON_CONTENIDO.items():
        encontrados = [m for m in momentos if m.descripcion == frase]
        assert len(encontrados) == 1 and abs(encontrados[0].tiempo_seg - inicio) < 6
    assert all(m.fuente == "audio" and m.puntaje >= local.PUNTAJE_MINIMO for m in momentos)
    assert not any("intervalos regulares" in a for a in resultado.avisos)
    _comprobar_momentos_basicos(momentos, 1200.0)


def test_charla_neutra_sin_nada_relevante_usa_rejilla(monkeypatch, registro):
    segmentos = [(t, t + 5.5, " " + FRASES_NEUTRAS[k % len(FRASES_NEUTRAS)]) for k, t in enumerate(range(0, 600, 6))]
    monkeypatch.setitem(sys.modules, "faster_whisper", _modulo_whisper_falso(segmentos))
    monkeypatch.setattr(local, "_detectar_escenas_video", lambda ruta, ffmpeg, umbral: [])
    resultado = local.analizar_local(Path("/no/existe.mp4"), _info_falsa(600.0), "ffmpeg", whisper_modelo="base", log=registro)
    assert [m.tiempo_seg for m in resultado.momentos] == [25.0, 75.0, 125.0, 175.0, 225.0, 275.0, 325.0, 375.0, 425.0,
                                                          475.0, 525.0, 575.0]     # cada max(30 s, 600/12) = 50 s
    assert all(m.titulo.startswith("Vista a los") for m in resultado.momentos)
    assert any("intervalos regulares" in a for a in resultado.avisos)


def test_cambios_de_plano_numerados_sin_huecos(monkeypatch, registro):
    """P-H8: los títulos "Cambio de plano k" van 1..n sobre los momentos definitivos."""
    escenas = [(10.0, 30.0), (20.0, 4.0), (30.0, 4.5), (40.0, 12.0), (50.0, 3.5), (60.0, 25.0)]
    monkeypatch.setattr(local, "_detectar_escenas_video", lambda ruta, ffmpeg, umbral: escenas)
    resultado = local.analizar_local(Path("/no/existe.mp4"), _info_falsa(90.0), "ffmpeg", whisper_modelo=None, log=registro)
    assert [m.titulo for m in resultado.momentos] == ["Cambio de plano 1", "Cambio de plano 2", "Cambio de plano 3"]
    assert [m.tiempo_seg for m in resultado.momentos] == [10.5, 40.5, 60.5]
    assert all(m.descripcion == f"Cambio de plano a los {m.tiempo}." for m in resultado.momentos)
    # con frases: un plano fundido con una frase conserva su numeración consecutiva
    segmentos = [(9.0, 12.0, " Primero debe verificar que el colimador esté a 10 cm.")]
    monkeypatch.setitem(sys.modules, "faster_whisper", _modulo_whisper_falso(segmentos))
    resultado = local.analizar_local(Path("/no/existe.mp4"), _info_falsa(90.0), "ffmpeg", whisper_modelo="base", log=registro)
    titulos = [m.titulo for m in resultado.momentos]
    planos = [t for t in titulos if t.startswith("Cambio de plano")]
    assert planos == [f"Cambio de plano {k}" for k in range(1, len(planos) + 1)]
    assert resultado.momentos[0].fuente == "ambos"
