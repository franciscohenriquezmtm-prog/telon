"""Tests de ``documentos.py``: paginación exacta, docx recargable, capturas por aspecto, textos íntegros, acentos."""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.oxml.ns import qn
from docx.shared import Cm
from PIL import Image, ImageDraw
from reportlab.lib.units import cm

from resumen_videos import config, documentos
from resumen_videos.documentos import (calcular_layout, contar_paginas_indice, contar_paginas_pdf,
                                       generar_docx, generar_documentos, generar_pdf)
from resumen_videos.modelos import Momento

pymupdf = pytest.importorskip("pymupdf")

SECCIONES = ["Preparación", "Encendido", "Calibración", "Toma de imagen", "Cierre"]
SILENCIO = (lambda _msg: None)
TITULO_80 = "Ajustar el colimador a 10 cm y confirmar el valor de kV en la pantalla principal"
DESCRIPCION_252 = ("Se cierra el colimador con las palancas laterales hasta leer 10 cm en la pantalla táctil; así se "
                   "reduce la dosis dispersa al paciente y al operador. Verificar que el indicador DAP marque ≤ 0,5 "
                   "Gy·cm² y que el pedal no supere 2 s de exposición continua.")
assert len(TITULO_80) == config.MAX_TITULO and len(DESCRIPCION_252) <= config.MAX_DESCRIPCION


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


def _layout_de(momentos: list, por_pagina="auto"):
    return calcular_layout(len(momentos), por_pagina, ratio=documentos._ratio_capturas(momentos))


def _paginas_esperadas(momentos: list, por_pagina="auto", incluir_indice: bool = True) -> int:
    L = _layout_de(momentos, por_pagina)
    indice = contar_paginas_indice(momentos, por_pagina) if incluir_indice else 0
    return 1 + indice + L.paginas_contenido


def _generar(tmp_path: Path, momentos: list, **kw):
    kw.setdefault("log", SILENCIO)
    return generar_documentos("video_prueba", momentos, tmp_path / "salida", **kw)


def _celdas_docx(docx: Path) -> list[str]:
    return [c.text for t in Document(str(docx)).tables for r in t.rows for c in r.cells]


# ----------------------------------------------------------------------------- layout
def test_layout_auto_umbrales():
    assert [calcular_layout(n).por_pagina for n in (1, 5, 6, 10, 11, 15, 16, 47, 300)] == [1, 1, 2, 2, 3, 3, 4, 4, 4]
    L = calcular_layout(20)
    assert (L.cols, L.filas, L.horizontal) == (2, 2, True) and L.descripcion == "A4 horizontal 2x2"
    assert L.paginas_contenido == 5 and L.pag_w_cm > L.pag_h_cm and not L.vertical
    for n in (1, 3, 8, 13, 20, 47):
        L = calcular_layout(n)
        assert L.paginas_contenido == math.ceil(n / L.por_pagina)
        # la imagen de referencia y el texto reservado caben en la celda
        texto_pt = (L.pt_meta + L.pt_titulo + L.lineas_desc * L.pt_desc) * documentos.INTERLINEADO
        assert L.img_h_cm + texto_pt / 72 * 2.54 <= L.celda_h_cm + 1e-6
        assert L.img_w_cm <= L.interior_w_cm + 1e-6
        assert L.lineas_desc >= (3 if L.por_pagina == 4 else 2)
        assert L.lineas_desc_max >= (6 if L.por_pagina == 4 else 8) and L.lineas_desc_max >= L.lineas_desc


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


def test_layout_capturas_verticales_dos_por_pagina():
    """Capturas verticales (iPhone en vertical): en 'auto' como máximo 2 por página, lado a lado en A4 horizontal."""
    for n in (7, 13, 23, 90):
        L = calcular_layout(n, ratio=9 / 16)
        assert L.vertical and (L.por_pagina, L.cols, L.filas, L.horizontal) == (2, 2, 1, True)
        assert L.descripcion == "A4 horizontal 2x1"
        assert L.img_w_cm >= 5.5 and L.img_h_cm >= 10.0        # antes: 3,7 cm de ancho en 1x3 y 2x2
        assert L.paginas_contenido == math.ceil(n / 2)
    L = calcular_layout(3, ratio=9 / 16)                        # 1x1: imagen a toda la altura útil
    assert (L.por_pagina, L.cols, L.filas, L.horizontal) == (1, 1, 1, False)
    assert L.img_h_cm >= 20.0 and L.img_w_cm <= L.interior_w_cm + 1e-6
    assert calcular_layout(23, 4, ratio=9 / 16).descripcion == "A4 horizontal 2x2"   # forzado: se respeta
    assert calcular_layout(23, 3, ratio=9 / 16).descripcion == "A4 vertical 1x3"


# ----------------------------------------------------------------------------- paginación exacta
@pytest.mark.parametrize("n", [1, 3, 8, 13, 20, 47])
def test_paginas_pdf_y_docx_recargable(tmp_path, n):
    momentos = _momentos(n, tmp_path / "capturas")
    docx, pdf, paginas = _generar(tmp_path, momentos, titulo="Manual de prueba", resumen="Resumen breve.",
                                  duracion=1500.0, modo="simulado", modelo="ninguno")
    L = calcular_layout(n)
    n_indice = contar_paginas_indice(momentos)
    assert docx.name == "video_prueba.docx" and pdf.name == "video_prueba.pdf"
    assert paginas == _paginas_esperadas(momentos) == 1 + n_indice + math.ceil(n / L.por_pagina)
    assert contar_paginas_pdf(pdf) == paginas
    with pymupdf.open(str(pdf)) as doc:
        assert doc.page_count == paginas
        ancho, alto = doc[1].rect.width, doc[1].rect.height
        assert (ancho > alto) == L.horizontal
    d = Document(str(docx))
    assert len(d.tables) == n_indice + L.paginas_contenido        # una tabla por página de índice y de pasos
    assert len(d.inline_shapes) == n
    assert all(fila.height_rule == WD_ROW_HEIGHT_RULE.EXACTLY for tabla in d.tables for fila in tabla.rows)
    assert d.sections[0].orientation == (WD_ORIENT.LANDSCAPE if L.horizontal else WD_ORIENT.PORTRAIT)
    saltos = sum(1 for p in d.paragraphs if p.paragraph_format.page_break_before)
    assert saltos == L.paginas_contenido + n_indice               # una cabecera por página tras la portada
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


def test_indice_de_varias_paginas_con_numero_de_pagina(tmp_path):
    """El índice se pagina por medida (una columna) igual en pdf y docx, y cada paso indica su página real."""
    momentos = _momentos(90, tmp_path / "capturas", con_captura=False)
    for pp in (3, "auto"):
        n_indice = contar_paginas_indice(momentos, pp)
        assert n_indice >= 2
        docx, pdf, paginas = _generar(tmp_path / str(pp), momentos, por_pagina=pp)
        assert paginas == _paginas_esperadas(momentos, pp)
        textos = _texto_pdf(pdf)
        assert sum(1 for t in textos if "Índice · Página" in t) == n_indice
        assert "1. Ajustar" in textos[1] and "90. Ajustar" in textos[n_indice]
        L = calcular_layout(90, pp)
        for numero in (1, 37, 90):
            pagina = 1 + n_indice + math.ceil(numero / L.por_pagina)
            assert f"pág. {pagina}" in "".join(textos[1:n_indice + 1])
            assert f"PASO {numero} · " in textos[pagina - 1]              # la página indicada es la correcta
        d = Document(str(docx))
        assert len(d.tables) == n_indice + L.paginas_contenido
        celdas = _celdas_docx(docx)
        assert any(c.startswith("1. Ajustar") for c in celdas) and any(c.startswith("90. Ajustar") for c in celdas)
        assert any(c.strip() == f"00:05 · pág. {1 + n_indice + 1}" for c in celdas)
    assert contar_paginas_indice([], "auto") == 0


def test_indice_titulos_completos_en_varias_lineas(tmp_path):
    """Los títulos largos del índice no se recortan: ocupan varias líneas (pdf y docx)."""
    momentos = _momentos(20, tmp_path / "capturas", con_captura=False)
    for m in momentos:
        m.titulo = TITULO_80
    momentos[0].titulo = ("Verificar que el indicador DAP marque ≤ 0,5 Gy·cm² → confirmar en la pantalla principal "
                          "antes de liberar el pedal")     # 113 caracteres (momentos.json editado a mano)
    for pp in ("auto", 3):     # A4 horizontal (columna ancha: 1 línea) y A4 vertical (ese título ocupa 2 líneas)
        n_indice = contar_paginas_indice(momentos, pp)
        docx, pdf, paginas = _generar(tmp_path / str(pp), momentos, por_pagina=pp)
        assert paginas == _paginas_esperadas(momentos, pp)
        indice = " ".join(" ".join(_texto_pdf(pdf)[1:n_indice + 1]).split())
        assert "…" not in indice and TITULO_80 in indice and momentos[0].titulo in indice
        celdas = _celdas_docx(docx)
        assert f"1. {momentos[0].titulo}" in celdas and f"2. {TITULO_80}" in celdas
        L = calcular_layout(20, pp)
        F = documentos._fuentes_pdf()
        entradas = [e for e in documentos._entradas_indice(momentos, L, F) if e.tipo == "paso"]
        assert all(e.alto == e.lineas * documentos.ALTO_INDICE_PASO_PT for e in entradas)
        assert (max(e.lineas for e in entradas) >= 2) == (pp == 3)   # en A4 vertical el título de 87 caracteres se parte
        # en el docx la fila EXACTA de cada entrada mide sus líneas
        filas_pt = [int(f._tr.trPr.find(qn("w:trHeight")).get(qn("w:val"))) / 20
                    for t in Document(str(docx)).tables[:n_indice] for f in t.rows]
        assert max(filas_pt) >= (2 if pp == 3 else 1) * documentos.ALTO_INDICE_PASO_PT


def test_indice_agrupa_por_seccion_y_sin_seccion(tmp_path):
    momentos = _momentos(6, tmp_path / "capturas")
    momentos[0].seccion = None
    momentos[1].seccion = "   "
    docx, pdf, _paginas = _generar(tmp_path, momentos)
    indice = _texto_pdf(pdf)[1]
    assert documentos.SECCION_POR_DEFECTO in indice
    for seccion in {m.seccion for m in momentos[2:]}:
        assert seccion in indice
    celdas = _celdas_docx(docx)
    assert "1. Ajustar el colimador a 10 cm" in celdas and documentos.SECCION_POR_DEFECTO in celdas
    assert any(c.strip().startswith("00:05 · pág.") for c in celdas)


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
    assert sum(1 for c in _celdas_docx(docx) if "[sin captura]" in c) == 4


def test_captura_vertical_no_desborda(tmp_path):
    for n in (3, 8, 20):
        momentos = _momentos(n, tmp_path / f"capturas_{n}", ancho=360, alto=640)
        docx, pdf, paginas = _generar(tmp_path / str(n), momentos)
        assert paginas == _paginas_esperadas(momentos)
        L = calcular_layout(n, ratio=9 / 16)
        assert L.vertical and L.por_pagina == (1 if n <= 5 else 2)
        d = Document(str(docx))
        assert len(d.inline_shapes) == n
        for forma in d.inline_shapes:
            assert forma.height <= Cm(L.celda_h_cm)
            assert forma.height <= Cm(L.img_h_cm) + 1000          # 1000 EMU ≈ 0.003 cm de redondeo
            assert forma.width <= Cm(L.interior_w_cm) + 1000
            assert forma.height >= Cm(10.0)                       # legible: no las capturas de 3,7 cm de antes
            assert abs(forma.width / forma.height - 360 / 640) < 0.01   # aspecto conservado
        with pymupdf.open(str(pdf)) as doc:
            assert (doc[1].rect.width > doc[1].rect.height) == L.horizontal


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


# ----------------------------------------------------------------------------- textos íntegros (nunca recortar en silencio)
@pytest.mark.parametrize("pp", [1, 2, 3, 4])
def test_descripcion_y_titulo_completos_en_todas_las_rejillas(tmp_path, pp):
    """Con los límites del proyecto (título 80, descripción 260) el texto va íntegro: la imagen se reduce lo justo."""
    momentos = _momentos(20, tmp_path / "capturas")
    for m in momentos:
        m.titulo, m.descripcion = TITULO_80, DESCRIPCION_252
    avisos: list = []
    docx, pdf, paginas = _generar(tmp_path, momentos, por_pagina=pp, avisos=avisos)
    assert avisos == [] and paginas == _paginas_esperadas(momentos, pp)
    texto = " ".join("".join(_texto_pdf(pdf)[-2:]).split())
    assert "…" not in texto and "2 s de exposición continua." in texto
    assert texto.count(DESCRIPCION_252) >= 1 and TITULO_80 in texto
    celdas = _celdas_docx(docx)
    assert sum(1 for c in celdas if DESCRIPCION_252 in c and TITULO_80 in c) == 20
    L = calcular_layout(20, pp)
    d = Document(str(docx))
    for forma in d.inline_shapes:      # la imagen se reduce como máximo hasta que quepan las líneas del tope
        assert Cm(L.img_h_cm * 0.6) <= forma.height <= Cm(L.img_h_cm) + 1000


def test_texto_imposible_se_recorta_con_aviso(tmp_path):
    momentos = _momentos(20, tmp_path / "capturas")
    for m in momentos:
        m.titulo = "Título extremadamente largo que no cabe en una sola línea de la celda " * 3
        m.descripcion = "Descripción muy larga que debe recortarse con puntos suspensivos. " * 12
        m.seccion = "Sección con un nombre desproporcionadamente largo para la línea del paso " * 2
    avisos: list = []
    mensajes: list = []
    docx, pdf, paginas = _generar(tmp_path, momentos, log=mensajes.append, avisos=avisos)
    assert paginas == _paginas_esperadas(momentos)
    assert len(avisos) == 20 and all(a.startswith(f"paso {i}: ") for i, a in enumerate(avisos, 1))
    assert all("descripción de" in a and "título de" in a and config.NOMBRE_JSON in a for a in avisos)
    assert [m for m in mensajes if "aviso: paso 1:" in m]
    assert "…" in "".join(_texto_pdf(pdf))
    celda = Document(str(docx)).tables[-1].rows[0].cells[0]
    assert all(len(p.text) < 700 for p in celda.paragraphs) and any("…" in p.text for p in celda.paragraphs)


def test_recortar_a_lineas_conserva_lo_que_cabe():
    """El recorte quita palabras del final una a una: no pierde un 10 % de golpe."""
    F = documentos._fuentes_pdf()
    L = calcular_layout(20, 4)
    ancho = (L.celda_w_cm - 2 * documentos.CELDA_MARGEN_LAT_CM) * cm
    estilo = documentos._estilo(F, L.pt_desc)
    base = ("palabra " * 32).strip()
    resultados = [documentos._recortar_a_lineas(base + " extra" * k, estilo, ancho, 3, F) for k in (0, 1, 2, 5)]
    assert len({len(r) for r in resultados}) == 1 and all(r.endswith("…") for r in resultados)
    assert documentos._lineas(resultados[0], estilo, ancho, F) == 3
    assert documentos._lineas(resultados[0][:-1] + " palabra…", estilo, ancho, F) > 3   # una palabra más no cabe
    assert documentos._recortar_a_lineas("corto", estilo, ancho, 3, F) == "corto"
    assert documentos._recortar_a_lineas("", estilo, ancho, 3, F) == ""


def _alto_contenido_celda(celda, margen_sup_pt: float, ancho_pt: float, F: dict) -> float:
    """Suma, a partir del XML, lo que ocupa una celda del docx (líneas medidas con las fuentes del PDF)."""
    total = margen_sup_pt
    for p in celda.paragraphs:
        sp = p._p.pPr.find(qn("w:spacing"))
        antes = int(sp.get(qn("w:before")) or 0) / 20
        despues = int(sp.get(qn("w:after")) or 0) / 20
        extent = p._p.xpath(".//wp:extent")
        if extent:   # línea de la imagen: alto de la imagen + descendente de la marca de párrafo (2 pt)
            total += int(extent[0].get("cy")) / 12700 + 1.0 + antes + despues
            continue
        linea = int(sp.get(qn("w:line"))) / 20
        pt = max((run.font.size.pt for run in p.runs if run.font.size), default=10)
        negrita = any(run.bold for run in p.runs)
        lineas = documentos._lineas(p.text, documentos._estilo(F, pt, negrita=negrita), ancho_pt, F)
        total += antes + max(1, lineas) * linea + despues
    return total


@pytest.mark.parametrize("n, pp", [(23, 4), (13, 3), (7, 2)])
def test_celdas_docx_con_holgura(tmp_path, n, pp):
    """El contenido de cada celda queda al menos 4 pt por debajo de la fila EXACTA (Word no recorta nada)."""
    momentos = _momentos(n, tmp_path / "capturas")
    for m in momentos:
        m.titulo, m.descripcion = TITULO_80, DESCRIPCION_252
    docx, _pdf, _paginas = _generar(tmp_path, momentos, por_pagina=pp)
    F = documentos._fuentes_pdf()
    d = Document(str(docx))
    comprobadas = 0
    for tabla in d.tables:
        mar = tabla._tbl.tblPr.find(qn("w:tblCellMar"))
        margen_sup = int(mar.find(qn("w:top")).get(qn("w:w"))) / 20
        margen_lat = int(mar.find(qn("w:left")).get(qn("w:w"))) / 20
        for fila in tabla.rows:
            fila_pt = int(fila._tr.trPr.find(qn("w:trHeight")).get(qn("w:val"))) / 20
            if fila_pt < 100:
                continue                       # filas del índice (su alto es exactamente el del contenido)
            for celda in fila.cells:
                if not celda.text.strip():
                    continue
                ancho = int(celda._tc.tcPr.find(qn("w:tcW")).get(qn("w:w"))) / 20 - 2 * margen_lat
                assert _alto_contenido_celda(celda, margen_sup, ancho, F) <= fila_pt - documentos.SEGURIDAD_CELDA_PT
                comprobadas += 1
    assert comprobadas == n
    p_img = d.tables[-1].rows[0].cells[0].paragraphs[0]
    assert p_img._p.pPr.find(qn("w:rPr")).find(qn("w:sz")).get(qn("w:val")) == "4"   # marca de párrafo a 2 pt


def test_caracteres_de_control_no_rompen_el_docx(tmp_path):
    momentos = _momentos(3, tmp_path / "capturas")
    momentos[0].titulo = "Título con control \x01 aquí"
    momentos[0].descripcion = "Descripción \x02 con \x1f varios \x7f controles"
    momentos[0].seccion = "Sec\x03ción"
    docx, pdf, paginas = _generar(tmp_path, momentos, titulo="Manual \x05 raro", resumen="Resumen \x0b malo")
    assert paginas == _paginas_esperadas(momentos)
    d = Document(str(docx))
    todo = "\n".join(p.text for p in d.paragraphs) + "\n".join(_celdas_docx(docx))
    assert not any(ord(ch) < 32 and ch not in "\t\n" for ch in todo)
    assert "Título con control aquí" in todo and "Descripción con varios controles" in todo and "SECCIÓN" in todo
    assert "Manual raro" in todo and "Resumen malo" in todo
    texto = "".join(_texto_pdf(pdf))
    assert "Título con control aquí" in texto and "Descripción con varios controles" in texto


def test_avisos_de_recorte_por_log_y_por_lista(tmp_path):
    momentos = _momentos(2, tmp_path / "capturas")
    momentos[1].descripcion = "Frase repetida hasta el infinito. " * 200
    mensajes: list = []
    avisos: list = []
    _generar(tmp_path, momentos, log=mensajes.append, avisos=avisos)
    assert len(avisos) == 1 and avisos[0].startswith("paso 2: ") and "descripción de" in avisos[0]
    assert "título" not in avisos[0]
    assert sum(1 for m in mensajes if m.strip().startswith("aviso: paso 2:")) == 1
    solo_docx: list = []
    generar_docx("v", momentos, tmp_path / "d", log=SILENCIO, avisos=solo_docx)
    solo_pdf: list = []
    generar_pdf("v", momentos, tmp_path / "p", log=SILENCIO, avisos=solo_pdf)
    assert solo_docx == solo_pdf == avisos


# ----------------------------------------------------------------------------- fuentes y acentos
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


def test_fuentes_dejavu_registradas():
    fuentes = documentos._fuentes_pdf()
    assert fuentes["regular"] == "DejaVu" and fuentes["negrita"] == "DejaVu-Bold" and not fuentes["latin1"]
    assert Path(fuentes["origen"]) == Path(config.CARPETA_FUENTES) / "DejaVuSans.ttf"


def test_fuentes_del_sistema_si_falta_la_carpeta(tmp_path, monkeypatch):
    """Sin fuentes/ del proyecto se usa la primera fuente TrueType del sistema que exista."""
    origen = Path(config.CARPETA_FUENTES)
    sistema = tmp_path / "Fonts"
    sistema.mkdir()
    shutil.copy(origen / "DejaVuSans.ttf", sistema / "arial.ttf")
    shutil.copy(origen / "DejaVuSans-Bold.ttf", sistema / "arialbd.ttf")
    monkeypatch.setattr(config, "CARPETA_FUENTES", tmp_path / "sin_fuentes")
    monkeypatch.setattr(documentos, "FUENTES_SISTEMA", (
        (tmp_path / "no" / "segoeui.ttf", tmp_path / "no" / "segoeuib.ttf", "SegoeUI"),     # no existe: se salta
        (sistema / "arial.ttf", sistema / "arialbd.ttf", "ArialPrueba"),
    ))
    mensajes: list = []
    fuentes = documentos._fuentes_pdf(mensajes.append)
    assert fuentes == {"regular": "ArialPrueba", "negrita": "ArialPrueba-Bold", "latin1": False,
                       "origen": str(sistema / "arial.ttf")}
    assert mensajes == []
    momentos = _momentos(2, tmp_path / "capturas")
    momentos[0].titulo = "Colimar a ≥ 12 cm → confirmar"
    _docx, pdf, paginas = _generar(tmp_path, momentos)
    assert paginas == _paginas_esperadas(momentos) and "Colimar a ≥ 12 cm → confirmar" in "".join(_texto_pdf(pdf))


def test_fuentes_fallback_helvetica(tmp_path, monkeypatch):
    """Último recurso (sin ninguna TrueType): Helvetica, símbolos legibles en el PDF y Unicode íntegro en el docx."""
    monkeypatch.setattr(config, "CARPETA_FUENTES", tmp_path / "sin_fuentes")
    monkeypatch.setattr(documentos, "FUENTES_SISTEMA", ())
    monkeypatch.setattr(documentos, "_aviso_fuentes_emitido", False)
    mensajes: list = []
    fuentes = documentos._fuentes_pdf(mensajes.append)
    assert fuentes == {"regular": "Helvetica", "negrita": "Helvetica-Bold", "latin1": True, "origen": None}
    assert len(mensajes) == 1 and "fuentes/" in mensajes[0] and "Helvetica" in mensajes[0]
    momentos = _momentos(2, tmp_path / "capturas")
    momentos[0].titulo = "Señal ¿lista? → sí"
    momentos[0].descripcion = "Colimar a ≥ 12 cm y verificar que el DAP marque ≤ 0,5 Gy·cm² ∪ listo"
    docx, pdf, paginas = _generar(tmp_path, momentos, log=mensajes.append)
    assert paginas == _paginas_esperadas(momentos)
    assert sum(1 for m in mensajes if "aviso" in m) == 1               # el aviso de fuentes se da una sola vez
    texto = "".join(_texto_pdf(pdf))
    assert "Señal ¿lista? -> sí" in texto and ">= 12 cm" in texto and "<= 0,5 Gy·cm²" in texto and "? listo" in texto
    todo = "\n".join(_celdas_docx(docx))
    assert "Señal ¿lista? → sí" in todo and "≥ 12 cm" in todo and "≤ 0,5" in todo    # el docx nunca sustituye


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
    assert any("A4 vertical 1x1" in m for m in mensajes)
