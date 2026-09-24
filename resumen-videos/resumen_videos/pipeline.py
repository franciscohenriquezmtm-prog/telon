"""Orquestación de todo el proceso: carpeta de videos → ``salida/<nombre>/``.

Por cada video: información con ffmpeg, análisis según el modo (Gemini, batch,
local o simulado), ``momentos.json`` guardado en cuanto termina el análisis
(para no perder un resultado pagado), capturas nítidas del archivo ORIGINAL,
refinado opcional con esas capturas en alta resolución (Gemini vuelve a leer
textos, valores e iconos de pantalla), anotaciones (círculo/flecha si el modelo
indica una zona) y documentos ``.docx`` + ``.pdf``.  Cada video escribe su propio
``log.txt`` (y ``error.txt`` si falla) y un fallo nunca detiene a los demás.

``--regenerar``: si ya existe ``momentos.json`` se salta el análisis (sin API) y
se rehacen capturas, anotaciones y documentos a partir de él (permite corregir
el JSON a mano o pegar un análisis hecho en el chat de Gemini).

El modo batch va en dos pasos: ``procesar_carpeta`` con ``modo="batch"`` sube
los videos, envía el lote y guarda ``salida/_lotes/<id>.json``; más tarde, con
``modo="batch-recoger"`` y ``lote_id``, se recogen las respuestas y se generan
capturas y documentos.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import traceback
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from google.genai import errors

from . import __version__, config, documentos, gemini, local, video
from .anotar import anotar_captura
from .modelos import InfoVideo, Momento, ResultadoAnalisis, ResultadoVideo, Uso, formatear_tiempo

MODOS = ("gemini", "batch", "batch-recoger", "local", "simulado")
MODOS_CON_API = ("gemini", "batch", "batch-recoger")
RESOLUCIONES = ("baja", "media", "alta")
ALTOS_COPIA = (480, 720, 1080)
_MB = 1024 * 1024
_ANCHO_NOMBRE_TABLA = 38        # ancho máximo de la columna Video en la tabla resumen
_ANCHO_ERROR_TABLA = 60         # caracteres del mensaje de error que se muestran en la tabla
_PISTAS_API = {                 # ayuda breve según el código HTTP de la API de Gemini
    400: "petición rechazada: revise el modelo, --resolucion o el tamaño del video",
    401: "clave inválida: revise GEMINI_API_KEY en .env",
    403: "sin permiso: revise la clave o la facturación en ai.google.dev",
    404: "modelo no disponible para esta clave: pruebe otro --modelo",
    429: "cuota o límite de velocidad: espere y reintente, o use --pausa",
    500: "error del servidor de Google: reintente más tarde",
    503: "servicio saturado: reintente más tarde",
}


# ----------------------------------------------------------------------------
# Estructuras
# ----------------------------------------------------------------------------
@dataclass
class Opciones:
    """Espejo de las opciones de la CLI; los valores por defecto salen de ``config``."""

    carpeta_videos: Path
    carpeta_salida: Path
    modo: str = "gemini"                    # gemini | batch | batch-recoger | local | simulado
    modelo: str = config.MODELO_POR_DEFECTO
    fps: Optional[float] = None
    api_key: Optional[str] = None
    equipo: str = config.EQUIPO_POR_DEFECTO
    redactor: Optional[str] = None
    resolucion: str = config.RESOLUCION_VIDEO           # baja | media | alta (cómo mira Gemini el video)
    copia_alto: int = config.TRANSCODIFICAR_ALTO        # alto de la copia ligera que se sube (480|720|1080)
    subir_original: bool = False                        # subir el archivo tal cual si cabe en max_subida_mb
    refinar: bool = config.REFINAR_CON_CAPTURAS         # segunda pasada con las capturas en alta resolución
    regenerar: bool = False                             # reutilizar momentos.json y rehacer capturas/documentos
    lote_id: Optional[str] = None
    esperar_lote: bool = False
    whisper_modelo: Optional[str] = config.WHISPER_MODELO
    offline: bool = False
    ffmpeg: Optional[str] = None
    ffprobe: Optional[str] = None
    max_subida_mb: int = config.MAX_SUBIDA_MB
    timeout_procesado: int = config.TIMEOUT_PROCESADO_SEG
    precio_entrada: Optional[float] = None
    precio_salida: Optional[float] = None
    conservar_subida: bool = False
    pausa: float = 0.0
    max_momentos: Optional[int] = None
    importancia_minima: int = 1
    por_pagina: "str | int" = "auto"
    incluir_indice: bool = True
    anotar: bool = True
    tramo_min: int = config.TRAMO_MAX_MIN
    forzar: bool = False
    solo: Optional[list] = None
    verbose: bool = False


@dataclass
class ResumenEjecucion:
    """Resultado de una ejecución completa: un ``ResultadoVideo`` por video más totales."""

    resultados: list                        # list[ResultadoVideo]
    uso_total: Optional[Uso]                # suma de los análisis pagados en esta ejecución (None sin API)
    segundos: float

    def exitosos(self) -> list:
        return [r for r in self.resultados if r.exito]

    def fallidos(self) -> list:
        return [r for r in self.resultados if not r.exito]

    def tabla(self) -> str:
        """Tabla alineada para consola (Video | Momentos | Págs | Tokens | Costo est. | Estado) y totales."""
        cabecera = ("Video", "Momentos", "Págs", "Tokens", "Costo est.", "Estado")
        filas = [_fila_tabla(r) for r in self.resultados]
        anchos = [max([len(c)] + [len(f[i]) for f in filas]) for i, c in enumerate(cabecera)]
        numericas = {1, 2, 3, 4}

        def formatear(fila) -> str:
            celdas = [v.rjust(anchos[i]) if i in numericas else v.ljust(anchos[i]) for i, v in enumerate(fila)]
            return " | ".join(celdas).rstrip()

        lineas = [formatear(cabecera), "-+-".join("-" * a for a in anchos)]
        lineas.extend(formatear(f) for f in filas)
        if not filas:
            lineas.append("(ningún video)")
        lineas.extend(["", self._linea_costo(), self._linea_totales()])
        return "\n".join(lineas)

    def _linea_costo(self) -> str:
        uso = self.uso_total
        if uso is None:
            return "ESTIMACIÓN de costo total: US$ 0.0000 (sin API: modo local o simulado)"
        if uso.costo_usd is None:
            return (f"ESTIMACIÓN de costo total: desconocida ({uso.tokens_total} tokens; el modelo {uso.modelo!r} "
                    "no tiene precio en config: use --precio-entrada y --precio-salida)")
        return (f"ESTIMACIÓN de costo total: US$ {uso.costo_usd:.4f} ({uso.tokens_total} tokens"
                f"{', batch' if uso.batch else ''}; precios de config, verificar en ai.google.dev)")

    def _linea_totales(self) -> str:
        omitidos = sum(1 for r in self.resultados if r.omitido)
        ok = sum(1 for r in self.resultados if r.exito and not r.omitido)
        return (f"Total: {len(self.resultados)} video(s): {ok} OK, {len(self.fallidos())} con error, "
                f"{omitidos} omitido(s); tiempo {_duracion_legible(self.segundos)}")


def _fila_tabla(r: ResultadoVideo) -> tuple:
    analisis = r.analisis
    uso = analisis.uso if analisis is not None else None
    nombre = r.info.nombre
    if len(nombre) > _ANCHO_NOMBRE_TABLA:
        nombre = nombre[:_ANCHO_NOMBRE_TABLA - 1] + "…"
    momentos = str(len(analisis.momentos)) if analisis is not None else "-"
    paginas = str(r.paginas_pdf) if isinstance(r.paginas_pdf, int) and r.paginas_pdf >= 0 else "-"
    tokens = str(uso.tokens_total) if uso is not None else "-"
    if uso is None:
        costo = "-"
    elif uso.costo_usd is None:
        costo = "?"
    else:
        costo = f"US$ {uso.costo_usd:.4f}"
    if r.omitido:
        estado = "ya procesado (omitido)"
    elif not r.exito:
        estado = "ERROR: " + _una_linea(r.error or "desconocido")[:_ANCHO_ERROR_TABLA]
    elif analisis is None:
        estado = "pendiente (lote)"
    else:
        estado = "OK" + (" (respuesta cortada)" if analisis.truncado else "")
    return nombre, momentos, paginas, tokens, costo, estado


def _una_linea(texto: str) -> str:
    return " ".join(str(texto).split())


def _duracion_legible(segundos: float) -> str:
    segundos = max(0, int(round(segundos)))
    if segundos < 60:
        return f"{segundos} s"
    if segundos < 3600:
        return f"{segundos // 60} min {segundos % 60} s"
    return f"{segundos // 3600} h {segundos % 3600 // 60} min"


def sumar_uso(resultados: list, modelo: str) -> Uso | None:
    """Suma el ``Uso`` de los análisis pagados en esta ejecución (omitidos y fallidos no cuentan)."""
    usos = [r.analisis.uso for r in resultados
            if r.exito and not r.omitido and r.analisis is not None and r.analisis.uso is not None]
    if not usos:
        return None
    total = Uso(modelo=modelo, batch=all(u.batch for u in usos))
    for uso in usos:
        total.sumar(uso)
    if any(u.costo_usd is None for u in usos):
        total.costo_usd = None      # una suma parcial engañaría: mejor "desconocido"
    return total


# ----------------------------------------------------------------------------
# Log por video, carpetas y JSON
# ----------------------------------------------------------------------------
def crear_log(carpeta: Path, consola: Callable[[str], None] = print) -> Callable[[str], None]:
    """``log`` que escribe cada línea, con hora ``HH:MM:SS``, en consola y en ``<carpeta>/log.txt``."""
    ruta = Path(carpeta) / config.NOMBRE_LOG

    def log(mensaje: str) -> None:
        linea = f"{datetime.now():%H:%M:%S} {mensaje}"
        consola(linea)
        try:
            with ruta.open("a", encoding="utf-8", errors="replace") as archivo:
                archivo.write(linea + "\n")
        except OSError:
            pass    # un log que no se puede escribir no debe detener el proceso

    return log


def _ruta_video_registrada(carpeta: Path) -> str | None:
    """Ruta del video que generó ``<carpeta>/momentos.json`` (None si no existe o no se puede leer)."""
    try:
        datos = json.loads((carpeta / config.NOMBRE_JSON).read_text(encoding="utf-8"))
        return str(datos["video"]["ruta"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _misma_ruta(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(str(b))


def _carpeta_para(info: InfoVideo, op: Opciones, con_sufijo: bool = False) -> Path:
    nombre = info.nombre
    if con_sufijo:
        nombre += "-" + hashlib.sha1(str(info.ruta).encode("utf-8")).hexdigest()[:6]
    return Path(op.carpeta_salida) / nombre


def preparar_carpeta_salida(info: InfoVideo, op: Opciones) -> Path:
    """``salida/<nombre>/`` (se crea).  Si ya la ocupa otro video con el mismo nombre → sufijo ``-<6 hex>``."""
    carpeta = _carpeta_para(info, op)
    registrada = _ruta_video_registrada(carpeta)
    if registrada is not None and not _misma_ruta(registrada, info.ruta):
        carpeta = _carpeta_para(info, op, con_sufijo=True)
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _escribir_json(ruta: Path, datos: dict) -> None:
    """Escritura atómica (archivo temporal + reemplazo) para no dejar nunca un JSON a medias."""
    temporal = ruta.with_name(ruta.name + ".tmp")
    with temporal.open("w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
    os.replace(temporal, ruta)


def _leer_json(ruta: Path) -> dict:
    """Lee un JSON de la carpeta de salida; RuntimeError con mensaje claro si no es válido."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"No se pudo leer {ruta}: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"{ruta} no es un JSON válido: {exc}") from exc
    if not isinstance(datos, dict):
        raise RuntimeError(f"{ruta} no contiene un objeto JSON.")
    return datos


def guardar_json(carpeta: Path, info: InfoVideo, analisis: ResultadoAnalisis, extra: dict | None = None) -> Path:
    """Escribe ``<carpeta>/momentos.json`` (capturas con ruta relativa a la carpeta) y devuelve su ruta."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    datos = {
        "video": info.a_dict(),
        "generado": datetime.now().isoformat(timespec="seconds"),
        "version": __version__,
        "analisis": analisis.a_dict(base=carpeta),
        "documentos": {},
    }
    if extra:
        datos.update(extra)
    ruta = carpeta / config.NOMBRE_JSON
    _escribir_json(ruta, datos)
    return ruta


def _momento_desde_dict(datos: dict, base: Path) -> Momento:
    def absoluta(ruta):
        if not ruta:
            return None
        return str(base / ruta) if not Path(ruta).is_absolute() else str(ruta)

    return Momento(
        tiempo_seg=float(datos.get("tiempo_seg") or 0.0),
        titulo=str(datos.get("titulo") or ""),
        descripcion=str(datos.get("descripcion") or ""),
        importancia=int(datos.get("importancia") or 3),
        fuente=str(datos.get("fuente") or "ambos"),
        seccion=datos.get("seccion"),
        zona=datos.get("zona"),
        ruta_captura=absoluta(datos.get("captura")),
        ruta_captura_anotada=absoluta(datos.get("captura_anotada")),
        tiempo_real_seg=datos.get("tiempo_real_seg"),
        puntaje=datos.get("puntaje"),
    )


def _uso_desde_dict(datos) -> Uso | None:
    if not isinstance(datos, dict):
        return None
    conocidos = {f.name for f in fields(Uso)}
    valores = {k: v for k, v in datos.items() if k in conocidos}
    valores.setdefault("modelo", "")
    return Uso(**valores)


def cargar_json(carpeta: Path) -> tuple[ResultadoAnalisis | None, dict]:
    """Lee el ``momentos.json`` de un video ya procesado: ``(análisis, documentos)``; ``(None, {})`` si no se puede."""
    carpeta = Path(carpeta)
    try:
        datos = json.loads((carpeta / config.NOMBRE_JSON).read_text(encoding="utf-8"))
        a = datos["analisis"]
        analisis = ResultadoAnalisis(
            momentos=[_momento_desde_dict(m, carpeta) for m in a.get("momentos") or []],
            modo=str(a.get("modo") or ""), modelo=str(a.get("modelo") or ""), resumen=a.get("resumen"),
            uso=_uso_desde_dict(a.get("uso")), truncado=bool(a.get("truncado")), avisos=list(a.get("avisos") or []),
            transcripcion=a.get("transcripcion"), tramos=int(a.get("tramos") or 1), titulo=a.get("titulo"))
        return analisis, dict(datos.get("documentos") or {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, {}


def _info_desde_dict(datos: dict) -> InfoVideo:
    """``InfoVideo`` a partir de ``InfoVideo.a_dict()`` (lote guardado)."""
    return InfoVideo(ruta=Path(datos["ruta"]), nombre=str(datos["nombre"]), duracion=float(datos.get("duracion_seg") or 0.0),
                     fps=float(datos.get("fps") or 0.0), ancho=int(datos.get("ancho") or 0), alto=int(datos.get("alto") or 0),
                     tamano_bytes=int(datos.get("tamano_bytes") or 0), extra=dict(datos.get("extra") or {}))


def _info_minima(ruta: Path) -> InfoVideo:
    """``InfoVideo`` de un archivo que no se pudo leer (para tener carpeta de salida y fila en la tabla)."""
    ruta = Path(ruta).resolve()
    try:
        tamano = ruta.stat().st_size
    except OSError:
        tamano = 0
    return InfoVideo(ruta=ruta, nombre=video.sanear_nombre(ruta.stem), duracion=0.0, fps=0.0, ancho=0, alto=0,
                     tamano_bytes=tamano)


# ----------------------------------------------------------------------------
# Capturas y anotaciones
# ----------------------------------------------------------------------------
def capturar_momentos(info: InfoVideo, momentos: list, carpeta: Path, ffmpeg: str, *, anotar: bool = True,
                      log: Callable[[str], None] = print) -> None:
    """Extrae ``capturas/NN_mm-ss.jpg`` (el fotograma más nítido cerca del tiempo) y, si hay zona, la anotada.

    Rellena ``ruta_captura``, ``tiempo_real_seg`` y ``ruta_captura_anotada`` de cada momento.  Las
    capturas de una ejecución anterior se borran antes.  Nunca lanza por un fotograma.
    """
    carpeta_capturas = Path(carpeta) / config.CARPETA_CAPTURAS
    carpeta_capturas.mkdir(parents=True, exist_ok=True)
    for vieja in carpeta_capturas.glob("*.jpg"):
        vieja.unlink(missing_ok=True)
    total = len(momentos)
    ancho = max(2, len(str(total)))
    log(f"Extrayendo {total} capturas de {info.ruta.name}…")
    fallidas = 0
    for n, momento in enumerate(momentos, 1):
        base = f"{n:0{ancho}d}_{momento.tiempo.replace(':', '-')}"
        ruta, t_real = video.extraer_mejor_fotograma(info.ruta, momento.tiempo_seg, carpeta_capturas / f"{base}.jpg",
                                                     ffmpeg, info.duracion, log=log)
        momento.ruta_captura = str(ruta) if ruta else None
        momento.tiempo_real_seg = t_real
        momento.ruta_captura_anotada = None
        if ruta is None:
            fallidas += 1
        if n % 10 == 0 and n < total:
            log(f"  {n}/{total} capturas")
    log(f"Capturas: {total - fallidas} extraídas, {fallidas} fallidas")
    if anotar:
        anotar_momentos(momentos, log=log)


def anotar_momentos(momentos: list, *, log: Callable[[str], None] = print) -> int:
    """Dibuja la zona señalada sobre las capturas ya extraídas (``NN_mm-ss_anotada.jpg``); devuelve cuántas."""
    anotadas = 0
    for momento in momentos:
        momento.ruta_captura_anotada = None
        if not (momento.ruta_captura and momento.zona):
            continue
        origen = Path(momento.ruta_captura)
        destino = origen.with_name(origen.stem + "_anotada.jpg")
        if anotar_captura(origen, momento.zona, destino) is not None:
            momento.ruta_captura_anotada = str(destino)
            anotadas += 1
    log(f"Anotaciones: {anotadas} capturas con círculo/flecha.")
    return anotadas


# ----------------------------------------------------------------------------
# Errores
# ----------------------------------------------------------------------------
def mensaje_de_error(exc: BaseException) -> str:
    """Mensaje en español para la consola y ``error.txt`` (errores de la API con código, estado y pista)."""
    if isinstance(exc, errors.APIError):
        pista = _PISTAS_API.get(int(exc.code or 0))
        return (f"Error de la API de Gemini ({exc.code} {exc.status or ''}): {exc.message or exc}"
                + (f" [{pista}]" if pista else ""))
    if isinstance(exc, FileNotFoundError):
        return f"No se encontró un archivo: {exc}"
    if isinstance(exc, (RuntimeError, ValueError)):
        return str(exc) or type(exc).__name__
    return f"{type(exc).__name__}: {exc}"


def registrar_error(carpeta: Path, exc: BaseException, info: InfoVideo, verbose: bool = False,
                    log: Callable[[str], None] = print) -> str:
    """Escribe ``<carpeta>/error.txt`` (mensaje amable + traza), lo anuncia por ``log`` y devuelve el mensaje."""
    mensaje = mensaje_de_error(exc)
    traza = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    carpeta = Path(carpeta)
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / config.NOMBRE_ERROR).write_text(
            f"Video: {info.ruta}\nFecha: {datetime.now():%Y-%m-%d %H:%M:%S}\nresumen_videos {__version__}\n\n"
            f"{mensaje}\n\n--- traza técnica ---\n{traza}", encoding="utf-8", errors="replace")
    except OSError:
        pass
    log(f"ERROR en {info.ruta.name}: {mensaje} (detalle en {carpeta / config.NOMBRE_ERROR})")
    if verbose:
        for linea in traza.rstrip().splitlines():
            log("    " + linea)
    return mensaje


# ----------------------------------------------------------------------------
# Subida a Gemini (copia ligera o archivo original)
# ----------------------------------------------------------------------------
def precios_de(op: Opciones) -> dict | None:
    """``{"entrada", "salida"}`` (US$/millón) si el usuario fijó precios; None para usar la tabla de config."""
    if op.precio_entrada is None and op.precio_salida is None:
        return None
    base = config.PRECIOS_USD_POR_MILLON.get(op.modelo, {})
    entrada = op.precio_entrada if op.precio_entrada is not None else base.get("entrada")
    salida = op.precio_salida if op.precio_salida is not None else base.get("salida")
    if entrada is None or salida is None:
        raise ValueError(f"El modelo {op.modelo!r} no tiene precio en config: indique --precio-entrada y "
                         "--precio-salida (los dos).")
    return {"entrada": float(entrada), "salida": float(salida)}


def _preparar_subida(info: InfoVideo, op: Opciones, ffmpeg: str, temporal: Path,
                     log: Callable[[str], None]) -> Path:
    """Decide qué archivo se sube: el original o una copia ligera MP4 (``copia_alto`` p) en ``temporal``."""
    megas = info.tamano_bytes / _MB
    extension = info.ruta.suffix.lower()
    if op.subir_original:
        if extension not in config.MIME_SUBIDA:
            log(f"Aviso: --subir-original no aplicable (extensión {extension} sin tipo MIME para la subida); "
                "se sube una copia ligera.")
        elif megas > op.max_subida_mb:
            log(f"Aviso: --subir-original no aplicable ({megas:.0f} MB > {op.max_subida_mb} MB); "
                "se sube una copia ligera.")
        else:
            log(f"Se sube el archivo original tal cual (--subir-original, {megas:.0f} MB).")
            return info.ruta
    else:
        motivo = video.necesita_transcodificar(info, config.UMBRAL_TRANSCODIFICAR_MB)
        if motivo is None:
            log(f"Se sube el archivo tal cual ({megas:.0f} MB).")
            return info.ruta
        log(f"{motivo}: se sube una copia ligera a {op.copia_alto}p; las capturas saldrán del original.")
    destino = temporal / f"{info.nombre}_subida.mp4"
    video.transcodificar_para_subida(info.ruta, destino, ffmpeg, alto=op.copia_alto, log=log)
    megas_copia = destino.stat().st_size / _MB
    if megas_copia > op.max_subida_mb:
        raise RuntimeError(f"La copia ligera pesa {megas_copia:.0f} MB, más que el límite de subida "
                           f"({op.max_subida_mb} MB): use --copia-alto 480 o divida el video.")
    return destino


def _subir(info: InfoVideo, op: Opciones, cliente, ffmpeg: str, log: Callable[[str], None]) -> "object":
    """Sube el original o la copia ligera (archivo temporal que se borra al terminar) y devuelve el ``File``."""
    temporal = Path(tempfile.mkdtemp(prefix="resumen_videos_"))
    try:
        ruta_subida = _preparar_subida(info, op, ffmpeg, temporal, log)
        return gemini.subir_video(cliente, ruta_subida, info.nombre, timeout_procesado=op.timeout_procesado, log=log)
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


# ----------------------------------------------------------------------------
# Análisis, refinado y documentos de un video
# ----------------------------------------------------------------------------
def _analizar_con_gemini(info: InfoVideo, op: Opciones, cliente, ffmpeg: str,
                         log: Callable[[str], None]) -> ResultadoAnalisis:
    """Subida → ``analizar_video`` → borrado del archivo remoto (salvo ``conservar_subida``) → redactor opcional."""
    precios = precios_de(op)
    archivo = _subir(info, op, cliente, ffmpeg, log)
    try:
        resultado = gemini.analizar_video(
            cliente, archivo, info, modelo=op.modelo, fps=op.fps, tramo_max_seg=op.tramo_min * 60,
            precios=precios, equipo=op.equipo, resolucion=op.resolucion, max_momentos=op.max_momentos,
            importancia_minima=op.importancia_minima, log=log)
    finally:
        if op.conservar_subida:
            log(f"Archivo remoto conservado (--conservar-subida): {archivo.name}")
        else:
            gemini.eliminar_archivo(cliente, archivo, log=log)
    if op.redactor:
        resultado = gemini.pulir_redaccion(cliente, resultado, op.redactor, precios, log=log)
    return resultado


def _analizar(info: InfoVideo, op: Opciones, cliente, ffmpeg: str, log: Callable[[str], None]) -> ResultadoAnalisis:
    """Análisis según ``op.modo`` (los modos batch se gestionan en ``procesar_carpeta``)."""
    if op.modo == "gemini":
        if cliente is None:
            raise RuntimeError("Falta el cliente de Gemini: cree un archivo .env con GEMINI_API_KEY=... "
                               "(ver .env.ejemplo) o use --local / --simular.")
        return _analizar_con_gemini(info, op, cliente, ffmpeg, log)
    if op.modo == "local":
        return local.analizar_local(info.ruta, info, ffmpeg, whisper_modelo=op.whisper_modelo, offline=op.offline,
                                    max_momentos=op.max_momentos, importancia_minima=op.importancia_minima, log=log)
    if op.modo == "simulado":
        return local.analizar_simulado(info, log=log)
    if op.modo in ("batch", "batch-recoger"):
        raise ValueError(f"El modo {op.modo!r} se gestiona con procesar_carpeta (envío y recogida del lote); "
                         "este video no tiene momentos.json que regenerar.")
    raise ValueError(f"Modo desconocido: {op.modo!r} (use {', '.join(MODOS)}).")


def _modelo_refinado(analisis: ResultadoAnalisis, op: Opciones) -> str:
    """Modelo para el refinado: el que realmente respondió el análisis (tras fallbacks; sin el sufijo del
    redactor ``a+b``) si el análisis viene de Gemini, y si no (local/simulado regenerados) el de la CLI."""
    if analisis.modo.startswith("gemini") and analisis.modelo:
        return analisis.modelo.split("+", 1)[0].strip() or op.modelo
    return op.modelo


def _refinar(cliente, analisis: ResultadoAnalisis, op: Opciones, log: Callable[[str], None]) -> ResultadoAnalisis:
    """Segunda pasada con las capturas en alta resolución; ante cualquier fallo conserva el análisis original."""
    con_captura = sum(1 for m in analisis.momentos if m.ruta_captura)
    if not con_captura:
        log("Refinado omitido: ninguna captura disponible.")
        return analisis
    modelo = _modelo_refinado(analisis, op)
    log(f"Refinando títulos y descripciones con {con_captura} capturas en alta resolución ({modelo})…")
    try:
        return gemini.refinar_con_capturas(cliente, analisis, modelo, precios_de(op), equipo=op.equipo, log=log)
    except Exception as exc:  # noqa: BLE001 - el refinado es opcional: nunca debe tirar un análisis ya pagado
        log(f"Aviso: el refinado falló y se conserva el análisis original ({exc}).")
        analisis.avisos.append(f"Refinado con capturas omitido: {exc}")
        return analisis


def _finalizar_video(info: InfoVideo, carpeta: Path, analisis: ResultadoAnalisis, op: Opciones, cliente,
                     ffmpeg: str, log: Callable[[str], None]) -> tuple[ResultadoAnalisis, Path, Path, int]:
    """Del análisis a los documentos: JSON → capturas → refinado → anotaciones → docx/pdf → JSON actualizado."""
    guardar_json(carpeta, info, analisis)
    log(f"{config.NOMBRE_JSON} guardado ({len(analisis.momentos)} momentos).")
    if not analisis.momentos:
        raise RuntimeError("El análisis no encontró ningún momento: no hay nada que documentar "
                           "(revise el video o los filtros --max-momentos / --importancia-minima).")
    capturar_momentos(info, analisis.momentos, carpeta, ffmpeg, anotar=False, log=log)
    if op.refinar and cliente is not None:
        analisis = _refinar(cliente, analisis, op, log)
        guardar_json(carpeta, info, analisis)
    elif op.refinar and analisis.modo.startswith("gemini"):
        log("Refinado con capturas omitido: no hay cliente de Gemini (sin clave).")
    if op.anotar:
        anotar_momentos(analisis.momentos, log=log)
    docx, pdf, paginas = documentos.generar_documentos(
        info.nombre, analisis.momentos, carpeta, titulo=analisis.titulo, resumen=analisis.resumen,
        duracion=info.duracion, modo=analisis.modo, modelo=analisis.modelo, por_pagina=op.por_pagina,
        incluir_indice=op.incluir_indice, log=log)
    guardar_json(carpeta, info, analisis, extra={"documentos": {"docx": docx.name, "pdf": pdf.name, "paginas": paginas}})
    (Path(carpeta) / config.NOMBRE_ERROR).unlink(missing_ok=True)   # un error de una ejecución anterior ya no aplica
    return analisis, docx, pdf, paginas


def _log_cabecera(info: InfoVideo, carpeta: Path, log: Callable[[str], None]) -> None:
    log(f"=== {info.ruta.name}: {formatear_tiempo(info.duracion)}, {info.ancho}x{info.alto}, {info.fps:g} fps, "
        f"{info.tamano_bytes / _MB:.0f} MB → {carpeta}")


def _resultado_omitido(info: InfoVideo, carpeta: Path, inicio: float, log: Callable[[str], None]) -> ResultadoVideo:
    """Video ya procesado: se carga lo que hay en ``momentos.json`` sin volver a analizar."""
    analisis, docs = cargar_json(carpeta)
    log(f"Ya procesado ({config.NOMBRE_JSON} existe): se omite. Use --forzar para repetir el análisis o "
        "--regenerar para rehacer capturas y documentos sin volver a analizar.")
    paginas = docs.get("paginas")
    return ResultadoVideo(info=info, carpeta_salida=carpeta, exito=True, analisis=analisis,
                          ruta_docx=carpeta / docs["docx"] if docs.get("docx") else None,
                          ruta_pdf=carpeta / docs["pdf"] if docs.get("pdf") else None,
                          paginas_pdf=paginas if isinstance(paginas, int) else None,
                          omitido=True, segundos=time.monotonic() - inicio)


def procesar_video(ruta: Path, op: Opciones, cliente=None, *, log: Callable[[str], None] = print) -> ResultadoVideo:
    """Procesa un video completo hasta sus documentos.  Nunca lanza: un fallo queda en ``error.txt``."""
    inicio = time.monotonic()
    consola = log
    info = _info_minima(ruta)
    carpeta = _carpeta_para(info, op)
    try:
        ffmpeg = video.localizar_ffmpeg(op.ffmpeg)
        info = video.obtener_info(ruta, ffmpeg, video.localizar_ffprobe(op.ffprobe))
        carpeta = preparar_carpeta_salida(info, op)
        log = crear_log(carpeta, consola)
        _log_cabecera(info, carpeta, log)
        existe_json = (carpeta / config.NOMBRE_JSON).is_file()
        if existe_json and not op.regenerar and not op.forzar:
            return _resultado_omitido(info, carpeta, inicio, log)
        if op.regenerar and existe_json:
            analisis, _ = cargar_json(carpeta)
            if analisis is None:
                raise RuntimeError(f"No se pudo leer {carpeta / config.NOMBRE_JSON}: revise que sea un JSON válido "
                                   "con la estructura original (clave 'analisis' con su lista 'momentos').")
            log(f"--regenerar: se reutiliza el análisis de {config.NOMBRE_JSON} ({len(analisis.momentos)} momentos, "
                f"modo {analisis.modo or '?'}); no se vuelve a analizar el video.")
            analisis.avisos.append(f"Regenerado desde {config.NOMBRE_JSON} el {datetime.now():%Y-%m-%d %H:%M}")
        else:
            if op.regenerar:
                log(f"--regenerar: todavía no hay {config.NOMBRE_JSON}; se analiza el video de forma normal.")
            analisis = _analizar(info, op, cliente, ffmpeg, log)
        analisis, docx, pdf, paginas = _finalizar_video(info, carpeta, analisis, op, cliente, ffmpeg, log)
        segundos = time.monotonic() - inicio
        log(f"Listo en {_duracion_legible(segundos)}: {len(analisis.momentos)} momentos, "
            f"{paginas if paginas >= 0 else '?'} páginas → {docx.name}, {pdf.name}")
        return ResultadoVideo(info=info, carpeta_salida=carpeta, exito=True, analisis=analisis, ruta_docx=docx,
                              ruta_pdf=pdf, paginas_pdf=paginas, segundos=segundos)
    except Exception as exc:  # noqa: BLE001 - por contrato: se registra y se sigue con el siguiente video
        if log is consola:      # falló antes de abrir log.txt (archivo ilegible): que el error quede también allí
            log = crear_log(carpeta, consola)
        mensaje = registrar_error(carpeta, exc, info, op.verbose, log)
        return ResultadoVideo(info=info, carpeta_salida=carpeta, exito=False, error=mensaje,
                              segundos=time.monotonic() - inicio)


# ----------------------------------------------------------------------------
# Carpeta completa
# ----------------------------------------------------------------------------
def seleccionar_videos(op: Opciones, log: Callable[[str], None] = print) -> list:
    """Videos de ``op.carpeta_videos`` (FileNotFoundError si no existe), filtrados por ``op.solo`` si se indica."""
    videos = video.listar_videos(op.carpeta_videos)
    if not op.solo:
        return videos

    def claves(ruta: Path) -> set:
        return {ruta.name.lower(), ruta.stem.lower(), video.sanear_nombre(ruta.stem).lower()}

    elegidos = []
    for pedido in op.solo:
        buscado = Path(str(pedido).strip())
        coincidencias = [v for v in videos if claves(buscado) & claves(v)]
        if not coincidencias:
            log(f"Aviso: --solo {pedido!r} no coincide con ningún video de {op.carpeta_videos}.")
        elegidos.extend(v for v in coincidencias if v not in elegidos)
    return elegidos


def _cliente_para(op: Opciones, log: Callable[[str], None]):
    """Cliente de Gemini para los modos con API (None en local/simulado o al regenerar sin clave)."""
    if op.modo not in ("gemini", "batch"):
        return None
    api_key = op.api_key or gemini.obtener_api_key()
    if not api_key:
        if op.regenerar:
            log("Aviso: sin clave de Gemini; --regenerar rehará capturas y documentos sin refinado.")
            return None
        raise RuntimeError("Falta la clave de API de Gemini: cree un archivo .env con GEMINI_API_KEY=... "
                           "(ver .env.ejemplo) o use --local / --simular.")
    if op.regenerar and not op.refinar:
        return None      # nada que pedir a la API
    return gemini.crear_cliente(api_key)


def procesar_carpeta(op: Opciones, *, log: Callable[[str], None] = print) -> ResumenEjecucion:
    """Procesa todos los videos de la carpeta según ``op.modo`` y devuelve el resumen (tabla y costo)."""
    inicio = time.monotonic()
    if op.modo not in MODOS:
        raise ValueError(f"Modo desconocido: {op.modo!r} (use {', '.join(MODOS)}).")
    if op.modo == "batch-recoger":
        resultados = _recoger_lote(op, log)
    else:
        videos = seleccionar_videos(op, log)
        cliente = _cliente_para(op, log)
        if not videos:
            log(f"No hay videos que procesar en {op.carpeta_videos}.")
            resultados = []
        elif op.modo == "batch" and not op.regenerar:
            resultados = _enviar_lote(videos, op, cliente, log)
        else:
            resultados = []
            for i, ruta in enumerate(videos, 1):
                log(f"[{i}/{len(videos)}] {ruta.name}")
                resultados.append(procesar_video(ruta, op, cliente, log=log))
                if op.pausa > 0 and i < len(videos):
                    log(f"Pausa de {op.pausa:g} s antes del siguiente video…")
                    time.sleep(op.pausa)
    return ResumenEjecucion(resultados=resultados, uso_total=sumar_uso(resultados, op.modelo),
                            segundos=time.monotonic() - inicio)


# ----------------------------------------------------------------------------
# Modo batch: envío y recogida
# ----------------------------------------------------------------------------
def _id_lote(nombre_job: str) -> str:
    """Identificador corto y seguro para el nombre de archivo (``batches/abc-123`` → ``abc-123``)."""
    cola = re.sub(r"[^A-Za-z0-9_-]+", "", str(nombre_job or "").rsplit("/", 1)[-1])
    return cola or f"lote-{datetime.now():%Y%m%d-%H%M%S}"


def _estado_lote_legible(job) -> str:
    estado = getattr(job, "state", None)
    return getattr(estado, "name", str(estado)) if estado is not None else "desconocido"


def _enviar_lote(videos: list, op: Opciones, cliente, log: Callable[[str], None]) -> list:
    """Sube cada video (copia ligera si toca), envía el lote y guarda ``salida/_lotes/<id>.json``."""
    ffmpeg = video.localizar_ffmpeg(op.ffmpeg)
    ffprobe = video.localizar_ffprobe(op.ffprobe)
    resultados, peticiones, entradas = [], [], []
    for i, ruta in enumerate(videos, 1):
        inicio = time.monotonic()
        log(f"[{i}/{len(videos)}] {ruta.name}")
        info = _info_minima(ruta)
        carpeta = _carpeta_para(info, op)
        log_v = log
        try:
            info = video.obtener_info(ruta, ffmpeg, ffprobe)
            carpeta = preparar_carpeta_salida(info, op)
            log_v = crear_log(carpeta, log)
            _log_cabecera(info, carpeta, log_v)
            if (carpeta / config.NOMBRE_JSON).is_file() and not op.forzar:
                resultados.append(_resultado_omitido(info, carpeta, inicio, log_v))
                continue
            archivo = _subir(info, op, cliente, ffmpeg, log_v)
            tramos = gemini.calcular_tramos(info.duracion, op.tramo_min * 60)
            peticiones.extend({"archivo": archivo, "info": info, "tramo": t if len(tramos) > 1 else None}
                              for t in tramos)
            entradas.append({"nombre": info.nombre, "ruta": str(info.ruta), "archivo": archivo.name,
                             "duracion": info.duracion, "tramos": [list(t) for t in tramos] if len(tramos) > 1 else [],
                             "carpeta": str(carpeta), "info": info.a_dict()})
            log_v(f"En espera del lote ({len(tramos)} petición(es)).")
            resultados.append(ResultadoVideo(info=info, carpeta_salida=carpeta, exito=True,
                                             segundos=time.monotonic() - inicio))
        except Exception as exc:  # noqa: BLE001 - un video que no se puede subir no detiene al resto
            if log_v is log:    # falló antes de abrir log.txt (archivo ilegible)
                log_v = crear_log(carpeta, log)
            resultados.append(ResultadoVideo(info=info, carpeta_salida=carpeta, exito=False,
                                             error=registrar_error(carpeta, exc, info, op.verbose, log_v),
                                             segundos=time.monotonic() - inicio))
    if not peticiones:
        log("No hay videos que enviar al lote.")
        return resultados

    nombre_lote = f"resumen_videos {datetime.now():%Y-%m-%d %H:%M}"
    try:
        job = gemini.enviar_lote(cliente, peticiones, op.modelo, nombre_lote, equipo=op.equipo, fps=op.fps,
                                 resolucion=op.resolucion, log=log)
    except Exception as exc:  # noqa: BLE001 - el lote no se creó: los videos subidos quedan sin análisis
        pendientes = {e["nombre"] for e in entradas}
        for r in resultados:
            if r.exito and not r.omitido and r.info.nombre in pendientes:
                r.exito = False
                r.error = registrar_error(r.carpeta_salida, exc, r.info, op.verbose, crear_log(r.carpeta_salida, log))
        for entrada in entradas:
            gemini.eliminar_archivo(cliente, entrada["archivo"], log=log)
        return resultados

    id_corto = _id_lote(job.name)
    carpeta_lotes = Path(op.carpeta_salida) / config.CARPETA_LOTES
    carpeta_lotes.mkdir(parents=True, exist_ok=True)
    datos = {"id": id_corto, "job": job.name, "estado": _estado_lote_legible(job), "modelo": op.modelo,
             "enviado": datetime.now().isoformat(timespec="seconds"), "version": __version__,
             "carpeta_salida": str(Path(op.carpeta_salida).resolve()),
             "opciones": {"equipo": op.equipo, "fps": op.fps, "resolucion": op.resolucion, "tramo_min": op.tramo_min,
                          "max_momentos": op.max_momentos, "importancia_minima": op.importancia_minima},
             "videos": entradas}
    ruta_lote = carpeta_lotes / f"{id_corto}.json"
    _escribir_json(ruta_lote, datos)
    log(f"Lote {id_corto} enviado con {len(peticiones)} petición(es) de {len(entradas)} video(s); "
        f"registro en {ruta_lote}. Puede tardar minutos u horas.")
    log(f"Para recoger los resultados: python resumir_videos.py --batch-recoger {id_corto} [--esperar]")
    return resultados


def localizar_lote(carpeta_salida: Path, lote_id: str | None) -> tuple[Path, dict]:
    """``(ruta, datos)`` del lote pedido (id, archivo o nombre del job); sin id, el único lote pendiente."""
    carpeta_lotes = Path(carpeta_salida) / config.CARPETA_LOTES
    archivos = sorted(carpeta_lotes.glob("*.json")) if carpeta_lotes.is_dir() else []
    texto = str(lote_id or "").strip()
    if texto:
        for candidato in (Path(texto), carpeta_lotes / texto, carpeta_lotes / f"{texto}.json"):
            if candidato.is_file():
                return candidato, _leer_json(candidato)
        for archivo in archivos:
            datos = _leer_json(archivo)
            job = str(datos.get("job") or "")
            if job == texto or job.endswith("/" + texto):
                return archivo, datos
        disponibles = ", ".join(a.stem for a in archivos) or "ninguno"
        raise FileNotFoundError(f"No se encontró el lote {texto!r} en {carpeta_lotes} (lotes disponibles: {disponibles}).")
    pendientes = [a for a in archivos if "recogido" not in _leer_json(a)]
    if len(pendientes) == 1:
        return pendientes[0], _leer_json(pendientes[0])
    if not pendientes:
        raise FileNotFoundError(f"No hay lotes pendientes en {carpeta_lotes}: envíe uno con --batch.")
    raise ValueError("Hay varios lotes pendientes; indique cuál con --batch-recoger ID: "
                     + ", ".join(a.stem for a in pendientes))


def _recoger_lote(op: Opciones, log: Callable[[str], None]) -> list:
    """Lee ``salida/_lotes/<id>.json``, espera (si ``esperar_lote``) y termina cada video desde el análisis."""
    ruta_lote, datos = localizar_lote(op.carpeta_salida, op.lote_id)
    api_key = op.api_key or gemini.obtener_api_key()
    if not api_key:
        raise RuntimeError("Falta la clave de API de Gemini para recoger el lote: cree .env con GEMINI_API_KEY=...")
    cliente = gemini.crear_cliente(api_key)
    videos = list(datos.get("videos") or [])
    infos = {v["nombre"]: _info_desde_dict(v["info"]) for v in videos}
    nombre_job = str(datos.get("job") or "")
    job = gemini.estado_lote(cliente, nombre_job)
    log(f"Lote {datos.get('id', ruta_lote.stem)} ({nombre_job}, {len(videos)} video(s)): estado {_estado_lote_legible(job)}.")
    while not gemini.lote_terminado(job):
        if not op.esperar_lote:
            log("El lote todavía no ha terminado: vuelva a ejecutar el mismo comando más tarde "
                "o añada --esperar para quedarse esperando.")
            return [ResultadoVideo(info=infos[v["nombre"]], carpeta_salida=Path(v["carpeta"]), exito=True) for v in videos]
        intervalo = config.INTERVALO_SONDEO_LOTE_SEG
        log(f"  estado {_estado_lote_legible(job)}; nueva consulta en {intervalo} s…")
        if intervalo > 0:
            time.sleep(intervalo)
        job = gemini.estado_lote(cliente, nombre_job)
    log(f"Lote terminado con estado {_estado_lote_legible(job)}.")

    mapa = {v["nombre"]: {"info": infos[v["nombre"]], "tramos": [tuple(t) for t in v.get("tramos") or []] or None,
                          "modelo": datos.get("modelo"), "max_momentos": op.max_momentos,
                          "importancia_minima": op.importancia_minima} for v in videos}
    analisis_por_video = gemini.recoger_lote(cliente, job, mapa, precios_de(op), log=log)
    ffmpeg = video.localizar_ffmpeg(op.ffmpeg)
    resultados = []
    for v in videos:
        inicio = time.monotonic()
        info = infos[v["nombre"]]
        carpeta = Path(v["carpeta"])
        carpeta.mkdir(parents=True, exist_ok=True)
        log_v = crear_log(carpeta, log)
        try:
            resultado = analisis_por_video.get(v["nombre"])
            if resultado is None:
                raise RuntimeError("El lote no contiene ninguna respuesta para este video.")
            if isinstance(resultado, Exception):
                raise resultado
            analisis, docx, pdf, paginas = _finalizar_video(info, carpeta, resultado, op, cliente, ffmpeg, log_v)
            resultados.append(ResultadoVideo(info=info, carpeta_salida=carpeta, exito=True, analisis=analisis,
                                             ruta_docx=docx, ruta_pdf=pdf, paginas_pdf=paginas,
                                             segundos=time.monotonic() - inicio))
        except Exception as exc:  # noqa: BLE001 - se registra y se sigue con el siguiente video del lote
            resultados.append(ResultadoVideo(info=info, carpeta_salida=carpeta, exito=False,
                                             error=registrar_error(carpeta, exc, info, op.verbose, log_v),
                                             segundos=time.monotonic() - inicio))
        finally:
            if v.get("archivo") and not op.conservar_subida:
                gemini.eliminar_archivo(cliente, v["archivo"], log=log_v)
    datos["recogido"] = datetime.now().isoformat(timespec="seconds")
    datos["estado"] = _estado_lote_legible(job)
    _escribir_json(ruta_lote, datos)
    return resultados
