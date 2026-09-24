"""Tests de ``documentos.py``: paginación exacta, docx recargable, capturas por aspecto, acentos."""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.shared import Cm
from PIL import Image, ImageDraw

from resumen_videos import config, documentos
from resumen_videos.documentos import (calcular_layout, contar_paginas_indice, contar_paginas_pdf,
                                       generar_docx, generar_documentos, generar_pdf)
from resumen_videos.modelos import Momento

pymupdf = pytest.importorskip("pymupdf")

SECCIONES = ["Preparación", "Encendido", "Calibración", "Toma de imagen", "Cierre"]
SILENCIO = (lambda _msg: None)


def _captura(ruta: Path, ancho: int = 640, alto: int = 360, texto: str = "") -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (ancho, alto), (60, 70, 90))
    d = ImageDraw.Draw(img)
    d.rectangle([ancho * 0.1, alto * 0.2, ancho * 0.9, alto * 0.8], outline="white", width=4)
    d.text((ancho * 0.15, alto * 0.25), texto, fill="white")
    img.save(ruta, "JPEG", quality=85)
    return ruta


def _momentos(n: int, carpeta: Path, *, con_captura: bool = True, ancho: int = 640, alto: int = 360) -> list:
    momentos = []
    for i in range(n):
        seccion = SECCIONES[min(len(SECCIONES) - 1, i * len(SECCIONES) // max(1, n))]
        ruta = _captura(carpeta / f"{i + 1:03d}.jpg", ancho, alto, f"captura {i + 1}") if con_captura else None
        momentos.append(Momento(
            tiempo_seg=5 + i * 37.3, titulo=f"Ajustar el colimador a {10 + i} cm",
            descripcion=f"Paso {i + 1}: se cierra el colimador con las palancas laterales hasta leer "
                        f"{10 + i} cm en la pantalla; así se reduce la dosis dispersa al paciente y al operador.",
            importancia=3, fuente="ambos", seccion=seccion, ruta_captura=str(ruta) if ruta else None))
    return momentos


def _texto_pdf(ruta: Path) -> list[str]:
    with pymupdf.open(str(ruta)) as doc:
        return [pagina.get_text() for pagina in doc]


def _paginas_esperadas(momentos: list, por_pagina="auto", incluir_indice: bool = True) -> int:
    L = calcular_layout(len(momentos), por_pagina)
    indice = contar_paginas_indice(momentos, por_pagina) if incluir_indice else 0
    return 1 + indice + L.paginas_contenido


def _generar(tmp_path: Path, momentos: list, **kw):
    kw.setdefault("log", SILENCIO)
    return generar_documentos("video_prueba", momentos, tmp_path / "salida", **kw)


# ----------------------------------------------------------------------------- layout
def test_layout_auto_umbrales():
    assert [calcular_layout(n).por_pagina for n in (1, 5, 6, 10, 11, 15, 16, 47, 300)] == [1, 1, 2, 2, 3, 3, 4, 4, 4]
    L = calcular_layout(20)
    assert (L.cols, L.filas, L.horizontal) == (2, 2, True)
    assert L.paginas_contenido == 5 and L.pag_w_cm > L.pag_h_cm
    for n in (1, 3, 8, 13, 20, 47):
        L = calcular_layout(n)
        assert L.paginas_contenido == math.ceil(n / L.por_pagina)
        # la imagen y el texto reservado caben en la celda
        texto_pt = (L.pt_meta + L.pt_titulo + L.lineas_desc * L.pt_desc) * documentos.INTERLINEADO
        assert L.img_h_cm + texto_pt / 72 * 2.54 <= L.celda_h_cm + 1e-6
        assert L.img_w_cm <= L.interior_w_cm + 1e-6
        assert L.lineas_desc >= (3 if L.por_pagina == 4 else 2)


def test_layout_forzado_y_valores_invalidos():
    for pp in (1, 2, 3, 4):
        assert calcular_layout(40, pp).por_pagina == pp
        assert calcular_layout(40, str(pp)).por_pagina == pp
    assert calcular_layout(40, "auto").por_pagina == 4
    assert calcular_layout(0).n == 1
    for malo in (0, 5, "5", "x", True, 2.5):
        with pytest.raises(ValueError):
            calcular_layout(10, malo)


def test_layout_ratio_vertical_no_desborda():
    for n in (1, 8, 13, 20):
        L = calcular_layout(n, ratio=9 / 16)
        assert L.img_w_cm <= L.interior_w_cm + 1e-6
        assert L.img_h_cm <= L.celda_h_cm - documentos.CELDA_MARGEN_SUP_CM + 1e-6


# ----------------------------------------------------------------------------- paginación exacta
@pytest.mark.parametrize("n", [1, 3, 8, 13, 20, 47])
def test_paginas_pdf_y_docx_recargable(tmp_path, n):
    momentos = _momentos(n, tmp_path / "capturas")
    docx, pdf, paginas = _generar(tmp_path, momentos, titulo="Manual de prueba", resumen="Resumen breve.",
                                  duracion=1500.0, modo="simulado", modelo="ninguno")
    L = calcular_layout(n)
    assert docx.name == "video_prueba.docx" and pdf.name == "video_prueba.pdf"
    assert paginas == _paginas_esperadas(momentos) == 1 + contar_paginas_indice(momentos) + math.ceil(n / L.por_pagina)
    assert contar_paginas_pdf(pdf) == paginas
    with pymupdf.open(str(pdf)) as doc:
        assert doc.page_count == paginas
        ancho, alto = doc[1].rect.width, doc[1].rect.height
        assert (ancho > alto) == L.horizontal
    d = Document(str(docx))
    assert len(d.tables) == L.paginas_contenido
    assert len(d.inline_shapes) == n
    assert all(fila.height_rule == WD_ROW_HEIGHT_RULE.EXACTLY for tabla in d.tables for fila in tabla.rows)
    assert d.sections[0].orientation == (WD_ORIENT.LANDSCAPE if L.horizontal else WD_ORIENT.PORTRAIT)
    saltos = sum(1 for p in d.paragraphs if p.paragraph_format.page_break_before)
    assert saltos == L.paginas_contenido + 1          # cabecera del índice + una por página de pasos
    texto = "".join(_texto_pdf(pdf))
    assert f"PASO {n} · " in texto and "PASO 1 · PREPARACIÓN · 00:05" in texto


@pytest.mark.parametrize("pp", [1, 2, 3, 4])
def test_por_pagina_forzado(tmp_path, pp):
    momentos = _momentos(7, tmp_path / "capturas")
    _docx, pdf, paginas = _generar(tmp_path, momentos, por_pagina=pp)
    assert paginas == 1 + contar_paginas_indice(momentos, pp) + math.ceil(7 / pp)
    with pymupdf.open(str(pdf)) as doc:
        assert (doc[0].rect.width > doc[0].rect.height) == (pp == 4)
    assert "Pasos 1–" in _texto_pdf(pdf)[2] if pp > 1 else "Paso 1 de 7" in _texto_pdf(pdf)[2]


def test_por_pagina_como_cadena(tmp_path):
    momentos = _momentos(5, tmp_path / "capturas")
    _docx, _pdf, paginas = _generar(tmp_path, momentos, por_pagina="2")
    assert paginas == 1 + contar_paginas_indice(momentos, 2) + 3


def test_sin_indice(tmp_path):
    momentos = _momentos(8, tmp_path / "capturas")
    docx, pdf, paginas = _generar(tmp_path, momentos, incluir_indice=False)
    assert paginas == 1 + math.ceil(8 / 2)
    paginas_texto = _texto_pdf(pdf)
    assert not any("Índice" in t for t in paginas_texto)
    assert "Pasos 1–2 de 8" in paginas_texto[1]
    d = Document(str(docx))
    assert not any(p.text.strip() == "Índice" for p in d.paragraphs)
    assert sum(1 for p in d.paragraphs if p.paragraph_format.page_break_before) == 4


def test_indice_de_varias_paginas_vertical_y_horizontal(tmp_path):
    momentos = _momentos(90, tmp_path / "capturas", con_captura=False)
    for pp in (3, "auto"):
        n_indice = contar_paginas_indice(momentos, pp)
        assert n_indice >= 2
        _docx, pdf, paginas = _generar(tmp_path / str(pp), momentos, por_pagina=pp)
        assert paginas == _paginas_esperadas(momentos, pp)
        textos = _texto_pdf(pdf)
        assert sum(1 for t in textos if "Índice · Página" in t) == n_indice
        assert "1. Ajustar" in textos[1] and "90. Ajustar" in textos[n_indice]
    # en A4 horizontal el índice va a dos columnas: caben más pasos por página
    muchos = _momentos(240, tmp_path / "sin_capturas", con_captura=False)
    assert 2 <= contar_paginas_indice(muchos, "auto") < contar_paginas_indice(muchos, 3)
    assert contar_paginas_indice([], "auto") == 0


def test_indice_agrupa_por_seccion_y_sin_seccion(tmp_path):
    momentos = _momentos(6, tmp_path / "capturas")
    momentos[0].seccion = None
    momentos[1].seccion = "   "
    _docx, pdf, _paginas = _generar(tmp_path, momentos)
    indice = _texto_pdf(pdf)[1]
    assert documentos.SECCION_POR_DEFECTO in indice
    for seccion in {m.seccion for m in momentos[2:]}:
        assert seccion in indice
    d = Document(str(_docx))
    textos = [p.text for p in d.paragraphs]
    assert any(t.startswith("1. Ajustar") and t.endswith("00:05") for t in textos)


# ----------------------------------------------------------------------------- capturas
def test_sin_captura_muestra_caja(tmp_path):
    momentos = _momentos(6, tmp_path / "capturas")
    for m in momentos[::2]:
        m.ruta_captura = None
    momentos[1].ruta_captura = str(tmp_path / "capturas" / "no_existe.jpg")
    docx, pdf, _paginas = _generar(tmp_path, momentos)
    texto = "".join(_texto_pdf(pdf))
    assert texto.count("[sin captura]") == 4
    d = Document(str(docx))
    assert len(d.inline_shapes) == 2
    celdas = [c.text for t in d.tables for r in t.rows for c in r.cells]
    assert sum(1 for c in celdas if "[sin captura]" in c) == 4


def test_captura_vertical_no_desborda(tmp_path):
    for n in (3, 8, 20):
        momentos = _momentos(n, tmp_path / f"capturas_{n}", ancho=360, alto=640)
        docx, pdf, paginas = _generar(tmp_path / str(n), momentos)
        assert paginas == _paginas_esperadas(momentos)
        L = calcular_layout(n, ratio=9 / 16)
        d = Document(str(docx))
        assert len(d.inline_shapes) == n
        for forma in d.inline_shapes:
            assert forma.height <= Cm(L.celda_h_cm)
            assert forma.height <= Cm(L.img_h_cm) + 1000          # 1000 EMU ≈ 0.003 cm de redondeo
            assert forma.width <= Cm(L.interior_w_cm) + 1000
            assert abs(forma.width / forma.height - 360 / 640) < 0.01   # aspecto conservado


def test_captura_anotada_tiene_prioridad(tmp_path):
    momentos = _momentos(2, tmp_path / "capturas")
    anotada = _captura(tmp_path / "capturas" / "001_anotada.jpg", texto="anotada")
    momentos[0].ruta_captura_anotada = str(anotada)
    _docx, pdf, _paginas = _generar(tmp_path, momentos)
    with pymupdf.open(str(pdf)) as doc:
        imagenes = [doc.extract_image(x[0])["image"] for x in doc[2].get_images()]
    assert anotada.read_bytes() in imagenes


def test_jpeg_incrustado_sin_recodificar(tmp_path):
    momentos = _momentos(1, tmp_path / "capturas")
    _docx, pdf, _paginas = _generar(tmp_path, momentos)
    with pymupdf.open(str(pdf)) as doc:
        info = [doc.extract_image(x[0]) for x in doc[2].get_images()]
    assert len(info) == 1 and info[0]["ext"] == "jpeg"
    assert info[0]["image"] == Path(momentos[0].ruta_captura).read_bytes()


def test_captura_con_aspecto_panoramico(tmp_path):
    momentos = _momentos(4, tmp_path / "capturas", ancho=1280, alto=360)
    docx, _pdf, _paginas = _generar(tmp_path, momentos)
    L = calcular_layout(4, ratio=1280 / 360)
    d = Document(str(docx))
    for forma in d.inline_shapes:
        assert forma.width <= Cm(L.interior_w_cm) + 1000
        assert forma.height <= Cm(L.img_h_cm) + 1000


# ----------------------------------------------------------------------------- textos
def test_tildes_y_signos_en_pdf(tmp_path):
    momentos = _momentos(3, tmp_path / "capturas")
    momentos[0].titulo = "Configuración de la máquina: ¿está lista? ñandú"
    momentos[0].descripcion = "Sí, también ü y ¡ojo! con «comillas» y el símbolo € & <etiquetas>."
    momentos[0].seccion = "Revisión inicial"
    _docx, pdf, _paginas = _generar(tmp_path, momentos, titulo="Título con acentos: áéíóú", resumen="Año 2026 ñ.")
    texto = "".join(_texto_pdf(pdf))
    for fragmento in ("Configuración", "¿está lista? ñandú", "Sí, también ü y ¡ojo!", "«comillas»", "€ & <etiquetas>",
                      "Título con acentos: áéíóú", "Año 2026 ñ.", "REVISIÓN INICIAL"):
        assert fragmento in texto


def test_fuentes_fallback_helvetica(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CARPETA_FUENTES", tmp_path / "sin_fuentes")
    fuentes = documentos._fuentes_pdf()
    assert fuentes == {"regular": "Helvetica", "negrita": "Helvetica-Bold", "latin1": True}
    momentos = _momentos(2, tmp_path / "capturas")
    momentos[0].titulo = "Señal ¿lista? → sí"
    _docx, pdf, paginas = _generar(tmp_path, momentos)
    assert paginas == _paginas_esperadas(momentos)
    texto = "".join(_texto_pdf(pdf))
    assert "Señal ¿lista? ? sí" in texto      # '→' no existe en cp1252: se sustituye, no falla


def test_fuentes_dejavu_registradas():
    fuentes = documentos._fuentes_pdf()
    assert fuentes["regular"] == "DejaVu" and fuentes["negrita"] == "DejaVu-Bold" and not fuentes["latin1"]


def test_textos_largos_se_recortan_sin_desbordar(tmp_path):
    momentos = _momentos(20, tmp_path / "capturas")
    for m in momentos:
        m.titulo = "Título extremadamente largo que no cabe en una sola línea de la celda " * 3
        m.descripcion = "Descripción muy larga que debe recortarse con puntos suspensivos. " * 12
        m.seccion = "Sección con un nombre desproporcionadamente largo para la línea del paso " * 2
    _docx, pdf, paginas = _generar(tmp_path, momentos)
    assert paginas == _paginas_esperadas(momentos)
    texto = "".join(_texto_pdf(pdf))
    assert "…" in texto
    d = Document(str(_docx))
    celda = d.tables[0].rows[0].cells[0]
    assert all(len(p.text) < 400 for p in celda.paragraphs)


def test_portada(tmp_path):
    momentos = _momentos(4, tmp_path / "capturas")
    _docx, pdf, _paginas = _generar(tmp_path, momentos, titulo="Uso del arco en C", resumen="Enseña el encendido.",
                                    fecha="01/02/2026", duracion=1503, modo="local", modelo="scdet")
    portada = _texto_pdf(pdf)[0]
    for fragmento in ("MANUAL DE PROCEDIMIENTO", "Uso del arco en C", "Enseña el encendido.", "video_prueba",
                      "01/02/2026", "25:03", "4", "Generado con resumen_videos · modo local · modelo scdet"):
        assert fragmento in portada
    d = Document(str(_docx))
    cuerpo = "\n".join(p.text for p in d.paragraphs)
    assert "Uso del arco en C" in cuerpo and "modo local · modelo scdet" in cuerpo and "25:03" in cuerpo
    assert d.core_properties.title == "Uso del arco en C"
    assert d.sections[0].different_first_page_header_footer
    assert "PAGE" in d.sections[0].footer._element.xml


def test_portada_por_defecto_usa_nombre_y_fecha_de_hoy(tmp_path):
    import datetime as dt
    momentos = _momentos(1, tmp_path / "capturas")
    _docx, pdf, _paginas = _generar(tmp_path, momentos)
    portada = _texto_pdf(pdf)[0]
    assert "video_prueba" in portada and dt.date.today().strftime("%d/%m/%Y") in portada
    assert "Duración" not in portada and "Generado con resumen_videos" in portada


# ----------------------------------------------------------------------------- API
def test_momentos_vacio_lanza_valueerror(tmp_path):
    for funcion in (generar_documentos, generar_docx, generar_pdf):
        with pytest.raises(ValueError):
            funcion("video", [], tmp_path)


def test_generar_por_separado_y_nombre_seguro(tmp_path):
    momentos = _momentos(2, tmp_path / "capturas")
    docx = generar_docx('Toma: "final"?', momentos, tmp_path / "s", log=SILENCIO)
    pdf = generar_pdf('Toma: "final"?', momentos, tmp_path / "s", log=SILENCIO)
    assert docx == tmp_path / "s" / "Toma_ _final__.docx" and docx.exists()
    assert pdf == tmp_path / "s" / "Toma_ _final__.pdf" and contar_paginas_pdf(pdf) == _paginas_esperadas(momentos)


def test_contar_paginas_pdf_invalido(tmp_path):
    assert contar_paginas_pdf(tmp_path / "no_existe.pdf") == -1
    roto = tmp_path / "roto.pdf"
    roto.write_bytes(b"esto no es un pdf")
    assert contar_paginas_pdf(roto) == -1


def test_log_recibe_progreso(tmp_path):
    momentos = _momentos(2, tmp_path / "capturas")
    mensajes = []
    _generar(tmp_path, momentos, log=mensajes.append)
    assert any("docx" in m for m in mensajes) and any("pdf" in m for m in mensajes)
