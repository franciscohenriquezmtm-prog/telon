"""Utilidades de video basadas en ffmpeg.

Localización de binarios, información del archivo (ffprobe → stderr de ffmpeg →
PyAV), extracción de fotogramas nítidos, detección de escenas, audio para Whisper
y copia ligera para subir a Gemini.  Todo funciona sin ffprobe: ffprobe solo
aporta metadatos más completos (códec, HDR, rotación) a ``InfoVideo.extra``.

Convenciones: ``subprocess`` con lista de argumentos (nunca ``shell=True``),
salida de los procesos leída como UTF-8 con ``errors="replace"``; rutas con
``pathlib``.  Compatible con Windows, macOS y Linux.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from . import config
from .modelos import InfoVideo, formatear_tiempo

# Cadena de filtros que convierte HDR (PQ o HLG) a SDR bt709.  Va SIEMPRE al final de la cadena, después
# de ``fps`` y ``scale``: así se aplica solo a los fotogramas que se conservan y ya reducidos (en un 4K60
# HDR es ~10 veces más rápido que aplicarla antes, con la misma salida).
FILTRO_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                  "tonemap=hable,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
TRANSFERENCIAS_HDR = {"smpte2084", "arib-std-b67"}
# ``--sin-tonemap`` lo pone en False: los videos HDR se procesan tal cual (capturas y copia ligera).
TONEMAP_HDR = True
_ROTACIONES_VERTICALES = {90.0, 270.0}

# PyAV devuelve ``color_trc`` como entero (AVColorTransferCharacteristic); 2 = sin especificar.
_NOMBRES_TRANSFERENCIA = {1: "bt709", 4: "bt470m", 5: "bt470bg", 6: "smpte170m", 7: "smpte240m",
                          8: "linear", 13: "iec61966-2-1", 14: "bt2020-10", 15: "bt2020-12",
                          16: "smpte2084", 18: "arib-std-b67"}
_NOMBRES_RESERVADOS_WINDOWS = {"con", "prn", "aux", "nul",
                               *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_CARACTERES_NO_PERMITIDOS = re.compile(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ _-]+")
_PATRON_SCDET = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+),\s*lavfi\.scd\.time:\s*([0-9.]+)")
_PATRON_PROGRESO = re.compile(r"^out_time_(?:us|ms)=(\d+)")
# En el stderr de ffmpeg, dentro del paréntesis del pix_fmt, estos valores no son colorimetría.
_NO_COLORIMETRIA = ("tv", "pc", "progressive", "first", "coded", "unknown")

_avisos_hdr: set[str] = set()   # videos HDR de los que ya se registró qué se hace con ellos (una línea por video)
_ffmpeg_resuelto: str | None = None   # último ffmpeg localizado (para buscar ffprobe a su lado)


# ----------------------------------------------------------------------------
# Procesos
# ----------------------------------------------------------------------------
def _ejecutar(cmd: list) -> subprocess.CompletedProcess:
    """Ejecuta un comando sin shell y captura su salida como texto UTF-8."""
    return subprocess.run([str(c) for c in cmd], capture_output=True, encoding="utf-8", errors="replace")


def _cola(texto: str, lineas: int = 6) -> str:
    """Últimas líneas no vacías de una salida (para mensajes de error)."""
    utiles = [l.strip() for l in (texto or "").splitlines() if l.strip()]
    return " | ".join(utiles[-lineas:]) if utiles else "(sin detalle)"


def _es_ejecutable(ruta: str | None) -> str | None:
    """Devuelve la ruta si apunta a un archivo existente o a un nombre del PATH; si no, None."""
    if not ruta:
        return None
    candidato = Path(ruta).expanduser()
    if candidato.is_file():
        return str(candidato)
    return shutil.which(str(ruta))


# ----------------------------------------------------------------------------
# Binarios
# ----------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _ffmpeg_imageio() -> str | None:
    """Ruta del ffmpeg estático que trae ``imageio-ffmpeg`` (import perezoso), o None si no está."""
    try:
        import imageio_ffmpeg  # paquete opcional; get_ffmpeg_exe() ejecuta `ffmpeg -version`: se cachea

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - ImportError o binario no disponible
        return None


def localizar_ffmpeg(ruta: str | None = None) -> str:
    """Ruta a ffmpeg: argumento -> FFMPEG_BIN -> imageio-ffmpeg (incluido) -> PATH.  RuntimeError si no hay ninguno.

    El ffmpeg incluido con ``imageio-ffmpeg`` va antes que el del PATH: un ffmpeg viejo de otro programa
    (p. ej. el de Anaconda) puede no tener ``libx264``, ``scdet`` o ``zscale``.  Quien quiera usar otro lo
    indica con ``--ffmpeg`` o ``FFMPEG_BIN``.
    """
    global _ffmpeg_resuelto
    if ruta:
        encontrado = _es_ejecutable(ruta)
        if not encontrado:
            raise RuntimeError(f"No existe el ffmpeg indicado: {ruta}")
        _ffmpeg_resuelto = encontrado
        return encontrado
    for candidato in (os.environ.get("FFMPEG_BIN"), _ffmpeg_imageio(), "ffmpeg"):
        encontrado = _es_ejecutable(candidato)
        if encontrado:
            _ffmpeg_resuelto = encontrado
            return encontrado
    raise RuntimeError(
        "No se encontró ffmpeg. Opciones: ejecute 'pip install imageio-ffmpeg' (trae un ffmpeg incluido), "
        "instálelo y póngalo en el PATH, o indique su ruta con --ffmpeg o con la variable de entorno FFMPEG_BIN.")


def localizar_ffprobe(ruta: str | None = None, ffmpeg: str | None = None) -> str | None:
    """Ruta a ffprobe (argumento -> FFPROBE_BIN -> junto a ``ffmpeg`` -> PATH) o None: no es obligatorio.

    ``ffmpeg`` es el ffmpeg ya elegido (el de ``--ffmpeg``, por ejemplo): ffprobe suele estar a su lado.
    Si no se pasa, se usa el último que devolvió ``localizar_ffmpeg`` (o se localiza uno).
    """
    candidatos = [ruta, os.environ.get("FFPROBE_BIN")]
    if not ffmpeg:
        try:
            ffmpeg = _ffmpeg_resuelto or localizar_ffmpeg()
        except RuntimeError:
            ffmpeg = None
    if ffmpeg:
        ruta_ffmpeg = Path(ffmpeg)
        sufijo = ruta_ffmpeg.suffix if ruta_ffmpeg.suffix.lower() == ".exe" else ""
        candidatos.append(str(ruta_ffmpeg.with_name("ffprobe" + sufijo)))
        if "ffmpeg" in ruta_ffmpeg.name:   # p. ej. ffmpeg-win64-v7.0.exe -> ffprobe-win64-v7.0.exe
            candidatos.append(str(ruta_ffmpeg.with_name(ruta_ffmpeg.name.replace("ffmpeg", "ffprobe", 1))))
    candidatos.append("ffprobe")
    for candidato in candidatos:
        encontrado = _es_ejecutable(candidato)
        if encontrado:
            return encontrado
    return None


@functools.lru_cache(maxsize=8)
def version_ffmpeg(ffmpeg: str) -> str:
    """Versión que declara este ffmpeg (``ffmpeg -version``, p. ej. ``7.0.2-static``); ``"?"`` si no responde."""
    try:
        primera = (_ejecutar([ffmpeg, "-version"]).stdout or "").splitlines()[:1]
    except (OSError, IndexError):
        return "?"
    m = re.match(r"ffmpeg version\s+(\S+)", primera[0]) if primera else None
    return m[1] if m else "?"


@functools.lru_cache(maxsize=8)
def _filtros_disponibles(ffmpeg: str) -> frozenset:
    """Nombres de los filtros que ofrece este ffmpeg (``ffmpeg -hide_banner -filters``), cacheado."""
    try:
        salida = _ejecutar([ffmpeg, "-hide_banner", "-filters"]).stdout
    except OSError:
        return frozenset()
    return frozenset(re.findall(r"^\s*[TSC.]{3}\s+(\w+)\s+\S+->\S+", salida, flags=re.M))


def _tonemap_disponible(ffmpeg: str) -> bool:
    """True si este ffmpeg tiene los filtros zscale y tonemap (necesarios para HDR → SDR)."""
    return {"zscale", "tonemap"} <= set(_filtros_disponibles(ffmpeg))


# ----------------------------------------------------------------------------
# Nombres y listado
# ----------------------------------------------------------------------------
def sanear_nombre(nombre: str) -> str:
    """Nombre seguro para carpeta/archivo en Windows.

    Conserva letras, dígitos, áéíóúüñ, espacio, ``-`` y ``_``; colapsa espacios; quita
    puntos y espacios finales; máximo 80 caracteres; vacío → ``"video"``.
    """
    limpio = _CARACTERES_NO_PERMITIDOS.sub(" ", str(nombre or ""))
    limpio = re.sub(r"\s+", " ", limpio).strip()
    limpio = limpio[:80].rstrip(" .")
    if not limpio:
        return "video"
    if limpio.lower() in _NOMBRES_RESERVADOS_WINDOWS:   # CON, NUL, COM1… no pueden ser carpetas en Windows
        limpio += "_video"
    return limpio


def es_video(ruta: Path) -> bool:
    """True si la extensión está en ``config.EXTENSIONES_VIDEO``."""
    return Path(ruta).suffix.lower() in config.EXTENSIONES_VIDEO


def listar_videos(carpeta: Path) -> list[Path]:
    """Videos de la carpeta (sin recorrer subcarpetas ni archivos ocultos), ordenados por nombre."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de videos: {carpeta}")
    videos = [p for p in carpeta.iterdir() if p.is_file() and not p.name.startswith(".") and es_video(p)]
    return sorted(videos, key=lambda p: (p.name.lower(), p.name))


# ----------------------------------------------------------------------------
# Información del archivo
# ----------------------------------------------------------------------------
def _a_float(valor) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _fraccion(valor) -> float | None:
    """``"30000/1001"`` → 29.97; None si no es una fracción válida o el denominador es 0."""
    if not valor or "/" not in str(valor):
        return _a_float(valor)
    numerador, denominador = str(valor).split("/", 1)
    n, d = _a_float(numerador), _a_float(denominador)
    return n / d if n and d else None


def _info_ffprobe(ruta: Path, ffprobe: str) -> dict:
    """Información con ffprobe (JSON): duración, fps, tamaño de imagen, códec, colorimetría y rotación."""
    entradas = ("stream=codec_name,pix_fmt,color_transfer,color_primaries,color_space,width,height,"
                "avg_frame_rate,r_frame_rate,duration,nb_frames:stream_tags=rotate:"
                "stream_side_data=rotation:format=duration")
    r = _ejecutar([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", entradas,
                   "-of", "json", ruta])
    if r.returncode != 0:
        raise RuntimeError(_cola(r.stderr) if r.stderr.strip() else f"ffprobe salió con código {r.returncode}")
    datos = json.loads(r.stdout or "{}")
    pistas = datos.get("streams") or []
    if not pistas:
        raise RuntimeError("el archivo no tiene pista de video")
    pista = pistas[0]
    fps = _fraccion(pista.get("avg_frame_rate")) or _fraccion(pista.get("r_frame_rate"))
    duracion = _a_float((datos.get("format") or {}).get("duration")) or _a_float(pista.get("duration"))
    if not duracion and fps and _a_float(pista.get("nb_frames")):
        duracion = float(pista["nb_frames"]) / fps
    rotacion = None
    for lateral in pista.get("side_data_list") or []:
        if lateral.get("rotation") is not None:
            rotacion = _a_float(lateral["rotation"])
    if rotacion is None:
        rotacion = _a_float((pista.get("tags") or {}).get("rotate"))
    extra = {"codec": pista.get("codec_name"), "pix_fmt": pista.get("pix_fmt"),
             "color_transfer": pista.get("color_transfer"), "color_primaries": pista.get("color_primaries"),
             "color_space": pista.get("color_space"), "rotacion": rotacion}
    return {"duracion": duracion, "fps": fps, "ancho": pista.get("width"), "alto": pista.get("height"),
            "extra": extra}


def _colorimetria(parentesis: str) -> dict:
    """Interpreta ``tv, bt2020nc/bt2020/smpte2084, progressive`` del stderr de ffmpeg."""
    for parte in (p.strip() for p in parentesis.split(",")):
        if not parte or any(marca in parte for marca in _NO_COLORIMETRIA):
            continue
        partes = parte.split("/")
        if len(partes) == 3:
            return {"color_space": partes[0], "color_primaries": partes[1], "color_transfer": partes[2]}
        if len(partes) == 1:   # ffmpeg imprime un solo nombre cuando espacio, primarios y transferencia coinciden
            return {"color_space": parte, "color_primaries": parte, "color_transfer": parte}
    return {}


def _info_ffmpeg_stderr(ruta: Path, ffmpeg: str) -> dict:
    """Información parseando el stderr de ``ffmpeg -i`` (sale con código 1: es lo normal sin salida)."""
    err = _ejecutar([ffmpeg, "-hide_banner", "-i", ruta]).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duracion = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    extra: dict = {}
    ancho = alto = fps = None
    linea = re.search(r"Stream #\d+:\d+.*?Video:\s*(.*)", err)
    if linea:
        detalle = linea[1]
        m = re.match(r"(\w+)", detalle)
        extra["codec"] = m[1] if m else None
        m = re.search(r",\s*([a-z0-9]+)(?:\(([^)]*)\))?,\s*(\d{2,5})x(\d{2,5})", detalle)
        if m:
            extra["pix_fmt"] = m[1]
            extra.update(_colorimetria(m[2] or ""))
            ancho, alto = int(m[3]), int(m[4])
        m = re.search(r"(\d+(?:\.\d+)?)\s*fps", detalle) or re.search(r"(\d+(?:\.\d+)?)\s*tbr", detalle)
        fps = float(m[1]) if m else None
    m = re.search(r"rotation of\s*(-?\d+(?:\.\d+)?)\s*degrees", err)
    extra["rotacion"] = float(m[1]) if m else None
    return {"duracion": duracion, "fps": fps, "ancho": ancho, "alto": alto, "extra": extra}


def _info_pyav(ruta: Path) -> dict:
    """Información con PyAV (import perezoso): último recurso si no hay ffprobe ni se pudo leer ffmpeg."""
    import av

    with av.open(str(ruta)) as contenedor:
        if not contenedor.streams.video:
            raise RuntimeError("el archivo no tiene pista de video")
        pista = contenedor.streams.video[0]
        codec = pista.codec_context
        if contenedor.duration is not None:
            duracion = contenedor.duration / av.time_base
        elif pista.duration is not None and pista.time_base is not None:
            duracion = float(pista.duration * pista.time_base)
        else:
            duracion = None
        tasa = pista.average_rate or pista.guessed_rate
        transferencia = getattr(codec, "color_trc", None)
        extra = {"codec": getattr(codec, "name", None), "pix_fmt": getattr(codec, "pix_fmt", None),
                 "color_transfer": _NOMBRES_TRANSFERENCIA.get(transferencia),
                 "rotacion": _a_float((pista.metadata or {}).get("rotate"))}
        return {"duracion": duracion, "fps": float(tasa) if tasa else None,
                "ancho": codec.width, "alto": codec.height, "extra": extra}


def obtener_info(ruta: Path, ffmpeg: str, ffprobe: str | None = None) -> InfoVideo:
    """Duración, fps, tamaño y metadatos del video.

    Orden de lectura: ffprobe (si se indica) → stderr de ``ffmpeg -i`` → PyAV.  ``extra`` guarda
    códec, pix_fmt, colorimetría y rotación cuando se conocen, y ``origen_info`` con el lector usado.
    RuntimeError si ningún lector obtiene la duración; FileNotFoundError si el archivo no existe.
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el video: {ruta}")
    ruta = ruta.resolve()
    lectores: list[tuple[str, Callable[[], dict]]] = []
    if ffprobe:
        lectores.append(("ffprobe", lambda: _info_ffprobe(ruta, ffprobe)))
    lectores.append(("ffmpeg", lambda: _info_ffmpeg_stderr(ruta, ffmpeg)))
    lectores.append(("pyav", lambda: _info_pyav(ruta)))
    errores = []
    for origen, lector in lectores:
        try:
            datos = lector()
        except Exception as exc:  # cada lector puede fallar por su cuenta; se prueba el siguiente
            errores.append(f"{origen}: {exc}")
            continue
        if datos.get("duracion"):
            extra = {k: v for k, v in (datos.get("extra") or {}).items() if v not in (None, "")}
            extra["origen_info"] = origen
            ancho, alto = int(datos.get("ancho") or 0), int(datos.get("alto") or 0)
            if abs(_a_float(extra.get("rotacion")) or 0.0) % 180 in _ROTACIONES_VERTICALES:
                # grabado en vertical (iPhone): el archivo guarda 1920x1080 con rotación ±90; se ve como 1080x1920
                ancho, alto = alto, ancho
            return InfoVideo(ruta=ruta, nombre=sanear_nombre(ruta.stem), duracion=float(datos["duracion"]),
                             fps=float(datos.get("fps") or 0.0), ancho=ancho, alto=alto,
                             tamano_bytes=ruta.stat().st_size, extra=extra)
        errores.append(f"{origen}: no informa la duración")
    raise RuntimeError(f"No se pudo leer la información de {ruta.name} ({'; '.join(errores)})")


# ----------------------------------------------------------------------------
# HDR
# ----------------------------------------------------------------------------
def es_hdr(info_extra: dict) -> bool:
    """True si la transferencia de color es PQ (``smpte2084``) o HLG (``arib-std-b67``)."""
    valor = (info_extra or {}).get("color_transfer")
    return bool(valor) and str(valor).strip().lower() in TRANSFERENCIAS_HDR


@functools.lru_cache(maxsize=256)
def _es_hdr_cacheado(ruta: str, ffmpeg: str, _tamano: int, _fecha: int) -> bool:
    try:
        return es_hdr(_info_ffmpeg_stderr(Path(ruta), ffmpeg).get("extra", {}))
    except Exception:
        return False


def _es_hdr_archivo(ruta_video: Path, ffmpeg: str) -> bool:
    """Detecta HDR leyendo la cabecera con ffmpeg (una vez por archivo: cacheado por ruta, tamaño y fecha)."""
    try:
        st = Path(ruta_video).stat()
    except OSError:
        return False
    return _es_hdr_cacheado(str(ruta_video), ffmpeg, st.st_size, st.st_mtime_ns)


def _sufijo_tonemap(ruta_video: Path, ffmpeg: str, log: Callable[[str], None] | None) -> str:
    """``"," + FILTRO_TONEMAP`` si el video es HDR, el tonemap está activo y este ffmpeg puede aplicarlo; si no, ``""``.

    Se registra una sola línea por video diciendo qué se hace con él (conversión a SDR, desactivada
    con ``--sin-tonemap``, o ffmpeg sin los filtros).
    """
    if not _es_hdr_archivo(ruta_video, ffmpeg):
        return ""
    nombre = Path(ruta_video).name
    if not TONEMAP_HDR:
        aplicar, mensaje = False, f"  {nombre} es HDR: conversión a SDR desactivada (--sin-tonemap); se procesa tal cual"
    elif _tonemap_disponible(ffmpeg):
        aplicar, mensaje = True, (f"  {nombre} es HDR: se convierte a SDR (zscale+tonemap) en las capturas y en la "
                                  "copia ligera; si salen oscuras pruebe --sin-tonemap")
    else:
        aplicar, mensaje = False, (f"  aviso: {nombre} es HDR y este ffmpeg no tiene los filtros zscale/tonemap; "
                                   "las capturas pueden verse lavadas")
    clave = str(ruta_video)
    if log is not None and clave not in _avisos_hdr:
        _avisos_hdr.add(clave)
        log(mensaje)
    return "," + FILTRO_TONEMAP if aplicar else ""


def _filtro_escala(ancho_max: int) -> str:
    return f"scale='min({int(ancho_max)},iw)':-2"


def _filtro_escala_copia(lado_menor: int) -> str:
    """Escala de la copia ligera: el lado MENOR queda en ``lado_menor`` (sin ampliar).

    Horizontal 1920x1080 -> 1280x720; vertical 1080x1920 (iPhone en vertical, ya autorrotado por ffmpeg) ->
    720x1280, con la misma cantidad de píxeles para leer los textos de pantalla.  Limitar solo el alto dejaría
    el video vertical en 406x720.
    """
    lado = int(lado_menor)
    return (f"scale=w='if(gt(iw,ih),-2,2*trunc(min({lado},iw)/2))'"
            f":h='if(gt(iw,ih),2*trunc(min({lado},ih)/2),-2)'")


# ----------------------------------------------------------------------------
# Fotogramas
# ----------------------------------------------------------------------------
def extraer_fotograma(ruta_video: Path, t: float, destino: Path, ffmpeg: str,
                      ancho_max: int = config.ANCHO_MAX_CAPTURA) -> Path | None:
    """Un fotograma JPEG en el instante ``t`` (búsqueda rápida, ancho ≤ ``ancho_max``).

    Devuelve None si ffmpeg no produce el archivo (p. ej. ``t`` fuera del video: ffmpeg sale con 0 igual).
    """
    ruta_video, destino = Path(ruta_video), Path(destino)
    if not ruta_video.is_file():
        raise FileNotFoundError(f"No existe el video: {ruta_video}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    filtro = _filtro_escala(ancho_max) + _sufijo_tonemap(ruta_video, ffmpeg, None)
    _ejecutar([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, float(t)):.3f}",
               "-i", ruta_video, "-frames:v", "1", "-vf", filtro, "-pix_fmt", "yuvj420p", "-q:v", "2", destino])
    if destino.is_file() and destino.stat().st_size > 0:
        return destino
    destino.unlink(missing_ok=True)
    return None


def nitidez(ruta_imagen: Path) -> float:
    """Varianza del laplaciano 3x3 en escala de grises sobre una miniatura ≤ 640 px.  Mayor = más nítida."""
    with Image.open(ruta_imagen) as img:
        gris = img.convert("L")
    gris.thumbnail((640, 640))
    a = np.asarray(gris, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    laplaciano = -4.0 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    return float(laplaciano.var())


def extraer_mejor_fotograma(ruta_video: Path, t: float, destino: Path, ffmpeg: str, duracion: float,
                            ventana: float = config.VENTANA_NITIDEZ_SEG, fps_rafaga: float = config.FPS_RAFAGA,
                            ancho_max: int = config.ANCHO_MAX_CAPTURA, *,
                            log: Callable[[str], None] = print) -> tuple[Path | None, float | None]:
    """El fotograma más nítido de la ventana ``[t - ventana, t + ventana]``, guardado en ``destino``.

    ``t`` se acota a ``[0, duracion - 0.5]``.  Devuelve ``(destino, t_real)``; si la ráfaga no produce
    fotogramas se intenta uno solo en ``t`` y luego en ``t - 1``; si nada funciona, ``(None, None)``.
    Nunca lanza por un fotograma: registra el motivo con ``log``.
    """
    ruta_video, destino = Path(ruta_video), Path(destino)
    etiqueta = f"  captura {formatear_tiempo(t)}"
    try:
        if not ruta_video.is_file():
            log(f"{etiqueta}: no existe el video {ruta_video}")
            return None, None
        t = min(max(0.0, float(t)), max(0.0, float(duracion) - 0.5))
        inicio = max(0.0, t - ventana)
        destino.parent.mkdir(parents=True, exist_ok=True)
        # fps y scale primero: el tonemap (caro) solo trabaja sobre los fotogramas conservados y ya reducidos
        filtro = f"fps={fps_rafaga},{_filtro_escala(ancho_max)}{_sufijo_tonemap(ruta_video, ffmpeg, log)}"
        with tempfile.TemporaryDirectory(prefix="rafaga_") as tmp:
            r = _ejecutar([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{inicio:.3f}",
                           "-t", f"{2 * ventana:.3f}", "-i", ruta_video, "-vf", filtro,
                           "-pix_fmt", "yuvj420p", "-q:v", "2", Path(tmp) / "f_%03d.jpg"])
            candidatos = sorted(Path(tmp).glob("f_*.jpg"))
            if candidatos:
                puntuados = []
                for archivo in candidatos:
                    t_i = inicio + (int(archivo.stem.split("_")[1]) - 1) / fps_rafaga
                    puntuados.append((nitidez(archivo), -abs(t_i - t), archivo, t_i))
                # Mayor nitidez; en empate (escena estática), el más cercano a t.
                _, _, mejor, t_real = max(puntuados, key=lambda p: (p[0], p[1]))
                destino.unlink(missing_ok=True)
                shutil.move(str(mejor), str(destino))
                return destino, t_real
            if r.returncode != 0:
                log(f"{etiqueta}: la ráfaga falló ({_cola(r.stderr)})")
        for t_alt in (t, max(0.0, t - 1.0)):
            if extraer_fotograma(ruta_video, t_alt, destino, ffmpeg, ancho_max):
                log(f"{etiqueta}: la ráfaga no dio fotogramas; se usó el fotograma en {t_alt:.1f} s")
                return destino, t_alt
        log(f"{etiqueta}: ffmpeg no produjo ningún fotograma")
        return None, None
    except Exception as exc:
        log(f"{etiqueta}: error al extraer el fotograma ({exc})")
        return None, None


# ----------------------------------------------------------------------------
# Escenas, audio y copia ligera
# ----------------------------------------------------------------------------
def detectar_escenas(ruta_video: Path, ffmpeg: str,
                     umbral: float = config.UMBRAL_ESCENA) -> list[tuple[float, float]]:
    """Cambios de plano con el filtro ``scdet``: ``[(tiempo_seg, puntuacion)]`` ordenados por tiempo.

    ``umbral`` es la puntuación mínima de scdet (escala 0-100; se acota a ese rango).
    """
    ruta_video = Path(ruta_video)
    if not ruta_video.is_file():
        raise FileNotFoundError(f"No existe el video: {ruta_video}")
    umbral = min(100.0, max(0.0, float(umbral)))
    r = _ejecutar([ffmpeg, "-hide_banner", "-nostats", "-i", ruta_video, "-an", "-sn", "-dn",
                   "-vf", f"scdet=threshold={umbral}", "-f", "null", "-"])
    escenas = [(float(t), float(s)) for s, t in _PATRON_SCDET.findall(r.stderr)]
    if r.returncode != 0 and not escenas:
        raise RuntimeError(f"ffmpeg no pudo analizar las escenas de {ruta_video.name}: {_cola(r.stderr)}")
    return sorted(escenas)


def extraer_audio_16k(ruta_video: Path, destino_wav: Path, ffmpeg: str) -> Path:
    """WAV PCM 16 bits, mono, 16 kHz (formato nativo de Whisper).  RuntimeError si ffmpeg falla."""
    ruta_video, destino = Path(ruta_video), Path(destino_wav)
    if not ruta_video.is_file():
        raise FileNotFoundError(f"No existe el video: {ruta_video}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    r = _ejecutar([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", ruta_video, "-vn", "-sn", "-dn",
                   "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", destino])
    if r.returncode != 0 or not destino.is_file() or destino.stat().st_size == 0:
        destino.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg no pudo extraer el audio de {ruta_video.name}: {_cola(r.stderr)}")
    return destino


def _ejecutar_con_progreso(cmd: list, duracion: float | None, log: Callable[[str], None]) -> tuple[int, str]:
    """Ejecuta ffmpeg con ``-progress pipe:1`` e informa el avance; devuelve (código de salida, stderr)."""
    ultimo_aviso = -1.0
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proceso = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=err,
                                   encoding="utf-8", errors="replace")
        for linea in proceso.stdout or ():
            m = _PATRON_PROGRESO.match(linea)
            if not m:
                continue
            segundos = int(m[1]) / 1_000_000
            if duracion:   # un aviso cada ~10 % del video
                pct = min(100.0, 100.0 * segundos / duracion)
                if pct - ultimo_aviso >= 10.0:
                    ultimo_aviso = pct
                    log(f"    {pct:3.0f} % ({formatear_tiempo(segundos)} de {formatear_tiempo(duracion)})")
            elif segundos - ultimo_aviso >= 60.0:   # duración desconocida: cada minuto de video
                ultimo_aviso = segundos
                log(f"    procesado {formatear_tiempo(segundos)}")
        codigo = proceso.wait()
        err.seek(0)
        return codigo, err.read()


def transcodificar_para_subida(ruta_video: Path, destino_mp4: Path, ffmpeg: str,
                               alto: int = config.TRANSCODIFICAR_ALTO, fps: int = config.TRANSCODIFICAR_FPS,
                               *, log: Callable[[str], None] = print) -> Path:
    """Copia ligera MP4 (H.264, lado menor ≤ ``alto`` px, ``fps`` fotogramas/s, AAC mono) para subir a Gemini.

    ``alto`` es el lado MENOR de la copia: 720 -> 1280x720 en horizontal y 720x1280 en vertical (ver
    ``_filtro_escala_copia``).  Conserva los tiempos (no corta nada), no amplía videos más pequeños, aplica
    tonemap si el original es HDR (después de fps/scale: mucho más rápido) y solo copia la primera pista de
    video y de audio (los .MOV de iPhone traen pistas de datos que el mp4 no admite).  RuntimeError si ffmpeg falla.
    """
    ruta_video, destino = Path(ruta_video), Path(destino_mp4)
    if not ruta_video.is_file():
        raise FileNotFoundError(f"No existe el video: {ruta_video}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    filtro = f"fps={fps},{_filtro_escala_copia(alto)}{_sufijo_tonemap(ruta_video, ffmpeg, log)}"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-i", ruta_video,
           "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", filtro, "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", f"{config.TRANSCODIFICAR_AUDIO_KBPS}k", "-ac", "1",
           "-movflags", "+faststart", "-progress", "pipe:1", destino]
    try:
        duracion = _info_ffmpeg_stderr(ruta_video, ffmpeg).get("duracion")
    except Exception:
        duracion = None
    log(f"  transcodificando {ruta_video.name} -> {destino.name} (lado menor {alto} px, {fps} fps, audio mono "
        f"{config.TRANSCODIFICAR_AUDIO_KBPS} kbps)...")
    codigo, stderr = _ejecutar_con_progreso(cmd, duracion, log)
    if codigo != 0 or not destino.is_file() or destino.stat().st_size == 0:
        destino.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg no pudo transcodificar {ruta_video.name}: {_cola(stderr)}")
    log(f"  copia ligera lista: {destino.stat().st_size / (1024 * 1024):.1f} MB")
    return destino


def necesita_transcodificar(info: InfoVideo, umbral_mb: int = config.UMBRAL_TRANSCODIFICAR_MB) -> str | None:
    """Motivo por el que el archivo no se sube tal cual (extensión o tamaño), o None si se sube directo.

    Estrategia iPhone: el .MOV HEVC de varios GB nunca se sube; se sube la copia ligera y las capturas
    salen del original en alta calidad.
    """
    extension = Path(info.ruta).suffix.lower()
    if extension not in config.EXTENSIONES_SUBIDA_DIRECTA:
        return f"extensión {extension or '(sin extensión)'}: se sube una copia ligera"
    megas = info.tamano_bytes / (1024 * 1024)
    if megas > umbral_mb:
        return f"tamaño {megas:.0f} MB > {umbral_mb} MB"
    return None
