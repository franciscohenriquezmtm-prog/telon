#!/usr/bin/env python3
"""resumir_videos: convierte videos de capacitación en manuales con capturas (.docx + .pdf).

Único punto de entrada.  Procesa todos los videos de una carpeta (``videos/`` por
defecto) y deja los resultados en ``salida/<nombre>/``.  Ver ``--help``.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from resumen_videos import __version__, config, documentos, gemini, pipeline, video  # noqa: E402

EPILOGO = """\
Ejemplos:
  python resumir_videos.py
      Analiza con Gemini todos los videos de videos\\ y deja los manuales en salida\\.
  python resumir_videos.py D:\\grabaciones --equipo "arco en C" --salida D:\\manuales
      Otra carpeta de entrada y de salida; el prompt se adapta al equipo indicado.
  python resumir_videos.py --simular
      Prueba todo el circuito (capturas, docx, pdf) sin usar la API ni gastar nada.
  python resumir_videos.py --batch          y luego     python resumir_videos.py --batch-recoger --esperar
      Modo batch: mitad de precio; los resultados llegan minutos u horas después.
  python resumir_videos.py --regenerar --solo "arco en C parte 1"
      Rehace capturas y documentos a partir de salida\\<video>\\momentos.json (corregido a mano) sin volver a analizar.

Variables de entorno (.env): GEMINI_API_KEY (o GOOGLE_API_KEY), GEMINI_MODELO, FFMPEG_BIN, FFPROBE_BIN, WHISPER_CACHE.
"""


def _cargar_env() -> None:
    """Carga ``.env`` desde el directorio actual (o sus padres) y desde la carpeta del script."""
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    load_dotenv(find_dotenv(usecwd=True))
    load_dotenv(RAIZ / ".env")


def _importancia(valor: str) -> int:
    n = int(valor)
    if not 1 <= n <= 5:
        raise argparse.ArgumentTypeError("la importancia mínima va de 1 a 5")
    return n


def _por_pagina(valor: str):
    try:
        return documentos.normalizar_por_pagina(valor)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _no_negativo(valor: str) -> float:
    n = float(valor)
    if n < 0:
        raise argparse.ArgumentTypeError("debe ser un número no negativo")
    return n


def crear_parser() -> argparse.ArgumentParser:
    """Define todas las opciones de la línea de comandos (§7 de la especificación)."""
    p = argparse.ArgumentParser(
        prog="resumir_videos.py",
        description="Convierte videos de capacitación (p. ej. el uso de un arco en C grabado con el iPhone) en "
                    "manuales sencillos con capturas: un .docx editable y un .pdf por video.  La cantidad de "
                    "capturas la decide el contenido del video, no un rango fijo.",
        epilog=EPILOGO, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("carpeta", nargs="?", default=config.CARPETA_VIDEOS,
                   help=f"carpeta con los videos (por defecto: {config.CARPETA_VIDEOS}/)")
    p.add_argument("--salida", default=config.CARPETA_SALIDA, metavar="DIR",
                   help=f"carpeta de salida (por defecto: {config.CARPETA_SALIDA}/)")
    p.add_argument("--version", action="version", version=f"resumen_videos {__version__}")
    p.add_argument("--verbose", action="store_true", help="muestra las trazas técnicas de los errores en consola")

    g = p.add_argument_group("análisis con Gemini")
    g.add_argument("--modelo", default=os.environ.get("GEMINI_MODELO") or config.MODELO_POR_DEFECTO, metavar="M",
                   help="modelo de Gemini (por defecto GEMINI_MODELO del entorno o %(default)s)")
    g.add_argument("--equipo", default=config.EQUIPO_POR_DEFECTO, metavar="TEXTO",
                   help='equipo que se enseña en los videos, p. ej. "arco en C" (se inserta en el prompt)')
    g.add_argument("--redactor", default=config.REDACTOR_MODELO, metavar="M",
                   help="segunda pasada solo de texto con otro modelo que pule la redacción (opcional)")
    g.add_argument("--fps", type=float, default=config.FPS_POR_DEFECTO, metavar="F",
                   help="fotogramas por segundo que muestrea Gemini (por defecto el suyo, 1/s)")
    g.add_argument("--resolucion", choices=pipeline.RESOLUCIONES, default=config.RESOLUCION_VIDEO,
                   help="resolución con la que Gemini mira el video (por defecto %(default)s; con texto e iconos "
                        "en pantalla no conviene bajar de media)")
    g.add_argument("--copia-alto", type=int, choices=pipeline.ALTOS_COPIA, default=config.TRANSCODIFICAR_ALTO,
                   metavar="480|720|1080", help="alto de la copia ligera que se sube (por defecto %(default)s p)")
    g.add_argument("--subir-original", action="store_true",
                   help="sube el archivo tal cual (si cabe en --max-subida-mb) en vez de la copia ligera")
    g.add_argument("--sin-refinado", action="store_true",
                   help="no hacer la segunda pasada con las capturas en alta resolución")
    g.add_argument("--tramo-min", type=int, default=config.TRAMO_MAX_MIN, metavar="N",
                   help="videos más largos que N minutos se analizan por tramos (por defecto %(default)s)")
    g.add_argument("--max-subida-mb", type=int, default=config.MAX_SUBIDA_MB, metavar="N",
                   help="tamaño máximo del archivo que se sube (por defecto %(default)s)")
    g.add_argument("--timeout-procesado", type=int, default=config.TIMEOUT_PROCESADO_SEG, metavar="S",
                   help="segundos máximos de espera a que Gemini procese el archivo subido (por defecto %(default)s)")
    g.add_argument("--precio-entrada", type=float, metavar="X", help="US$ por millón de tokens de entrada (estimación)")
    g.add_argument("--precio-salida", type=float, metavar="Y", help="US$ por millón de tokens de salida (estimación)")
    g.add_argument("--conservar-subida", action="store_true", help="no borrar el archivo remoto tras analizarlo")
    g.add_argument("--pausa", type=_no_negativo, default=0.0, metavar="S", help="segundos de pausa entre videos")

    m = p.add_argument_group("modo de trabajo")
    modo = m.add_mutually_exclusive_group()
    modo.add_argument("--batch", action="store_true", help="envía todos los videos en un lote (50 %% más barato)")
    modo.add_argument("--batch-recoger", nargs="?", const="", default=None, metavar="ID",
                      help="recoge un lote enviado antes (sin ID: el único pendiente) y genera los documentos")
    modo.add_argument("--local", action="store_true",
                      help="sin API: cambios de plano + transcripción local (faster-whisper); privacidad total")
    modo.add_argument("--simular", action="store_true", help="sin API ni whisper: momentos de ejemplo para probar")
    m.add_argument("--esperar", action="store_true", help="con --batch-recoger: esperar a que el lote termine")
    m.add_argument("--regenerar", action="store_true",
                   help="si existe momentos.json, salta el análisis y rehace capturas y documentos a partir de él")
    m.add_argument("--forzar", action="store_true", help="volver a analizar videos ya procesados")
    m.add_argument("--solo", nargs="+", metavar="NOMBRE", help="procesar solo estos videos (nombre con o sin extensión)")

    lo = p.add_argument_group("modo local")
    lo.add_argument("--whisper-modelo", default=config.WHISPER_MODELO, metavar="M",
                    help="modelo de faster-whisper: tiny, base, small, medium, large-v3 o ruta (por defecto %(default)s)")
    lo.add_argument("--sin-whisper", action="store_true", help="modo local solo con cambios de plano")
    lo.add_argument("--offline", action="store_true", help="no descargar el modelo de whisper (usar el ya descargado)")

    h = p.add_argument_group("herramientas y documentos")
    h.add_argument("--ffmpeg", metavar="RUTA", help="ruta a ffmpeg (por defecto FFMPEG_BIN, PATH o imageio-ffmpeg)")
    h.add_argument("--ffprobe", metavar="RUTA", help="ruta a ffprobe (opcional)")
    h.add_argument("--max-momentos", type=int, metavar="N", help="conservar como máximo N momentos (los más importantes)")
    h.add_argument("--importancia-minima", type=_importancia, default=config.IMPORTANCIA_MINIMA, metavar="1-5",
                   help="descartar momentos con importancia menor (por defecto %(default)s: todos)")
    h.add_argument("--por-pagina", type=_por_pagina, default=config.POR_PAGINA, metavar="auto|1|2|3|4",
                   help="capturas por página (por defecto auto: según la cantidad de pasos)")
    h.add_argument("--sin-indice", action="store_true", help="no incluir el índice de pasos por sección")
    h.add_argument("--sin-anotaciones", action="store_true", help="no dibujar círculo/flecha en las capturas")
    return p


def _modo(args: argparse.Namespace) -> str:
    if args.batch:
        return "batch"
    if args.batch_recoger is not None:
        return "batch-recoger"
    if args.local:
        return "local"
    if args.simular:
        return "simulado"
    return "gemini"


def opciones_desde_args(args: argparse.Namespace) -> pipeline.Opciones:
    """Traduce el ``Namespace`` de argparse a ``pipeline.Opciones``."""
    return pipeline.Opciones(
        carpeta_videos=Path(args.carpeta), carpeta_salida=Path(args.salida), modo=_modo(args), modelo=args.modelo,
        fps=args.fps, api_key=gemini.obtener_api_key(), equipo=args.equipo, redactor=args.redactor,
        resolucion=args.resolucion, copia_alto=args.copia_alto, subir_original=args.subir_original,
        refinar=config.REFINAR_CON_CAPTURAS and not args.sin_refinado, regenerar=args.regenerar,
        lote_id=args.batch_recoger or None, esperar_lote=args.esperar,
        whisper_modelo=None if args.sin_whisper else args.whisper_modelo, offline=args.offline,
        ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, max_subida_mb=args.max_subida_mb,
        timeout_procesado=args.timeout_procesado, precio_entrada=args.precio_entrada, precio_salida=args.precio_salida,
        conservar_subida=args.conservar_subida, pausa=args.pausa, max_momentos=args.max_momentos,
        importancia_minima=args.importancia_minima, por_pagina=args.por_pagina, incluir_indice=not args.sin_indice,
        anotar=not args.sin_anotaciones, tramo_min=args.tramo_min, forzar=args.forzar, solo=args.solo,
        verbose=args.verbose)


def comprobar(op: pipeline.Opciones, log=print) -> str | None:
    """Comprobaciones previas; devuelve el mensaje de error de configuración (o None si todo está bien)."""
    if op.modo != "batch-recoger":
        try:
            videos = pipeline.seleccionar_videos(op, log)
        except FileNotFoundError as exc:
            return f"{exc}\nIndique la carpeta como primer argumento: python resumir_videos.py C:\\ruta\\a\\los\\videos"
        if not videos:
            extensiones = ", ".join(sorted(config.EXTENSIONES_VIDEO))
            if op.solo:
                return (f"Ningún video de {op.carpeta_videos} coincide con --solo {' '.join(op.solo)}. "
                        f"Disponibles: {', '.join(v.name for v in video.listar_videos(op.carpeta_videos)) or 'ninguno'}")
            return f"No hay videos en {Path(op.carpeta_videos).resolve()} (extensiones admitidas: {extensiones})."
        log(f"{len(videos)} video(s) en {Path(op.carpeta_videos).resolve()}: {', '.join(v.name for v in videos)}")
    try:
        ffmpeg = video.localizar_ffmpeg(op.ffmpeg)
    except RuntimeError as exc:
        return str(exc)
    ffprobe = video.localizar_ffprobe(op.ffprobe)
    log(f"ffmpeg: {ffmpeg}" + (f" | ffprobe: {ffprobe}" if ffprobe else " | ffprobe: no encontrado (opcional)"))
    if op.modo in pipeline.MODOS_CON_API and not op.api_key:
        if op.regenerar and op.modo != "batch-recoger":
            log("Aviso: no hay clave de Gemini; --regenerar rehará capturas y documentos sin refinado.")
        else:
            return ("No hay clave de API de Gemini. Cree un archivo .env junto a resumir_videos.py con la línea\n"
                    "    GEMINI_API_KEY=su_clave\n(vea .env.ejemplo; la clave se obtiene en https://aistudio.google.com/apikey).\n"
                    "Sin clave puede usar --local (privado, sin API) o --simular (para probar el circuito).")
    if op.modo == "local" and op.whisper_modelo:
        try:
            importlib.import_module("faster_whisper")   # solo se comprueba que esté instalado
        except ImportError:
            log("Aviso: faster-whisper no está instalado (pip install -r requirements-local.txt); "
                "el modo local seguirá solo con los cambios de plano.")
    if op.modo == "batch" and op.regenerar:
        log("Aviso: --regenerar con --batch solo rehace los videos que ya tienen momentos.json.")
    return None


def main(argv: list | None = None) -> int:
    """Punto de entrada: 0 si todo salió bien, 1 si algún video falló, 2 si hay un error de configuración."""
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    _cargar_env()
    args = crear_parser().parse_args(argv)
    op = opciones_desde_args(args)
    print(f"resumen_videos {__version__} — modo {op.modo}"
          + (f", modelo {op.modelo}, resolución {op.resolucion}" if op.modo in pipeline.MODOS_CON_API else "")
          + f" → {Path(op.carpeta_salida).resolve()}")
    problema = comprobar(op)
    if problema:
        print(f"\nERROR de configuración: {problema}", file=sys.stderr)
        return 2
    try:
        resumen = pipeline.procesar_carpeta(op)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario. Lo ya generado queda en la carpeta de salida.", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - errores de configuración detectados al empezar (lote, clave, precios…)
        print(f"\nERROR: {pipeline.mensaje_de_error(exc)}", file=sys.stderr)
        if args.verbose:
            raise
        return 2
    print("\n" + resumen.tabla())
    if resumen.fallidos():
        print("\nRevise error.txt en la carpeta de cada video con error.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
