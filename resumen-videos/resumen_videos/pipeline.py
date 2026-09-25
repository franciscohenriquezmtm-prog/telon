"""Orquestación de todo el proceso: carpeta de videos → ``salida/<nombre>/``.

Por cada video: información con ffmpeg, análisis según el modo (Gemini, batch,
local o simulado), ``momentos.json`` guardado en cuanto termina el análisis
(para no perder un resultado pagado), capturas nítidas del archivo ORIGINAL,
refinado opcional con esas capturas en alta resolución (Gemini vuelve a leer
textos, valores e iconos de pantalla), anotaciones (círculo/flecha si el modelo
indica una zona) y documentos ``.docx`` + ``.pdf``.  Cada video escribe su propio
``log.txt`` (y ``error.txt`` si falla) y un fallo nunca detiene a los demás.

``--regenerar``: exige que exista ``momentos.json`` y NUNCA llama a la API (ni
análisis, ni refinado, ni redactor): rehace capturas, anotaciones y documentos a
partir del JSON tal cual (permite corregir el JSON a mano o pegar un análisis
hecho en el chat de Gemini), aplicando ``--max-momentos`` e ``--importancia-minima``
si se indican.  Un video con ``momentos.json`` pero sin documentos (una ejecución
anterior falló a medias) no cuenta como "ya procesado": se retoma desde el JSON,
también sin API.

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
    solo_refinado: bool = False                         # como regenerar, pero repitiendo el refinado con capturas (API)
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
    lupa: bool = True                                   # recuadro con la zona señalada ampliada en cada captura anotada
    transcribir: bool = config.TRANSCRIBIR              # transcripción literal del audio con tiempos (Gemini)
    tramo_min: int = config.TRAMO_MAX_MIN
    forzar: bool = False
    solo: Optional[list] = None
    verbose: bool = False
    temperatura: Optional[float] = config.TEMPERATURA   # None = no se envía (valor por defecto del modelo)


@dataclass
class ResumenEjecucion:
    """Resultado de una ejecución completa: un ``ResultadoVideo`` por video más totales."""

    resultados: list                        # list[ResultadoVideo]
    uso_total: Optional[Uso]                # suma de lo pagado a la API EN ESTA ejecución (None sin API)
    segundos: float
    uso_acumulado: Optional[Uso] = None     # suma de lo pagado por estos videos en todas las ejecuciones (JSON)

    def exitosos(self) -> list:
        return [r for r in self.resultados if r.exito]

    def fallidos(self) -> list:
        return [r for r in self.resultados if not r.exito]

    def tabla(self) -> str:
        """Tabla alineada para consola (Video | Momentos | Págs | Tokens | Costo est. | Acumulado | Estado) y totales.

        ``Tokens`` y ``Costo est.`` son de ESTA ejecución; ``Acumulado`` es todo lo pagado por el video (según su
        ``momentos.json``), también en los omitidos y regenerados.
        """
        cabecera = ("Video", "Momentos", "Págs", "Tokens", "Costo est.", "Acumulado", "Estado")
        filas = [_fila_tabla(r) for r in self.resultados]
        anchos = [max([len(c)] + [len(f[i]) for f in filas]) for i, c in enumerate(cabecera)]
        numericas = {1, 2, 3, 4, 5}

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
        """Distingue lo pagado en ESTA ejecución de lo acumulado por los videos listados (todas las ejecuciones)."""
        return (f"ESTIMACIÓN de costo de esta ejecución: {_describir_uso(self.uso_total)}; "
                f"acumulado de estos videos en todas las ejecuciones: {_describir_uso(self.uso_acumulado)} "
                "(precios de config, verificar en ai.google.dev)")

    def _linea_totales(self) -> str:
        omitidos = sum(1 for r in self.resultados if r.omitido)
        ok = sum(1 for r in self.resultados if r.exito and not r.omitido)
        return (f"Total: {len(self.resultados)} video(s): {ok} OK, {len(self.fallidos())} con error, "
                f"{omitidos} omitido(s); tiempo {_duracion_legible(self.segundos)}")


def _describir_uso(uso: Uso | None) -> str:
    if uso is None:
        return "US$ 0.0000 (sin API)"
    if uso.costo_usd is None:
        return (f"desconocida ({uso.tokens_total} tokens; el modelo {uso.modelo!r} no tiene precio en config: "
                "use --precio-entrada y --precio-salida)")
    return f"US$ {uso.costo_usd:.4f} ({uso.tokens_total} tokens{', batch' if uso.batch else ''})"


def _costo_corto(uso: Uso | None) -> str:
    if uso is None:
        return "-"
    return "?" if uso.costo_usd is None else f"US$ {uso.costo_usd:.4f}"


def _fila_tabla(r: ResultadoVideo) -> tuple:
    analisis = r.analisis
    uso = r.uso_ejecucion                   # lo pagado en ESTA ejecución (None: omitido, regenerado, local, simulado)
    nombre = r.info.nombre
    if len(nombre) > _ANCHO_NOMBRE_TABLA:
        nombre = nombre[:_ANCHO_NOMBRE_TABLA - 1] + "…"
    momentos = str(len(analisis.momentos)) if analisis is not None else "-"
    paginas = str(r.paginas_pdf) if isinstance(r.paginas_pdf, int) and r.paginas_pdf >= 0 else "-"
    tokens = str(uso.tokens_total) if uso is not None else "-"
    costo = _costo_corto(uso)
    acumulado = _costo_corto(r.uso_acumulado)
    if r.omitido:
        estado = "ya procesado (omitido)"
    elif not r.exito:
        estado = "ERROR: " + _una_linea(r.error or "desconocido")[:_ANCHO_ERROR_TABLA]
    elif analisis is None:
        estado = "pendiente (lote)"
    else:
        estado = "OK" + (" (respuesta cortada)" if analisis.truncado else "")
    return nombre, momentos, paginas, tokens, costo, acumulado, estado


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
    """Suma lo pagado a la API EN ESTA ejecución (``ResultadoVideo.uso_ejecucion``): los omitidos, los regenerados
    desde ``momentos.json`` y los fallidos no cuentan."""
    return _sumar_usos([r.uso_ejecucion for r in resultados if r.exito and not r.omitido and r.uso_ejecucion is not None],
                       modelo)


def sumar_acumulado(resultados: list, modelo: str) -> Uso | None:
    """Suma lo pagado por los videos listados en TODAS las ejecuciones (``ResultadoVideo.uso_acumulado``, según su
    ``momentos.json``), incluidos los omitidos y regenerados."""
    return _sumar_usos([r.uso_acumulado for r in resultados if r.uso_acumulado is not None], modelo)


def _sumar_usos(usos: list, modelo: str) -> Uso | None:
    if not usos:
        return None
    total = Uso(modelo=modelo, batch=all(u.batch for u in usos))
    for uso in usos:
        total.sumar(uso)
    if any(u.costo_usd is None for u in usos):
        total.costo_usd = None      # una suma parcial engañaría: mejor "desconocido"
    return total


def _acumular(previo: Uso | None, actual: Uso | None) -> Uso | None:
    """Uso acumulado de un video: lo registrado en su ``momentos.json`` más lo pagado ahora (None si nada)."""
    presentes = [u for u in (previo, actual) if u is not None]
    if not presentes:
        return None
    total = Uso(modelo=presentes[-1].modelo, batch=all(u.batch for u in presentes))
    for uso in presentes:
        total.sumar(uso)
    if any(u.costo_usd is None for u in presentes):
        total.costo_usd = None
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
        titulo_original=datos.get("titulo_original"),
        descripcion_original=datos.get("descripcion_original"),
        capitulo=datos.get("capitulo"),
        rotacion=int(datos.get("rotacion") or 0) if datos.get("rotacion") in (0, 90, 180, 270) else 0,
    )


def _uso_desde_dict(datos) -> Uso | None:
    if not isinstance(datos, dict):
        return None
    conocidos = {f.name for f in fields(Uso)}
    valores = {k: v for k, v in datos.items() if k in conocidos}
    valores.setdefault("modelo", "")
    return Uso(**valores)


def cargar_json(carpeta: Path) -> tuple[ResultadoAnalisis | None, dict]:
    """Lee el ``momentos.json`` de un video ya procesado: ``(análisis, documentos)``; ``(None, {})`` si no se puede.

    Los ``momentos_descartados`` por un filtro de una regeneración anterior vuelven a la lista completa (en orden
    cronológico): el análisis nunca se pierde por regenerar con ``--max-momentos`` / ``--importancia-minima``.
    """
    carpeta = Path(carpeta)
    try:
        datos = json.loads((carpeta / config.NOMBRE_JSON).read_text(encoding="utf-8"))
        a = datos["analisis"]
        momentos = [_momento_desde_dict(m, carpeta) for m in (a.get("momentos") or []) + (a.get("momentos_descartados") or [])]
        momentos.sort(key=lambda m: m.tiempo_seg)
        analisis = ResultadoAnalisis(
            momentos=momentos,
            modo=str(a.get("modo") or ""), modelo=str(a.get("modelo") or ""), resumen=a.get("resumen"),
            uso=_uso_desde_dict(a.get("uso")), truncado=bool(a.get("truncado")), avisos=list(a.get("avisos") or []),
            transcripcion=a.get("transcripcion"), tramos=int(a.get("tramos") or 1), titulo=a.get("titulo"))
        return analisis, dict(datos.get("documentos") or {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, {}


def _uso_acumulado_previo(carpeta: Path) -> Uso | None:
    """Uso acumulado que registra el ``momentos.json`` existente (clave ``uso_acumulado``; en JSON antiguos, el uso
    del análisis); None si no hay JSON o no tiene uso."""
    try:
        datos = json.loads((Path(carpeta) / config.NOMBRE_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(datos, dict):
        return None
    if isinstance(datos.get("uso_acumulado"), dict):
        return _uso_desde_dict(datos["uso_acumulado"])
    analisis = datos.get("analisis")
    return _uso_desde_dict(analisis.get("uso")) if isinstance(analisis, dict) else None


def _estado_salida(carpeta: Path) -> str | None:
    """None si no hay ``momentos.json``; "completo" si además existen el .docx y el .pdf registrados y no hay
    ``error.txt``; "incompleto" si el análisis se guardó pero una ejecución anterior falló antes de los documentos."""
    carpeta = Path(carpeta)
    if not (carpeta / config.NOMBRE_JSON).is_file():
        return None
    _, docs = cargar_json(carpeta)
    completo = (bool(docs.get("docx")) and bool(docs.get("pdf")) and (carpeta / str(docs["docx"])).is_file()
                and (carpeta / str(docs["pdf"])).is_file() and not (carpeta / config.NOMBRE_ERROR).exists())
    return "completo" if completo else "incompleto"


def _json_posterior_a(carpeta: Path, enviado_iso: str) -> bool:
    """True si el ``momentos.json`` de la carpeta se generó después del instante ISO dado (o no se puede saber)."""
    try:
        generado = str(json.loads((Path(carpeta) / config.NOMBRE_JSON).read_text(encoding="utf-8")).get("generado") or "")
    except (OSError, ValueError, AttributeError):
        return True
    if not generado or not enviado_iso:
        return True
    return generado >= enviado_iso


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
                      lupa: bool = True, log: Callable[[str], None] = print) -> None:
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
        anotar_momentos(momentos, lupa=lupa, log=log)


def rotar_zona(zona: dict | None, grados: int) -> dict | None:
    """La zona (punto o caja, coordenadas 0-1) tras girar la captura ``grados`` en sentido horario."""
    if not isinstance(zona, dict) or grados % 360 == 0:
        return zona

    def punto(x: float, y: float) -> tuple[float, float]:
        g = grados % 360
        if g == 90:
            return 1.0 - y, x
        if g == 180:
            return 1.0 - x, 1.0 - y
        return y, 1.0 - x          # 270

    if "caja" in zona and isinstance(zona["caja"], (list, tuple)) and len(zona["caja"]) == 4:
        x1, y1, x2, y2 = (float(v) for v in zona["caja"])
        (ax, ay), (bx, by) = punto(x1, y1), punto(x2, y2)
        return {"caja": [round(min(ax, bx), 4), round(min(ay, by), 4), round(max(ax, bx), 4), round(max(ay, by), 4)]}
    if "x" in zona and "y" in zona:
        nx, ny = punto(float(zona["x"]), float(zona["y"]))
        return {"x": round(nx, 4), "y": round(ny, 4)}
    return zona


def rotar_capturas(momentos: list, *, rotar_zona_tambien: bool, log: Callable[[str], None] = print) -> int:
    """Gira en sitio la captura de cada momento con ``rotacion`` (grados en sentido horario) y devuelve cuántas.

    ``rotar_zona_tambien`` True cuando la zona se refiere a la captura sin girar (recién devuelta por el refinado);
    False al regenerar desde ``momentos.json``, donde la zona ya está guardada girada.  Un fallo al girar deja la
    captura como está y avisa.
    """
    from PIL import Image   # import perezoso

    giradas = 0
    for momento in momentos:
        grados = int(momento.rotacion or 0) % 360
        if not grados or not momento.ruta_captura:
            continue
        try:
            with Image.open(momento.ruta_captura) as imagen:
                girada = imagen.convert("RGB").rotate(-grados, expand=True)   # Pillow gira en sentido antihorario
                girada.save(momento.ruta_captura, "JPEG", quality=95)
        except (OSError, ValueError) as exc:
            log(f"  aviso: no se pudo girar la captura {Path(momento.ruta_captura).name} ({exc}); queda como estaba")
            continue
        if rotar_zona_tambien:
            momento.zona = rotar_zona(momento.zona, grados)
        giradas += 1
    if giradas:
        log(f"Capturas enderezadas: {giradas} (giradas según lo indicado por el refinado).")
    return giradas


def anotar_momentos(momentos: list, *, lupa: bool = True, log: Callable[[str], None] = print) -> int:
    """Dibuja la zona señalada sobre las capturas ya extraídas (``NN_mm-ss_anotada.jpg``); devuelve cuántas.

    Con ``lupa`` cada captura anotada lleva además un recuadro con la zona señalada ampliada."""
    anotadas = 0
    for momento in momentos:
        momento.ruta_captura_anotada = None
        if not (momento.ruta_captura and momento.zona):
            continue
        origen = Path(momento.ruta_captura)
        destino = origen.with_name(origen.stem + "_anotada.jpg")
        if anotar_captura(origen, momento.zona, destino, lupa=lupa, log=log) is not None:
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
        return gemini.subir_video(cliente, ruta_subida, info.nombre, timeout_procesado=op.timeout_procesado,
                                  conservar_subida=op.conservar_subida, log=log)
    finally:
        shutil.rmtree(temporal, ignore_errors=True)


# ----------------------------------------------------------------------------
# Análisis, refinado y documentos de un video
# ----------------------------------------------------------------------------
def _analizar_con_gemini(info: InfoVideo, op: Opciones, cliente, ffmpeg: str,
                         log: Callable[[str], None]) -> ResultadoAnalisis:
    """Subida → ``analizar_video`` → borrado del archivo remoto (salvo ``conservar_subida``; el ``finally`` cubre
    también Ctrl+C).  El refinado y el redactor van después de las capturas (``_finalizar_video``)."""
    precios = precios_de(op)
    archivo = _subir(info, op, cliente, ffmpeg, log)
    try:
        analisis = gemini.analizar_video(
            cliente, archivo, info, modelo=op.modelo, fps=op.fps, tramo_max_seg=op.tramo_min * 60,
            precios=precios, equipo=op.equipo, resolucion=op.resolucion, max_momentos=op.max_momentos,
            importancia_minima=op.importancia_minima, temperatura=op.temperatura, log=log)
        if op.transcribir:
            analisis = _transcribir(cliente, archivo, info, analisis, op, log)
        return analisis
    finally:
        if op.conservar_subida:
            log(f"Archivo remoto conservado (--conservar-subida): {archivo.name}")
        else:
            gemini.eliminar_archivo(cliente, archivo, log=log)


def _transcribir(cliente, archivo, info: InfoVideo, analisis: ResultadoAnalisis, op: Opciones,
                 log: Callable[[str], None]) -> ResultadoAnalisis:
    """Transcripción literal del audio con el archivo ya subido; un fallo deja un aviso y no tira el análisis."""
    modelo = _modelo_refinado(analisis, op)
    try:
        segmentos, uso = gemini.transcribir_video(cliente, archivo, info, modelo, precios_de(op), equipo=op.equipo,
                                                  tramo_max_seg=op.tramo_min * 60, temperatura=op.temperatura, log=log)
    except Exception as exc:  # noqa: BLE001 - opcional: nunca debe tirar un análisis ya pagado
        log(f"Aviso: la transcripción falló y el manual sale sin ella ({exc}).")
        analisis.avisos.append(f"Transcripción omitida: {exc}")
        uso_fallido = getattr(exc, "uso", None)
        if isinstance(uso_fallido, Uso) and uso_fallido.llamadas:     # lo pagado se contabiliza igual
            etiqueta = analisis.uso.modelo if analisis.uso is not None else uso_fallido.modelo
            analisis.uso = _acumular(analisis.uso, uso_fallido)
            analisis.uso.modelo = etiqueta
        return analisis
    analisis.transcripcion = segmentos
    etiqueta = analisis.uso.modelo if analisis.uso is not None else uso.modelo
    analisis.uso = _acumular(analisis.uso, uso)
    analisis.uso.modelo = etiqueta          # la etiqueta del análisis manda (la transcripción no la cambia)
    return analisis


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
    redactor ``a+b``) si el análisis viene de Gemini, y si no el de la CLI."""
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
        return gemini.refinar_con_capturas(cliente, analisis, modelo, precios_de(op), equipo=op.equipo,
                                           temperatura=op.temperatura, log=log)
    except Exception as exc:  # noqa: BLE001 - el refinado es opcional: nunca debe tirar un análisis ya pagado
        log(f"Aviso: el refinado falló y se conserva el análisis original ({exc}).")
        analisis.avisos.append(f"Refinado con capturas omitido: {exc}")
        return analisis


def _orientar(cliente, analisis: ResultadoAnalisis, op: Opciones, log: Callable[[str], None]) -> ResultadoAnalisis:
    """Confirma la dirección del giro de las capturas marcadas como giradas; un fallo conserva lo propuesto."""
    if not any(m.rotacion for m in analisis.momentos):
        return analisis
    try:
        return gemini.orientar_capturas(cliente, analisis, _modelo_refinado(analisis, op), precios_de(op),
                                        temperatura=op.temperatura, log=log)
    except Exception as exc:  # noqa: BLE001 - opcional
        log(f"Aviso: no se pudo confirmar la dirección del giro ({exc}); se conservan las rotaciones propuestas.")
        analisis.avisos.append(f"Orientación omitida: {exc}")
        return analisis


def _pulir(cliente, analisis: ResultadoAnalisis, op: Opciones, log: Callable[[str], None]) -> ResultadoAnalisis:
    """Redactor opcional (``--redactor``), DESPUÉS del refinado para que este no deshaga su trabajo."""
    try:
        return gemini.pulir_redaccion(cliente, analisis, op.redactor, precios_de(op), temperatura=op.temperatura, log=log)
    except Exception as exc:  # noqa: BLE001 - opcional: nunca debe tirar un análisis ya pagado
        log(f"Aviso: el redactor falló y se conserva el texto anterior ({exc}).")
        analisis.avisos.append(f"Redactor omitido: {exc}")
        return analisis


def _finalizar_video(info: InfoVideo, carpeta: Path, analisis: ResultadoAnalisis, op: Opciones, cliente,
                     ffmpeg: str, log: Callable[[str], None], *, uso_previo: Uso | None = None,
                     con_api: bool = True) -> tuple[ResultadoAnalisis, Path, Path, int, Uso | None]:
    """Del análisis a los documentos: JSON → capturas → refinado → redactor → JSON → anotaciones → docx/pdf → JSON.

    ``con_api`` False (``--regenerar`` o retomar desde el JSON): no se llama a la API (ni refinado ni redactor) y
    el uso acumulado del video no cambia.  Devuelve ``(análisis, docx, pdf, páginas, uso acumulado)``; el uso
    acumulado (``uso_previo`` + lo pagado ahora) se guarda en el JSON bajo ``uso_acumulado``.
    """
    def acumulado_de(a: ResultadoAnalisis) -> Uso | None:
        return _acumular(uso_previo, a.uso) if con_api else uso_previo

    def guardar(a: ResultadoAnalisis, **extra) -> None:
        acumulado = acumulado_de(a)
        guardar_json(carpeta, info, a, extra={"uso_acumulado": None if acumulado is None else acumulado.a_dict(), **extra})

    guardar(analisis)
    log(f"{config.NOMBRE_JSON} guardado ({len(analisis.momentos)} momentos).")
    if not analisis.momentos:
        raise RuntimeError("El análisis no encontró ningún momento: no hay nada que documentar "
                           "(revise el video o los filtros --max-momentos / --importancia-minima).")
    capturar_momentos(info, analisis.momentos, carpeta, ffmpeg, anotar=False, log=log)
    if con_api and cliente is not None:
        if op.refinar:
            analisis = _refinar(cliente, analisis, op, log)
            analisis = _orientar(cliente, analisis, op, log)
            rotar_capturas(analisis.momentos, rotar_zona_tambien=True, log=log)
        if op.redactor:
            analisis = _pulir(cliente, analisis, op, log)
        if op.refinar or op.redactor:
            guardar(analisis)
    else:
        if con_api and op.refinar and analisis.modo.startswith("gemini"):
            log("Refinado con capturas omitido: no hay cliente de Gemini (sin clave).")
        rotar_capturas(analisis.momentos, rotar_zona_tambien=False, log=log)   # rotación guardada en el JSON
    if op.anotar:
        anotar_momentos(analisis.momentos, lupa=op.lupa, log=log)
    docx, pdf, paginas = documentos.generar_documentos(
        info.nombre, analisis.momentos, carpeta, titulo=analisis.titulo, resumen=analisis.resumen,
        duracion=info.duracion, modo=analisis.modo, modelo=analisis.modelo, por_pagina=op.por_pagina,
        incluir_indice=op.incluir_indice, transcripcion=analisis.transcripcion, log=log)
    guardar(analisis, documentos={"docx": docx.name, "pdf": pdf.name, "paginas": paginas})
    (Path(carpeta) / config.NOMBRE_ERROR).unlink(missing_ok=True)   # un error de una ejecución anterior ya no aplica
    return analisis, docx, pdf, paginas, acumulado_de(analisis)


def _log_cabecera(info: InfoVideo, carpeta: Path, log: Callable[[str], None]) -> None:
    log(f"=== {info.ruta.name}: {formatear_tiempo(info.duracion)}, {info.ancho}x{info.alto}, {info.fps:g} fps, "
        f"{info.tamano_bytes / _MB:.0f} MB -> {carpeta}")


def _resultado_omitido(info: InfoVideo, carpeta: Path, inicio: float, log: Callable[[str], None]) -> ResultadoVideo:
    """Video ya procesado (JSON y documentos completos): se carga lo que hay en ``momentos.json`` sin volver a analizar."""
    analisis, docs = cargar_json(carpeta)
    log(f"Ya procesado ({config.NOMBRE_JSON} y documentos existen): se omite. Use --forzar para repetir el análisis "
        "o --regenerar para rehacer capturas y documentos sin volver a analizar.")
    paginas = docs.get("paginas")
    return ResultadoVideo(info=info, carpeta_salida=carpeta, exito=True, analisis=analisis,
                          ruta_docx=carpeta / docs["docx"] if docs.get("docx") else None,
                          ruta_pdf=carpeta / docs["pdf"] if docs.get("pdf") else None,
                          paginas_pdf=paginas if isinstance(paginas, int) else None,
                          omitido=True, segundos=time.monotonic() - inicio, uso_acumulado=_uso_acumulado_previo(carpeta))


def _resultado_listo(info: InfoVideo, carpeta: Path, analisis: ResultadoAnalisis, docx: Path, pdf: Path, paginas: int,
                     acumulado: Uso | None, inicio: float, con_api: bool, log: Callable[[str], None]) -> ResultadoVideo:
    segundos = time.monotonic() - inicio
    log(f"Listo en {_duracion_legible(segundos)}: {len(analisis.momentos)} momentos, "
        f"{paginas if paginas >= 0 else '?'} páginas -> {docx.name}, {pdf.name}")
    return ResultadoVideo(info=info, carpeta_salida=carpeta, exito=True, analisis=analisis, ruta_docx=docx,
                          ruta_pdf=pdf, paginas_pdf=paginas, segundos=segundos,
                          uso_ejecucion=analisis.uso if con_api else None, uso_acumulado=acumulado)


def _analisis_desde_json(carpeta: Path, op: Opciones, log: Callable[[str], None], *, retomar: bool) -> ResultadoAnalisis:
    """Carga ``momentos.json`` para regenerar (o retomar) sin API y aplica los filtros de la CLI.

    Los momentos apartados por ``--max-momentos`` / ``--importancia-minima`` van a ``analisis.descartados`` y se
    conservan en el JSON (``momentos_descartados``): una regeneración posterior sin filtros los recupera.
    """
    analisis, _ = cargar_json(carpeta)
    if analisis is None:
        raise RuntimeError(f"No se pudo leer {carpeta / config.NOMBRE_JSON}: revise que sea un JSON válido "
                           "con la estructura original (clave 'analisis' con su lista 'momentos').")
    etiqueta = "Retomado" if retomar else "Regenerado"
    log(f"{'Se retoma' if retomar else '--regenerar: se reutiliza'} el análisis de {config.NOMBRE_JSON} "
        f"({len(analisis.momentos)} momentos, modo {analisis.modo or '?'}); no se vuelve a analizar el video "
        "ni se llama a la API.")
    filtrados, avisos = gemini.filtrar_momentos(analisis.momentos, max_momentos=op.max_momentos,
                                                importancia_minima=op.importancia_minima)
    if len(filtrados) < len(analisis.momentos):
        conservados = {id(m) for m in filtrados}
        analisis.descartados = [m for m in analisis.momentos if id(m) not in conservados]
        for m in analisis.descartados:       # sus capturas se borran al extraer las nuevas
            m.ruta_captura = m.ruta_captura_anotada = None
        analisis.momentos = filtrados
        for aviso in avisos:
            log("Aviso: " + aviso)
        avisos.append(f"{len(analisis.descartados)} momento(s) apartados por los filtros quedan en momentos_descartados "
                      "(se recuperan regenerando sin filtros).")
    analisis.avisos.extend(avisos)
    analisis.avisos.append(f"{etiqueta} desde {config.NOMBRE_JSON} el {datetime.now():%Y-%m-%d %H:%M}")
    return analisis


def _desde_json(info: InfoVideo, carpeta: Path, op: Opciones, ffmpeg: str, inicio: float,
                log: Callable[[str], None], *, retomar: bool, cliente=None) -> ResultadoVideo:
    """Capturas, anotaciones y documentos a partir de ``momentos.json``, sin llamar a la API.

    Con ``cliente`` (``--solo-refinado``) se repite además la pasada de refinado con las capturas (barata: solo
    imágenes), que corrige textos, zonas y la rotación de las capturas; el análisis del video no se repite.
    """
    if retomar:
        log(f"{config.NOMBRE_JSON} existe pero faltan los documentos (una ejecución anterior falló a medias): "
            "se retoma desde el JSON sin volver a analizar (use --forzar para repetir el análisis).")
    analisis = _analisis_desde_json(carpeta, op, log, retomar=retomar)
    con_api = cliente is not None
    if con_api:
        log("--solo-refinado: se repite solo la pasada de refinado con las capturas (el análisis del video se reutiliza).")
        for m in analisis.momentos:
            m.rotacion = 0            # las capturas se vuelven a extraer sin girar; el refinado decide de nuevo
        analisis.uso = None           # lo ya pagado está en uso_acumulado; esta ejecución solo paga el refinado
    analisis, docx, pdf, paginas, acumulado = _finalizar_video(info, carpeta, analisis, op, cliente, ffmpeg, log,
                                                               uso_previo=_uso_acumulado_previo(carpeta), con_api=con_api)
    return _resultado_listo(info, carpeta, analisis, docx, pdf, paginas, acumulado, inicio, con_api, log)


def procesar_video(ruta: Path, op: Opciones, cliente=None, *, log: Callable[[str], None] = print) -> ResultadoVideo:
    """Procesa un video completo hasta sus documentos.  Nunca lanza: un fallo queda en ``error.txt``."""
    inicio = time.monotonic()
    consola = log
    info = _info_minima(ruta)
    carpeta = _carpeta_para(info, op)
    try:
        ffmpeg = video.localizar_ffmpeg(op.ffmpeg)
        info = video.obtener_info(ruta, ffmpeg, video.localizar_ffprobe(op.ffprobe, ffmpeg=ffmpeg))
        carpeta = preparar_carpeta_salida(info, op)
        log = crear_log(carpeta, consola)
        _log_cabecera(info, carpeta, log)
        estado = _estado_salida(carpeta)
        if op.regenerar or op.solo_refinado:
            opcion = "--solo-refinado" if op.solo_refinado else "--regenerar"
            if estado is None:
                raise RuntimeError(f"{opcion}: no existe {carpeta / config.NOMBRE_JSON}; ejecute sin {opcion} "
                                   "para analizar el video.")
            if op.solo_refinado and cliente is None:
                raise RuntimeError("--solo-refinado necesita la clave de Gemini (GEMINI_API_KEY) para la pasada de refinado.")
            return _desde_json(info, carpeta, op, ffmpeg, inicio, log, retomar=False,
                               cliente=cliente if op.solo_refinado else None)
        if estado == "completo" and not op.forzar:
            return _resultado_omitido(info, carpeta, inicio, log)
        if estado == "incompleto" and not op.forzar:
            return _desde_json(info, carpeta, op, ffmpeg, inicio, log, retomar=True)
        uso_previo = _uso_acumulado_previo(carpeta) if estado else None      # --forzar: se acumula lo ya pagado
        analisis = _analizar(info, op, cliente, ffmpeg, log)
        analisis, docx, pdf, paginas, acumulado = _finalizar_video(info, carpeta, analisis, op, cliente, ffmpeg, log,
                                                                   uso_previo=uso_previo, con_api=True)
        return _resultado_listo(info, carpeta, analisis, docx, pdf, paginas, acumulado, inicio, True, log)
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
    """Cliente de Gemini para los modos con API; None en local/simulado y SIEMPRE con ``--regenerar`` (que no
    llama a la API: ni análisis, ni refinado, ni redactor)."""
    if op.modo not in ("gemini", "batch"):
        return None
    if op.regenerar:
        log(f"--regenerar: no se llama a la API de Gemini (se reutiliza {config.NOMBRE_JSON}: sin análisis, "
            "sin refinado ni redactor).")
        return None
    api_key = op.api_key or gemini.obtener_api_key()
    if not api_key:
        raise RuntimeError("Falta la clave de API de Gemini: cree un archivo .env con GEMINI_API_KEY=... "
                           "(ver .env.ejemplo) o use --local / --simular.")
    return gemini.crear_cliente(api_key)


def procesar_carpeta(op: Opciones, *, log: Callable[[str], None] = print) -> ResumenEjecucion:
    """Procesa todos los videos de la carpeta según ``op.modo`` y devuelve el resumen (tabla y costo)."""
    inicio = time.monotonic()
    if op.modo not in MODOS:
        raise ValueError(f"Modo desconocido: {op.modo!r} (use {', '.join(MODOS)}).")
    if op.regenerar and op.forzar:
        raise ValueError("--forzar y --regenerar son incompatibles: --forzar vuelve a analizar el video (API) y "
                         f"--regenerar reutiliza {config.NOMBRE_JSON} sin llamar a la API. Use solo una de las dos.")
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
                            segundos=time.monotonic() - inicio, uso_acumulado=sumar_acumulado(resultados, op.modelo))


# ----------------------------------------------------------------------------
# Modo batch: envío y recogida
# ----------------------------------------------------------------------------
NOTA_ESCALERA_LOTE = ("El modo batch no tiene la escalera de fallbacks del modo síncrono: si el modelo rechaza una "
                      "opción (thinking, media_resolution, esquema JSON) el rechazo llega al recoger el lote, en todas "
                      "sus respuestas; en ese caso el lote se reenvía automáticamente sin esa opción (máximo "
                      f"{gemini.MAX_REINTENTOS_LOTE} reenvíos) y queda anotado aquí.")
ESTADOS_LOTE_FALLIDO = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")


def _id_lote(nombre_job: str) -> str:
    """Identificador corto y seguro para el nombre de archivo (``batches/abc-123`` → ``abc-123``)."""
    cola = re.sub(r"[^A-Za-z0-9_-]+", "", str(nombre_job or "").rsplit("/", 1)[-1])
    return cola or f"lote-{datetime.now():%Y%m%d-%H%M%S}"


def _estado_lote_legible(job) -> str:
    estado = getattr(job, "state", None)
    return getattr(estado, "name", str(estado)) if estado is not None else "desconocido"


def _carpeta_lote(info: InfoVideo, op: Opciones, ocupadas: dict) -> Path:
    """Como ``preparar_carpeta_salida``, y además evita que dos videos del mismo lote con el mismo nombre base
    (``IMG_0001.mp4`` e ``IMG_0001.mov``) compartan carpeta y clave: en batch aún no hay ``momentos.json`` que
    delate la colisión.  ``ocupadas``: carpeta → ruta del video que la usa en este lote."""
    carpeta = preparar_carpeta_salida(info, op)
    ocupante = ocupadas.get(carpeta)
    if ocupante is not None and ocupante != info.ruta:
        carpeta = _carpeta_para(info, op, con_sufijo=True)
        carpeta.mkdir(parents=True, exist_ok=True)
    ocupadas[carpeta] = info.ruta
    return carpeta


def _borrar_subidas(cliente, entradas: list, op: Opciones, log: Callable[[str], None]) -> None:
    """Borra los archivos remotos de los videos ya subidos a un lote que no llegó a crearse (salvo --conservar-subida)."""
    for entrada in entradas:
        if op.conservar_subida:
            log(f"Archivo remoto conservado (--conservar-subida): {entrada['archivo']}")
        else:
            gemini.eliminar_archivo(cliente, entrada["archivo"], log=log)


def _registrar_lote(op: Opciones, job, entradas: list, opciones_lote, log: Callable[[str], None], *,
                    modelo: str, opciones: dict, reenvio_de: str | None = None) -> Path:
    """Escribe ``salida/_lotes/<id>.json`` (job, modelo, opciones, escalera y videos) y explica cómo recogerlo."""
    id_corto = _id_lote(job.name)
    carpeta_lotes = Path(op.carpeta_salida) / config.CARPETA_LOTES
    carpeta_lotes.mkdir(parents=True, exist_ok=True)
    datos = {"id": id_corto, "job": job.name, "estado": _estado_lote_legible(job), "modelo": modelo,
             "enviado": datetime.now().isoformat(timespec="seconds"), "version": __version__,
             "carpeta_salida": str(Path(op.carpeta_salida).resolve()),
             "opciones": opciones,
             "escalera": {"nota": NOTA_ESCALERA_LOTE, **opciones_lote.a_dict()},
             "videos": entradas}
    if reenvio_de:
        datos["reenvio_de"] = reenvio_de
    ruta_lote = carpeta_lotes / f"{id_corto}.json"
    _escribir_json(ruta_lote, datos)
    peticiones = sum(len(e.get("tramos") or []) or 1 for e in entradas)
    log(f"Lote {id_corto} enviado con {peticiones} petición(es) de {len(entradas)} video(s); "
        f"registro en {ruta_lote}. Puede tardar minutos u horas.")
    log(f"Para recoger los resultados: python resumir_videos.py --batch-recoger {id_corto} [--esperar]")
    return ruta_lote


def _enviar_lote(videos: list, op: Opciones, cliente, log: Callable[[str], None]) -> list:
    """Sube cada video (copia ligera si toca), envía el lote y guarda ``salida/_lotes/<id>.json``.

    Si algo interrumpe el envío (Ctrl+C, fallo al crear el lote) se borran los archivos ya subidos (salvo
    ``--conservar-subida``): son videos clínicos y no deben quedar en Google sin un lote que los use.
    """
    ffmpeg = video.localizar_ffmpeg(op.ffmpeg)
    ffprobe = video.localizar_ffprobe(op.ffprobe, ffmpeg=ffmpeg)
    resultados, peticiones, entradas = [], [], []
    ocupadas: dict = {}
    try:
        for i, ruta in enumerate(videos, 1):
            inicio = time.monotonic()
            log(f"[{i}/{len(videos)}] {ruta.name}")
            info = _info_minima(ruta)
            carpeta = _carpeta_para(info, op)
            log_v = log
            try:
                info = video.obtener_info(ruta, ffmpeg, ffprobe)
                carpeta = _carpeta_lote(info, op, ocupadas)
                log_v = crear_log(carpeta, log)
                _log_cabecera(info, carpeta, log_v)
                estado = _estado_salida(carpeta)
                if estado == "completo" and not op.forzar:
                    resultados.append(_resultado_omitido(info, carpeta, inicio, log_v))
                    continue
                if estado == "incompleto" and not op.forzar:
                    resultados.append(_desde_json(info, carpeta, op, ffmpeg, inicio, log_v, retomar=True))
                    continue
                archivo = _subir(info, op, cliente, ffmpeg, log_v)
                tramos = gemini.calcular_tramos(info.duracion, op.tramo_min * 60)
                clave = carpeta.name        # única dentro del lote (P-H9); info.nombre puede repetirse
                peticiones.extend({"archivo": archivo, "info": info, "clave": clave,
                                   "tramo": t if len(tramos) > 1 else None} for t in tramos)
                entradas.append({"nombre": clave, "ruta": str(info.ruta), "archivo": archivo.name,
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
    except BaseException:       # Ctrl+C (u otro fallo no controlado) a mitad de las subidas
        log("Envío del lote interrumpido: se borran los archivos ya subidos.")
        _borrar_subidas(cliente, entradas, op, log)
        raise
    if not peticiones:
        log("No hay videos que enviar al lote.")
        return resultados

    nombre_lote = f"resumen_videos {datetime.now():%Y-%m-%d %H:%M}"
    opciones_lote = gemini.OpcionesLote()
    try:
        job = gemini.enviar_lote(cliente, peticiones, op.modelo, nombre_lote, equipo=op.equipo, fps=op.fps,
                                 resolucion=op.resolucion, temperatura=op.temperatura, opciones_lote=opciones_lote,
                                 log=log)
    except BaseException as exc:  # noqa: BLE001 - el lote no se creó: los videos subidos quedan sin análisis
        _borrar_subidas(cliente, entradas, op, log)
        if not isinstance(exc, Exception):
            raise
        pendientes = {e["nombre"] for e in entradas}
        for r in resultados:
            if r.exito and not r.omitido and r.carpeta_salida.name in pendientes:
                r.exito = False
                r.error = registrar_error(r.carpeta_salida, exc, r.info, op.verbose, crear_log(r.carpeta_salida, log))
        return resultados
    _registrar_lote(op, job, entradas, opciones_lote, log, modelo=op.modelo,
                    opciones={"equipo": op.equipo, "fps": op.fps, "resolucion": op.resolucion, "tramo_min": op.tramo_min,
                              "max_momentos": op.max_momentos, "importancia_minima": op.importancia_minima})
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


def _reenviar_lote(op: Opciones, cliente, ruta_lote: Path, datos: dict, accion: str, opciones_lote, infos: dict,
                   log: Callable[[str], None]) -> list | None:
    """El modelo rechazó una opción en todas las peticiones: se reenvía el lote sin ella (mismos archivos remotos).

    Devuelve los resultados "pendiente (lote)" del lote nuevo, o None si no se pudo reenviar (entonces se
    registran los errores del lote como siempre).
    """
    if not opciones_lote.aplicar(accion):
        return None
    opciones_lote.reintentos += 1
    aviso = (f"El modelo rechazó una opción en todas las peticiones del lote; se reenvía automáticamente "
             f"{accion.replace('_', ' ')} (reenvío {opciones_lote.reintentos}/{gemini.MAX_REINTENTOS_LOTE}).")
    opciones_lote.avisos.append(aviso)
    log("Aviso: " + aviso)
    peticiones, entradas = [], []
    for v in datos.get("videos") or []:
        try:
            archivo = gemini.obtener_archivo(cliente, v["archivo"])
        except Exception as exc:  # noqa: BLE001 - archivo caducado o borrado: ese video se resube con --batch
            log(f"No se pudo recuperar el archivo remoto {v['archivo']} de {v['nombre']} ({exc}): no se reenvía; "
                "vuelva a ejecutar --batch para subirlo de nuevo.")
            continue
        tramos = [tuple(t) for t in v.get("tramos") or []] or [None]
        peticiones.extend({"archivo": archivo, "info": infos[v["nombre"]], "clave": v["nombre"], "tramo": t} for t in tramos)
        entradas.append(dict(v))
    if not peticiones:
        return None
    opciones = dict(datos.get("opciones") or {})
    modelo = str(datos.get("modelo") or op.modelo)
    try:
        job = gemini.enviar_lote(cliente, peticiones, modelo, f"resumen_videos {datetime.now():%Y-%m-%d %H:%M} (reenvío)",
                                 equipo=opciones.get("equipo") or op.equipo, fps=opciones.get("fps"),
                                 resolucion=opciones.get("resolucion") or op.resolucion, temperatura=op.temperatura,
                                 opciones_lote=opciones_lote, log=log)
    except Exception as exc:  # noqa: BLE001 - no se pudo reenviar: se informan los errores originales
        log(f"Aviso: el reenvío automático del lote falló ({exc}); se registran los errores del lote original.")
        return None
    ruta_nueva = _registrar_lote(op, job, entradas, opciones_lote, log, modelo=modelo, opciones=opciones,
                                 reenvio_de=str(datos.get("id") or ruta_lote.stem))
    datos["recogido"] = datetime.now().isoformat(timespec="seconds")
    datos["reenviado_como"] = ruta_nueva.stem
    _escribir_json(ruta_lote, datos)
    return [ResultadoVideo(info=infos[e["nombre"]], carpeta_salida=Path(e["carpeta"]), exito=True) for e in entradas]


def _recoger_lote(op: Opciones, log: Callable[[str], None]) -> list:
    """Lee ``salida/_lotes/<id>.json``, espera (si ``esperar_lote``) y termina cada video desde el análisis.

    Un video que ya tiene sus documentos (generados después del envío del lote, p. ej. en una recogida anterior)
    se omite salvo ``--forzar``: recoger dos veces no repite el refinado ni pisa correcciones a mano.
    """
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
    ya_recogido = bool(datos.get("recogido"))
    if ya_recogido:
        log(f"Aviso: este lote ya se recogió el {datos['recogido']}: los videos que ya tienen sus documentos se "
            "omiten (use --forzar para rehacerlos).")
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
    estado_final = _estado_lote_legible(job)
    log(f"Lote terminado con estado {estado_final}.")
    if estado_final in ESTADOS_LOTE_FALLIDO:
        log(f"AVISO: el lote terminó con estado {estado_final} (falló, se canceló o expiró sin recogerse a tiempo): "
            f"sus videos quedan sin análisis. Vuelva a ejecutar --batch: se reenviarán solo los videos que sigan "
            f"sin {config.NOMBRE_JSON}.")

    opciones = dict(datos.get("opciones") or {})
    max_momentos = op.max_momentos if op.max_momentos is not None else opciones.get("max_momentos")
    importancia_minima = op.importancia_minima if op.importancia_minima > 1 else int(opciones.get("importancia_minima") or 1)
    mapa = {v["nombre"]: {"info": infos[v["nombre"]], "tramos": [tuple(t) for t in v.get("tramos") or []] or None,
                          "modelo": datos.get("modelo"), "max_momentos": max_momentos,
                          "importancia_minima": importancia_minima} for v in videos}
    analisis_por_video = gemini.recoger_lote(cliente, job, mapa, precios_de(op), log=log)
    opciones_lote = gemini.OpcionesLote.desde_dict(datos.get("escalera"))
    accion = gemini.opcion_rechazada_en_lote(analisis_por_video, opciones_lote, str(datos.get("modelo") or op.modelo),
                                             str(opciones.get("resolucion") or op.resolucion))
    if accion and opciones_lote.reintentos < gemini.MAX_REINTENTOS_LOTE:
        reenviados = _reenviar_lote(op, cliente, ruta_lote, datos, accion, opciones_lote, infos, log)
        if reenviados is not None:
            return reenviados
    ffmpeg = video.localizar_ffmpeg(op.ffmpeg)
    resultados = []
    enviado = str(datos.get("enviado") or "")
    for v in videos:
        inicio = time.monotonic()
        info = infos[v["nombre"]]
        carpeta = Path(v["carpeta"])
        carpeta.mkdir(parents=True, exist_ok=True)
        log_v = crear_log(carpeta, log)
        try:
            estado = _estado_salida(carpeta)
            if estado and not op.forzar and _json_posterior_a(carpeta, enviado):
                if estado == "completo":
                    resultados.append(_resultado_omitido(info, carpeta, inicio, log_v))
                else:
                    resultados.append(_desde_json(info, carpeta, op, ffmpeg, inicio, log_v, retomar=True))
                continue
            resultado = analisis_por_video.get(v["nombre"])
            if resultado is None:
                raise RuntimeError("El lote no contiene ninguna respuesta para este video.")
            if isinstance(resultado, Exception):
                raise resultado
            analisis, docx, pdf, paginas, acumulado = _finalizar_video(
                info, carpeta, resultado, op, cliente, ffmpeg, log_v,
                uso_previo=_uso_acumulado_previo(carpeta) if estado else None, con_api=True)
            resultados.append(_resultado_listo(info, carpeta, analisis, docx, pdf, paginas, acumulado, inicio, True, log_v))
        except Exception as exc:  # noqa: BLE001 - se registra y se sigue con el siguiente video del lote
            resultados.append(ResultadoVideo(info=info, carpeta_salida=carpeta, exito=False,
                                             error=registrar_error(carpeta, exc, info, op.verbose, log_v),
                                             segundos=time.monotonic() - inicio))
        finally:
            # en la primera recogida se borran los archivos remotos de todos los videos del lote (salvo
            # --conservar-subida); en una recogida repetida ya no existen
            if v.get("archivo") and not op.conservar_subida and not ya_recogido:
                gemini.eliminar_archivo(cliente, v["archivo"], log=log_v)
    datos["recogido"] = datetime.now().isoformat(timespec="seconds")
    datos["estado"] = estado_final
    _escribir_json(ruta_lote, datos)
    return resultados
