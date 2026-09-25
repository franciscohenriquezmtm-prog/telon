"""Modo local (``--local``) y modo simulado (``--simular``): análisis sin API.

Modo local: los momentos salen de la unión de dos fuentes, sin ningún rango
predefinido (se conservan los candidatos que superan un puntaje mínimo):

- lo que se **ve**: cambios de plano detectados con el filtro ``scdet`` de ffmpeg;
- lo que se **dice**: frases de la transcripción con ``faster-whisper`` (opcional;
  si la librería o el modelo no están disponibles se sigue solo con las escenas).

Modo simulado: momentos de ejemplo marcados "(SIMULADO)" para probar la
extracción de capturas y los documentos sin gastar ni transcribir.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config
from .modelos import InfoVideo, Momento, ResultadoAnalisis, formatear_tiempo

try:
    from .video import detectar_escenas as _detectar_escenas_video
except ImportError:      # video.py no disponible: se usa la copia privada de más abajo
    _detectar_escenas_video = None

# Parámetros de la puntuación (§6 de la especificación).
PUNTAJE_MINIMO = 1.5            # los candidatos por debajo se descartan; no hay mínimo ni máximo de momentos
PUNTOS_LONGITUD_MAX = 0.9       # tope de 0.1·palabras: una frase larga sin palabra clave ni cambio de plano cerca
                                # se queda en 0.5 + 0.9 = 1.4 < PUNTAJE_MINIMO (el contenido decide, no la longitud)
PAUSA_FRASE_SEG = 1.0           # un silencio mayor entre segmentos de Whisper cierra la frase
VENTANA_ESCENA_SEG = 5.0        # una frase con un cambio de plano a menos de esto suma 1 punto
DESFASE_ESCENA_SEG = 0.5        # la captura de un cambio de plano se toma un poco después del corte
ADELANTO_FRASE_MAX_SEG = 3.0    # el momento de una frase es su mitad, como mucho 3 s tras su inicio
PALABRAS_TITULO = 8             # el título de una frase son sus primeras palabras
_INTERVALO_PROGRESO_SEG = 300   # cada cuánto audio transcrito se informa el avance

# Palabras clave (con sus conjugaciones y derivados más habituales).  "no" solo cuenta como prohibición ("no debe",
# "no presione", "no hay que"…): suelto es la palabra más común del español y convertía cualquier frase en momento.
_CLAVES = [
    r"importantes?", r"cuidado", r"nunca", r"siempre", r"debe\w*", r"pasos?", r"primer[oa]?",
    r"no\s+(?:se\s+)?(?:debe\w*|hay\s+que|toque\w*|presione\w*|pulse\w*|apriete\w*|mueva\w*|retire\w*|"
    r"olvide\w*|use\w*|utilice\w*|coloque\w*|active\w*|desactive\w*|conecte\w*|desconecte\w*|abra\w*|cierre\w*|"
    r"gire\w*|deje\w*|permita\w*|exponga\w*|dispare\w*|encienda\w*|apague\w*|suelte\w*|fuerce\w*|intente\w*|"
    r"ponga\w*|quite\w*|levante\w*|baje\w*|suba\w*)",
    r"luego", r"despu[eé]s", r"ajust\w*", r"verific\w*", r"coloc\w*", r"presi[oó]n\w*", r"activ\w*",
    r"revis\w*", r"posici[oó]n\w*", r"aline\w*", r"med(?:ir|idas?|imos|ici[oó]n|iciones|idos?)",
    r"mid(?:a|e|an|en|as|es|iendo|i[oó])", r"selecci[oó]n\w*", r"gir[aeo]\w*",
    r"abr(?:ir|a|e|an|en|as|es|iendo|i[oó]|imos|iera)", r"(?:cerr|cierr)\w*", r"bloque\w*",
]
_PATRON_CLAVE = re.compile(r"\b(?:" + "|".join(_CLAVES) + r")\b", re.IGNORECASE)
# Números con unidad ("10 cm", "30 segundos", "70 kV", "2,5 mGy", "45 grados", "50 %").
_UNIDADES = (r"%|°|º|grados?|cent[ií]metros?|mil[ií]metros?|metros?|pulgadas?|segundos?|minutos?|horas?|"
             r"miliamperios?|amperios?|kilovoltios?|voltios?|veces|cm|mm|km|kvp|kv|mas|ma|kw|w|cgy|mgy|gy|"
             r"msv|sv|khz|mhz|hz|seg|min|kg|mg|ml|cc|fps|px|m|s|h|g|l")
_PATRON_NUMERO_UNIDAD = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:" + _UNIDADES + r")(?![^\W\d_])", re.IGNORECASE)
_PATRON_FIN_FRASE = re.compile(r"(?<=[.!?…])\s+")
_PATRON_SCDET = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),\s*lavfi\.scd\.time:\s*([0-9.]+)")


@dataclass
class _Candidato:
    """Momento en bruto antes de fundir duplicados y convertirlo en ``Momento``."""

    tiempo: float
    titulo: str
    descripcion: str
    puntaje: float
    origenes: set = field(default_factory=set)   # subconjunto de {"visual", "audio"}


# ----------------------------------------------------------------------------
# Escenas
# ----------------------------------------------------------------------------
def _detectar_escenas(ruta_video: Path, ffmpeg: str,
                      umbral: float = config.UMBRAL_ESCENA) -> list[tuple[float, float]]:
    """Copia privada de ``video.detectar_escenas`` (scdet): ``[(tiempo, puntuacion)]`` ordenados."""
    ruta_video = Path(ruta_video)
    if not ruta_video.is_file():
        raise FileNotFoundError(f"No existe el video: {ruta_video}")
    umbral = min(100.0, max(0.0, float(umbral)))
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(ruta_video), "-an", "-sn", "-dn",
                        "-vf", f"scdet=threshold={umbral}", "-f", "null", "-"],
                       capture_output=True, encoding="utf-8", errors="replace")
    escenas = [(float(t), float(s)) for s, t in _PATRON_SCDET.findall(r.stderr)]
    if r.returncode != 0 and not escenas:
        detalle = " | ".join(l.strip() for l in r.stderr.splitlines() if l.strip())[-400:]
        raise RuntimeError(f"ffmpeg no pudo analizar las escenas de {ruta_video.name}: {detalle or '(sin detalle)'}")
    return sorted(escenas)


# ----------------------------------------------------------------------------
# Transcripción (faster-whisper, opcional)
# ----------------------------------------------------------------------------
def _primera_linea(exc: BaseException) -> str:
    lineas = [l.strip() for l in str(exc).splitlines() if l.strip()]
    return (lineas[0] if lineas else type(exc).__name__)[:200]


def _transcribir(ruta_video: Path, modelo: str, idioma: str | None, offline: bool, duracion: float,
                 avisos: list, log: Callable[[str], None]) -> list | None:
    """Transcribe con faster-whisper; ante cualquier fallo añade un aviso y devuelve None (nunca lanza).

    Devuelve ``[{"inicio": s, "fin": s, "texto": str}]`` (segmentos de Whisper, sin unir en frases).
    """
    def avisar(texto: str) -> None:
        avisos.append(texto)
        log("AVISO: " + texto)

    try:
        from faster_whisper import WhisperModel    # import perezoso: la dependencia es opcional
    except ImportError:
        avisar("faster-whisper no está instalado: se omite la transcripción y se usan solo los cambios de plano. "
               "Instálalo con: pip install faster-whisper")
        return None
    except Exception as exc:  # p. ej. librería nativa de ctranslate2 que no carga
        avisar(f"No se pudo cargar faster-whisper ({type(exc).__name__}: {_primera_linea(exc)}): "
               "se usan solo los cambios de plano.")
        return None

    try:
        log(f"Cargando el modelo Whisper '{modelo}' (CPU, int8{', sin red' if offline else ''})...")
        whisper = WhisperModel(str(modelo), device="cpu", compute_type="int8",
                               download_root=os.environ.get("WHISPER_CACHE") or None, local_files_only=offline)
    except Exception as exc:  # httpx.ProxyError, LocalEntryNotFoundError, HFValidationError, RuntimeError...
        avisar(f"No se pudo cargar el modelo Whisper '{modelo}' ({type(exc).__name__}: {_primera_linea(exc)}). "
               "Si no hay internet, descarga el modelo una vez desde una máquina con red: "
               f"python -c \"from faster_whisper import download_model; download_model('{modelo}')\" "
               "(o indica con --whisper-modelo la carpeta de un modelo ya descargado). "
               "Se sigue solo con los cambios de plano.")
        return None

    try:
        log("Transcribiendo el audio con faster-whisper (puede tardar varios minutos)...")
        segmentos, datos = whisper.transcribe(str(ruta_video), language=idioma, vad_filter=True)
        transcripcion = []
        proximo_aviso = _INTERVALO_PROGRESO_SEG
        for s in segmentos:            # generador perezoso: la decodificación ocurre aquí
            transcripcion.append({"inicio": round(float(s.start), 2), "fin": round(float(s.end), 2),
                                  "texto": str(s.text).strip()})
            if float(s.end) >= proximo_aviso:
                log(f"  transcrito hasta {formatear_tiempo(s.end)} de {formatear_tiempo(duracion)}")
                proximo_aviso += _INTERVALO_PROGRESO_SEG
    except Exception as exc:
        avisar(f"La transcripción falló ({type(exc).__name__}: {_primera_linea(exc)}): "
               "se usan solo los cambios de plano.")
        return None

    idioma_detectado = getattr(datos, "language", None)
    probabilidad = getattr(datos, "language_probability", None)
    detalle = f", idioma {idioma_detectado}" if idioma_detectado else ""
    if isinstance(probabilidad, (int, float)):
        detalle += f" (p={probabilidad:.2f})"
    log(f"Transcripción: {len(transcripcion)} segmentos{detalle}")
    return transcripcion


def segmentar_frases(segmentos: list, pausa_max: float = PAUSA_FRASE_SEG) -> list:
    """Reparte los segmentos de Whisper en frases.

    Una frase termina en ``. ? ! …`` o cuando entre dos segmentos hay una pausa mayor que
    ``pausa_max``.  Los tiempos dentro de un segmento se interpolan por longitud de texto.
    Devuelve ``[{"inicio": s, "fin": s, "texto": str}]`` sin frases vacías.
    """
    frases: list = []
    actual: dict | None = None
    for seg in segmentos:
        texto = " ".join(str(seg.get("texto", "")).split())
        if not any(c.isalnum() for c in texto):
            continue
        inicio, fin = float(seg["inicio"]), float(seg["fin"])
        if actual is not None and inicio - actual["fin"] > pausa_max:
            frases.append(actual)
            actual = None
        largo, avance = max(1, len(texto)), 0
        for parte in (p for p in _PATRON_FIN_FRASE.split(texto) if p):
            t_ini = inicio + (fin - inicio) * avance / largo
            avance += len(parte) + 1
            t_fin = inicio + (fin - inicio) * min(largo, avance) / largo
            if actual is None:
                actual = {"inicio": t_ini, "fin": t_fin, "texto": parte}
            else:
                actual["texto"] += " " + parte
                actual["fin"] = t_fin
            if parte[-1] in ".!?…":
                frases.append(actual)
                actual = None
    if actual is not None:
        frases.append(actual)
    for f in frases:
        f["inicio"], f["fin"] = round(f["inicio"], 2), round(f["fin"], 2)
    return frases


# ----------------------------------------------------------------------------
# Puntuación y construcción de momentos
# ----------------------------------------------------------------------------
def puntuar_escena(score: float) -> float:
    """Puntaje de un cambio de plano: ``score/10`` acotado a 0-3, más 1."""
    return min(3.0, max(0.0, float(score) / 10.0)) + 1.0


def puntuar_frase(texto: str, cerca_de_escena: bool = False) -> float:
    """Puntaje de una frase: 0.5 + 0.1·palabras (máx. ``PUNTOS_LONGITUD_MAX``) + 1 por palabra clave o número con
    unidad + 1 si hay un cambio de plano cerca.  Sin clave ni cambio de plano nunca llega a ``PUNTAJE_MINIMO``."""
    palabras = len(texto.split())
    claves = len(_PATRON_CLAVE.findall(texto)) + len(_PATRON_NUMERO_UNIDAD.findall(texto))
    return 0.5 + min(PUNTOS_LONGITUD_MAX, 0.1 * palabras) + claves + (1.0 if cerca_de_escena else 0.0)


def _importancia(puntaje: float) -> int:
    """Importancia 1-5 a partir del puntaje (redondeo a la mitad hacia arriba)."""
    return int(min(5.0, max(1.0, puntaje)) + 0.5)


def _acotar(t: float, duracion: float) -> float:
    return round(min(max(0.0, t), max(0.0, duracion - 0.5)), 3)


def _recortar(texto: str, maximo: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= maximo else texto[:maximo - 1].rstrip() + "…"


def _titulo_frase(texto: str) -> str:
    """Primeras palabras de la frase, con mayúscula inicial y sin puntuación final."""
    palabras = texto.split()
    titulo = " ".join(palabras[:PALABRAS_TITULO]).rstrip(".,;:")
    if len(palabras) > PALABRAS_TITULO:
        titulo += "…"
    return _recortar(titulo[:1].upper() + titulo[1:], config.MAX_TITULO)


def _distancia(t: float, inicio: float, fin: float) -> float:
    """Distancia de un instante al intervalo ``[inicio, fin]`` (0 si cae dentro)."""
    return 0.0 if inicio <= t <= fin else min(abs(t - inicio), abs(t - fin))


TITULO_PLANO = "Cambio de plano"


def _candidatos_escenas(escenas: list, duracion: float) -> list:
    candidatos = []
    for t, score in escenas:
        tiempo = _acotar(t + DESFASE_ESCENA_SEG, duracion)
        candidatos.append(_Candidato(tiempo, TITULO_PLANO, f"{TITULO_PLANO} a los {formatear_tiempo(tiempo)}.",
                                     puntuar_escena(score), {"visual"}))
    return candidatos


def _numerar_planos(momentos: list) -> None:
    """Títulos "Cambio de plano k" consecutivos (1..n) sobre los momentos definitivos, sin huecos por los
    candidatos descartados o fundidos."""
    k = 0
    for m in momentos:
        if m.titulo == TITULO_PLANO or m.titulo.startswith(TITULO_PLANO + " "):
            k += 1
            m.titulo = f"{TITULO_PLANO} {k}"


def _candidatos_frases(frases: list, tiempos_escenas: list, duracion: float) -> list:
    candidatos = []
    for f in frases:
        inicio, fin, texto = f["inicio"], f["fin"], f["texto"]
        cerca = any(_distancia(t, inicio, fin) < VENTANA_ESCENA_SEG for t in tiempos_escenas)
        tiempo = _acotar(inicio + min(ADELANTO_FRASE_MAX_SEG, (fin - inicio) / 2), duracion)
        candidatos.append(_Candidato(tiempo, _titulo_frase(texto), _recortar(texto, config.MAX_DESCRIPCION),
                                     puntuar_frase(texto, cerca), {"audio"}))
    return candidatos


def _fundir(candidatos: list, separacion_min: float) -> list:
    """Funde candidatos a menos de ``separacion_min``: gana el de mayor puntaje y hereda los orígenes."""
    aceptados: list = []
    for c in sorted(candidatos, key=lambda c: (-c.puntaje, c.tiempo)):
        vecino = next((a for a in aceptados if abs(a.tiempo - c.tiempo) < separacion_min), None)
        if vecino is None:
            aceptados.append(c)
        else:
            vecino.origenes |= c.origenes
    return sorted(aceptados, key=lambda c: c.tiempo)


def _a_momento(c: _Candidato) -> Momento:
    fuente = "ambos" if len(c.origenes) > 1 else next(iter(c.origenes))
    return Momento(tiempo_seg=c.tiempo, titulo=c.titulo, descripcion=c.descripcion,
                   importancia=_importancia(c.puntaje), fuente=fuente, puntaje=round(c.puntaje, 3))


def _filtrar(momentos: list, importancia_minima: int, max_momentos: int | None, avisos: list) -> list:
    """Filtros opcionales del usuario: importancia mínima y número máximo (se quedan los de mayor puntaje)."""
    minimo = min(5, max(1, int(importancia_minima)))
    filtrados = [m for m in momentos if m.importancia >= minimo]
    if len(filtrados) < len(momentos):
        avisos.append(f"{len(momentos) - len(filtrados)} momentos descartados por importancia menor que {minimo}.")
    if max_momentos is not None and max_momentos > 0 and len(filtrados) > max_momentos:
        mejores = sorted(filtrados, key=lambda m: (-(m.puntaje or 0.0), m.tiempo_seg))[:max_momentos]
        avisos.append(f"Se conservan los {max_momentos} momentos más relevantes de {len(filtrados)}.")
        filtrados = sorted(mejores, key=lambda m: m.tiempo_seg)
    return filtrados


def _rejilla(duracion: float) -> list:
    """Capturas a intervalos regulares cuando no hay ningún candidato."""
    paso = max(30.0, duracion / 12)
    tiempos = [_acotar(paso * (k + 0.5), duracion) for k in range(max(1, int(duracion // paso)))]
    return [Momento(tiempo_seg=t, titulo=f"Vista a los {formatear_tiempo(t)}",
                    descripcion="Sin cambios de plano ni frases relevantes detectados: captura a intervalos regulares.",
                    importancia=1, fuente="visual", puntaje=0.0) for t in tiempos]


def _asignar_secciones(momentos: list, duracion: float) -> None:
    """Secciones "Parte k" por tramos de ~duración/5 (mínimo 1), numeradas de forma consecutiva."""
    n_partes = max(1, min(5, len(momentos)))
    largo = max(duracion, 1e-6) / n_partes
    indices = [min(n_partes - 1, int(m.tiempo_seg // largo)) for m in momentos]
    etiquetas = {idx: f"Parte {k}" for k, idx in enumerate(sorted(set(indices)), 1)}
    for m, idx in zip(momentos, indices):
        m.seccion = etiquetas[idx]


def _resumen_local(n_escenas: int, n_frases: int, modelo_whisper: str | None) -> str:
    partes = [f"{n_escenas} cambios de plano detectados con ffmpeg"]
    if modelo_whisper is not None:
        partes.append(f"{n_frases} frases transcritas con faster-whisper ({modelo_whisper})")
    return (f"Análisis local sin IA generativa: {' y '.join(partes)}. Los títulos y descripciones son "
            "automáticos (frases literales de la transcripción o cambios de plano): revise y edite el documento.")


# ----------------------------------------------------------------------------
# API pública
# ----------------------------------------------------------------------------
def analizar_local(ruta_video: Path, info: InfoVideo, ffmpeg: str, *,
                   whisper_modelo: str | None = config.WHISPER_MODELO, idioma: str = config.WHISPER_IDIOMA,
                   offline: bool = False, umbral_escena: float = config.UMBRAL_ESCENA,
                   separacion_min: float = config.SEPARACION_MINIMA_LOCAL_SEG, max_momentos: int | None = None,
                   importancia_minima: int = 1, log: Callable[[str], None] = print) -> ResultadoAnalisis:
    """Análisis sin API: cambios de plano (scdet) ∪ frases de la transcripción (faster-whisper).

    ``whisper_modelo=None`` desactiva la transcripción.  Si faster-whisper falla por cualquier
    motivo se añade un aviso y se sigue solo con las escenas.  Los errores de ffmpeg se propagan.
    """
    ruta_video = Path(ruta_video)
    duracion = float(info.duracion)
    avisos: list = []

    log(f"Detectando cambios de plano (scdet, umbral {umbral_escena:g})...")
    escenas = (_detectar_escenas_video or _detectar_escenas)(ruta_video, ffmpeg, umbral_escena)
    log(f"{len(escenas)} cambios de plano detectados")

    transcripcion = None
    if whisper_modelo is None:
        log("Transcripción desactivada: se usan solo los cambios de plano.")
    else:
        transcripcion = _transcribir(ruta_video, whisper_modelo, idioma, offline, duracion, avisos, log)
    frases = segmentar_frases(transcripcion) if transcripcion else []
    if transcripcion is not None:
        log(f"{len(frases)} frases en la transcripción")

    tiempos_escenas = [t for t, _ in escenas]
    candidatos = _candidatos_escenas(escenas, duracion) + _candidatos_frases(frases, tiempos_escenas, duracion)
    relevantes = [c for c in candidatos if c.puntaje >= PUNTAJE_MINIMO]
    momentos = _filtrar([_a_momento(c) for c in _fundir(relevantes, separacion_min)],
                        importancia_minima, max_momentos, avisos)
    if not momentos:
        momentos = _rejilla(duracion)
        aviso = ("No se encontró ningún momento relevante (ni cambios de plano ni frases con palabras clave): "
                 f"se toman {len(momentos)} capturas a intervalos regulares.")
        avisos.append(aviso)
        log("AVISO: " + aviso)
    _numerar_planos(momentos)
    _asignar_secciones(momentos, duracion)

    modelo_whisper = str(whisper_modelo) if transcripcion is not None else None
    log(f"{len(momentos)} momentos ({len(relevantes)} candidatos relevantes de {len(candidatos)})")
    return ResultadoAnalisis(
        momentos=momentos, modo="local",
        modelo=f"scdet+faster-whisper:{modelo_whisper}" if modelo_whisper else "scdet",
        resumen=_resumen_local(len(escenas), len(frases), modelo_whisper),
        uso=None, avisos=avisos, transcripcion=transcripcion)


def analizar_simulado(info: InfoVideo, *, log: Callable[[str], None] = print) -> ResultadoAnalisis:
    """Momentos de ejemplo (sin API ni Whisper): uno cada ``max(10 s, duración/8)``, textos "(SIMULADO)"."""
    duracion = float(info.duracion)
    paso = max(10.0, duracion / 8)
    n = max(1, int(duracion // paso))
    secciones = ("Preparación", "Ejecución", "Cierre")
    fuentes = ("ambos", "visual", "audio")
    momentos = []
    for k in range(n):
        t = _acotar(paso * (k + 0.5), duracion)
        zona = None if k % 2 == 0 else {"x": round(0.25 + 0.15 * ((k // 2) % 4), 2), "y": round(0.35 + 0.1 * (k % 3), 2)}
        momentos.append(Momento(
            tiempo_seg=t, titulo=f"(SIMULADO) Paso {k + 1} a los {formatear_tiempo(t)}",
            descripcion=(f"(SIMULADO) Texto de ejemplo del paso {k + 1}: aquí iría qué se hace y por qué, "
                         "con los valores exactos que se mencionan en el video."),
            importancia=k % 5 + 1, fuente=fuentes[k % 3], seccion=secciones[min(2, k * 3 // n)], zona=zona))
    log(f"Modo simulado: {n} momentos de ejemplo (sin API ni transcripción)")
    return ResultadoAnalisis(
        momentos=momentos, modo="simulado", modelo="simulado",
        resumen="(SIMULADO) Resumen de ejemplo: este documento se generó sin analizar el contenido del video, "
                "solo para comprobar capturas y maquetación.",
        uso=None, titulo=f"(SIMULADO) Manual de {info.nombre}",
        avisos=["Modo simulado: los textos son de ejemplo y no describen el video."])
