"""Unión de varios videos ya procesados en un solo manual con capítulos.

Cada carpeta ``salida/<video>/`` (con su ``momentos.json`` y sus capturas) pasa a ser un capítulo: sus
pasos conservan el tiempo ``mm:ss`` de su propio video, su sección y reciben ``capitulo`` ("3. Bolus
tracking"), que el índice del manual muestra como encabezado antes de las secciones de ese capítulo.
Las capturas se copian a la carpeta del manual unido, la transcripción de cada capítulo se encadena con un
encabezado y se escribe un ``momentos.json`` propio (``video.capitulos`` lista el origen de cada uno).

No se usa la API: solo lo ya analizado.  ``resumir_videos.py --unir "Nombre"`` es la puerta de entrada.
"""
from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Callable

from . import config, documentos
from .modelos import InfoVideo, ResultadoAnalisis, Uso, formatear_tiempo

SECCION_POR_DEFECTO = "General"


def _cargar_capitulo(carpeta: Path) -> tuple[ResultadoAnalisis, dict]:
    """``(análisis, video)`` de una carpeta procesada; RuntimeError si no tiene un ``momentos.json`` usable."""
    from . import pipeline   # import perezoso: pipeline importa este módulo indirectamente por la CLI

    analisis, _documentos = pipeline.cargar_json(carpeta)
    if analisis is None or not analisis.momentos:
        raise RuntimeError(f"{carpeta}: no tiene un {config.NOMBRE_JSON} con momentos; procese ese video primero.")
    datos = pipeline._leer_json(Path(carpeta) / config.NOMBRE_JSON)
    return analisis, dict(datos.get("video") or {})


def _titulo_capitulo(analisis: ResultadoAnalisis, video: dict, carpeta: Path) -> str:
    return (analisis.titulo or video.get("nombre") or Path(carpeta).name or "Capítulo").strip()


def _copiar_captura(ruta: str | None, k: int, destino_capturas: Path) -> str | None:
    """Copia la captura a ``destino_capturas/<k>_<nombre>`` y devuelve la ruta nueva (None si no existe)."""
    if not ruta:
        return None
    origen = Path(ruta)
    if not origen.is_file():
        return None
    destino = destino_capturas / f"{k:02d}_{origen.name}"
    shutil.copyfile(origen, destino)
    return str(destino)


def unir_manuales(nombre: str, carpetas: list, carpeta_salida: Path, *, titulo: str | None = None,
                  resumen: str | None = None, equipo: str = config.EQUIPO_POR_DEFECTO, por_pagina="auto",
                  incluir_indice: bool = True, titulos_capitulos: list | None = None,
                  log: Callable[[str], None] = print) -> tuple[Path, Path, int, Path]:
    """Une los videos procesados de ``carpetas`` (en ese orden) en ``<carpeta_salida>/<nombre>/``.

    ``titulos_capitulos`` (misma longitud que ``carpetas``; una entrada vacía conserva el título del
    análisis) fija el título de cada capítulo.  Devuelve ``(docx, pdf, páginas, carpeta del manual)``.
    Lanza ``ValueError`` sin carpetas o con una lista de títulos de otra longitud, y ``RuntimeError`` si
    alguna carpeta no está procesada.
    """
    from . import pipeline

    carpetas = [Path(c) for c in carpetas]
    if not carpetas:
        raise ValueError("no hay videos procesados que unir")
    titulos_capitulos = list(titulos_capitulos or [])
    if titulos_capitulos and len(titulos_capitulos) != len(carpetas):
        raise ValueError(f"se indicaron {len(titulos_capitulos)} títulos de capítulo para {len(carpetas)} videos")
    destino = Path(carpeta_salida) / documentos._nombre_archivo_seguro(nombre)
    destino_capturas = destino / config.CARPETA_CAPTURAS
    destino_capturas.mkdir(parents=True, exist_ok=True)
    for vieja in destino_capturas.glob("*.jpg"):
        vieja.unlink(missing_ok=True)

    momentos: list = []
    transcripcion: list = []
    capitulos: list = []
    avisos: list = []
    uso_total: Uso | None = None
    modelos: list[str] = []
    duracion_total = 0.0
    for k, carpeta in enumerate(carpetas, start=1):
        analisis, video = _cargar_capitulo(carpeta)
        titulo_cap = _titulo_capitulo(analisis, video, carpeta)
        if titulos_capitulos and str(titulos_capitulos[k - 1] or "").strip():
            titulo_cap = str(titulos_capitulos[k - 1]).strip()
        log(f"Capítulo {k}: {titulo_cap} ({len(analisis.momentos)} pasos) <- {carpeta}")
        for m in analisis.momentos:
            seccion = (m.seccion or SECCION_POR_DEFECTO).strip() or SECCION_POR_DEFECTO
            momentos.append(replace(
                m, seccion=seccion, capitulo=f"{k}. {titulo_cap}",
                ruta_captura=_copiar_captura(m.ruta_captura, k, destino_capturas),
                ruta_captura_anotada=_copiar_captura(m.ruta_captura_anotada, k, destino_capturas)))
        if analisis.transcripcion:
            transcripcion.append({"encabezado": f"Capítulo {k}: {titulo_cap}"})
            transcripcion.extend(s for s in analisis.transcripcion if isinstance(s, dict))
        duracion = float(video.get("duracion_seg") or 0.0)
        duracion_total += duracion
        capitulos.append({"numero": k, "titulo": titulo_cap, "carpeta": str(carpeta),
                          "video": video.get("nombre"), "duracion": formatear_tiempo(duracion),
                          "pasos": len(analisis.momentos)})
        if analisis.uso is not None:
            uso_total = pipeline._acumular(uso_total, analisis.uso)
        if analisis.modelo and analisis.modelo not in modelos:
            modelos.append(analisis.modelo)
        avisos.extend(f"Capítulo {k}: {a}" for a in analisis.avisos)

    titulo = (titulo or f"Manual de {equipo}").strip()
    if not resumen:
        lista = "; ".join(f"{c['numero']}. {c['titulo']}" for c in capitulos)
        resumen = f"Manual en {len(capitulos)} capítulos, uno por video de capacitación: {lista}."
    modelo = " / ".join(modelos)
    unido = ResultadoAnalisis(momentos=momentos, modo="unido", modelo=modelo, resumen=resumen, uso=uso_total,
                              avisos=avisos, transcripcion=transcripcion or None, titulo=titulo)
    info = InfoVideo(ruta=destino, nombre=nombre, duracion=duracion_total, fps=0.0, ancho=0, alto=0, tamano_bytes=0,
                     extra={"capitulos": capitulos})
    pipeline.guardar_json(destino, info, unido)
    docx, pdf, paginas = documentos.generar_documentos(
        nombre, momentos, destino, titulo=titulo, resumen=resumen, duracion=duracion_total, modo="unido",
        modelo=modelo, por_pagina=por_pagina, incluir_indice=incluir_indice, transcripcion=transcripcion,
        log=log)
    pipeline.guardar_json(destino, info, unido, extra={
        "documentos": {"docx": docx.name, "pdf": pdf.name, "paginas": paginas},
        "uso_acumulado": None if uso_total is None else uso_total.a_dict()})
    log(f"Manual unido: {len(capitulos)} capítulos, {len(momentos)} pasos, {paginas} páginas -> {docx.name}, {pdf.name}")
    return docx, pdf, paginas, destino
