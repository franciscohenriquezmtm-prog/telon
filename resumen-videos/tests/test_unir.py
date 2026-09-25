"""Tests de ``unir.py`` y ``--unir``: varios videos procesados -> un manual con capítulos (sin API)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document
from PIL import Image

import resumir_videos as cli
from resumen_videos import config, pipeline, unir
from resumen_videos.modelos import InfoVideo, Momento, ResultadoAnalisis, Uso

pymupdf = pytest.importorskip("pymupdf")


def _captura(ruta: Path, color=(60, 70, 90)) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (640, 360), color).save(ruta, "JPEG", quality=85)
    return ruta


def _video_procesado(salida: Path, nombre: str, titulo: str, n: int, *, con_transcripcion: bool = True,
                     con_uso: bool = True) -> Path:
    """Simula ``salida/<nombre>/`` tal como lo deja el programa: momentos.json + capturas."""
    carpeta = salida / nombre
    momentos = []
    for i in range(n):
        base = carpeta / config.CARPETA_CAPTURAS / f"{i + 1:02d}_00-{10 * i:02d}.jpg"
        _captura(base)
        anotada = _captura(base.with_name(base.stem + "_anotada.jpg"), (200, 40, 40)) if i % 2 == 0 else None
        momentos.append(Momento(tiempo_seg=10.0 * i, titulo=f"{titulo} paso {i + 1}", descripcion=f"Descripción {i + 1}.",
                                seccion="Encendido" if i < n / 2 else "", ruta_captura=str(base),
                                ruta_captura_anotada=str(anotada) if anotada else None, zona={"x": 0.5, "y": 0.5}))
    analisis = ResultadoAnalisis(
        momentos=momentos, modo="gemini", modelo="gemini-x", titulo=titulo, resumen="r",
        uso=Uso(modelo="gemini-x", tokens_total=1000, llamadas=1, costo_usd=0.01) if con_uso else None,
        transcripcion=[{"inicio": 1.0, "fin": 3.0, "texto": f"Audio de {titulo}."}] if con_transcripcion else None,
        avisos=["aviso de prueba"])
    info = InfoVideo(ruta=carpeta / f"{nombre}.mov", nombre=nombre, duracion=100.0 * (n or 1), fps=30, ancho=1920,
                     alto=1080, tamano_bytes=1)
    pipeline.guardar_json(carpeta, info, analisis, extra={"documentos": {"docx": "x.docx", "pdf": "x.pdf", "paginas": 3}})
    return carpeta


def _texto_pdf(ruta: Path) -> list[str]:
    with pymupdf.open(str(ruta)) as doc:
        return [pagina.get_text() for pagina in doc]


def test_unir_dos_videos_en_un_manual_con_capitulos(tmp_path):
    salida = tmp_path / "salida"
    a = _video_procesado(salida, "TC_01_encendido", "Encendido del equipo", 3)
    b = _video_procesado(salida, "TC_02_worklist", "Worklist y envío", 2, con_uso=False)
    registro: list[str] = []
    docx, pdf, paginas, destino = unir.unir_manuales("Manual TC", [a, b], salida, equipo="tomógrafo", log=registro.append)
    assert destino == salida / "Manual TC" and docx.exists() and pdf.exists() and paginas >= 5
    datos = json.loads((destino / config.NOMBRE_JSON).read_text(encoding="utf-8"))
    an = datos["analisis"]
    assert an["modo"] == "unido" and an["titulo"] == "Manual de tomógrafo" and len(an["momentos"]) == 5
    secciones = [m["seccion"] for m in an["momentos"]]
    assert secciones[0] == "1. Encendido del equipo · Encendido" and secciones[-1] == "2. Worklist y envío · General"
    # tiempos relativos a cada video, en el orden de los capítulos
    assert [m["tiempo"] for m in an["momentos"]] == ["00:00", "00:10", "00:20", "00:00", "00:10"]
    # capturas copiadas a la carpeta del manual (limpia y anotada), con prefijo de capítulo
    copiadas = sorted(p.name for p in (destino / config.CARPETA_CAPTURAS).glob("*.jpg"))
    assert copiadas[0] == "01_01_00-00.jpg" and "01_01_00-00_anotada.jpg" in copiadas and "02_01_00-00.jpg" in copiadas
    assert all(Path(destino / m["captura"]).is_file() for m in an["momentos"])
    # transcripción encadenada con encabezado por capítulo
    assert an["transcripcion"][0] == {"encabezado": "Capítulo 1: Encendido del equipo"}
    assert an["transcripcion"][2] == {"encabezado": "Capítulo 2: Worklist y envío"}
    # uso: solo el capítulo que lo tiene; avisos con prefijo; video con capítulos y duración total
    assert an["uso"]["tokens_total"] == 1000 and an["avisos"] == ["Capítulo 1: aviso de prueba", "Capítulo 2: aviso de prueba"]
    assert datos["video"]["duracion"] == "08:20" and [c["numero"] for c in datos["video"]["extra"]["capitulos"]] == [1, 2]
    assert datos["documentos"]["pdf"] == "Manual TC.pdf" and datos["documentos"]["paginas"] == paginas
    textos = _texto_pdf(pdf)
    assert "1. Encendido del equipo" in textos[1] and "2. Worklist y envío" in textos[1]      # índice
    assert "Capítulo 2: Worklist y envío" in textos[-1] and "Audio de Encendido del equipo." in textos[-1]
    assert any("Capítulo 1: Encendido del equipo" in p.text for p in Document(str(docx)).paragraphs)
    assert registro[0].startswith("Capítulo 1: Encendido del equipo (3 pasos)")


def test_unir_titulo_y_resumen_propios_y_sin_transcripcion(tmp_path):
    salida = tmp_path / "salida"
    a = _video_procesado(salida, "uno", "Uno", 1, con_transcripcion=False)
    _docx, pdf, _paginas, destino = unir.unir_manuales("m", [a], salida, titulo="Mi manual", resumen="Resumen propio",
                                                        log=lambda _: None)
    an = json.loads((destino / config.NOMBRE_JSON).read_text(encoding="utf-8"))["analisis"]
    assert an["titulo"] == "Mi manual" and an["resumen"] == "Resumen propio" and an["transcripcion"] is None
    assert "Transcripción" not in "".join(_texto_pdf(pdf))


def test_unir_sin_carpetas_o_sin_json(tmp_path):
    with pytest.raises(ValueError):
        unir.unir_manuales("m", [], tmp_path, log=lambda _: None)
    (tmp_path / "vacia").mkdir()
    with pytest.raises(RuntimeError, match="momentos.json"):
        unir.unir_manuales("m", [tmp_path / "vacia"], tmp_path, log=lambda _: None)


def test_cli_unir_toma_los_procesados_en_orden_y_respeta_solo(tmp_path, monkeypatch, capsys):
    salida = tmp_path / "salida"
    _video_procesado(salida, "b_segundo", "Segundo", 1)
    _video_procesado(salida, "a_primero", "Primero", 1)
    (salida / "sin_json").mkdir()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "videos").mkdir()
    assert cli.main(["--unir", "Todo", "--salida", str(salida), "--titulo", "Manual completo"]) == 0
    an = json.loads((salida / "Todo" / config.NOMBRE_JSON).read_text(encoding="utf-8"))["analisis"]
    assert [m["seccion"] for m in an["momentos"]] == ["1. Primero · Encendido", "2. Segundo · Encendido"]
    assert an["titulo"] == "Manual completo"
    # una segunda unión no se incluye a sí misma como capítulo
    assert cli.main(["--unir", "Todo", "--salida", str(salida)]) == 0
    an = json.loads((salida / "Todo" / config.NOMBRE_JSON).read_text(encoding="utf-8"))["analisis"]
    assert len(an["momentos"]) == 2
    assert cli.main(["--unir", "Solo b", "--salida", str(salida), "--solo", "b_segundo"]) == 0
    an = json.loads((salida / "Solo b" / config.NOMBRE_JSON).read_text(encoding="utf-8"))["analisis"]
    assert [m["seccion"] for m in an["momentos"]] == ["1. Segundo · Encendido"]
    assert cli.main(["--unir", "Nada", "--salida", str(salida), "--solo", "no_existe"]) == 2
    assert "no hay videos procesados" in capsys.readouterr().err
