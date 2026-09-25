"""Tests de ``resumen_videos.pipeline`` y de la CLI.  Sin red: las funciones de Gemini se sustituyen por dobles."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from resumen_videos import config, documentos, gemini, local, pipeline, video
from resumen_videos.modelos import InfoVideo, Momento, ResultadoAnalisis, ResultadoVideo, Uso

RAIZ = Path(__file__).resolve().parents[1]
CLI = RAIZ / "resumir_videos.py"
MODELO = config.MODELO_POR_DEFECTO


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
@pytest.fixture
def carpeta_videos(tmp_path, video_prueba) -> Path:
    carpeta = tmp_path / "videos"
    carpeta.mkdir()
    shutil.copy(video_prueba, carpeta / "maquina.mp4")
    return carpeta


def opciones(carpeta_videos: Path, tmp_path: Path, **cambios) -> pipeline.Opciones:
    base = dict(carpeta_videos=carpeta_videos, carpeta_salida=tmp_path / "salida", modo="simulado")
    base.update(cambios)
    return pipeline.Opciones(**base)


def registro():
    lineas: list[str] = []
    return lineas, lineas.append


def momentos_falsos(duracion: float, n: int = 5) -> list[Momento]:
    paso = duracion / (n + 1)
    return [Momento(tiempo_seg=paso * (i + 1), titulo=f"Paso {i + 1}", descripcion=f"Descripción del paso {i + 1}.",
                    importancia=3 + i % 3, fuente="ambos", seccion="Preparación" if i < n // 2 else "Ejecución",
                    zona={"x": 0.2 + 0.1 * i, "y": 0.3 + 0.05 * i}) for i in range(n)]


def analisis_falso(info: InfoVideo, n: int = 5, modelo: str = MODELO, costo: float | None = 0.0025,
                   modo: str = "gemini") -> ResultadoAnalisis:
    uso = Uso(modelo=modelo, tokens_entrada=9000, tokens_salida=800, tokens_pensamiento=100, tokens_total=9900,
              detalle_entrada={"VIDEO": 8500, "AUDIO": 400, "TEXT": 100}, llamadas=1, batch=modo == "gemini-batch",
              costo_usd=costo)
    return ResultadoAnalisis(momentos=momentos_falsos(info.duracion, n), modo=modo, modelo=modelo,
                             resumen="El video enseña a operar el equipo.", uso=uso, titulo="Manual de prueba")


class DoblesGemini:
    """Sustituye las funciones de red de ``gemini.py`` y registra cada llamada en ``llamadas``."""

    rotar_primero = False      # True: el refinado devuelve rotacion=90 para el primer paso

    def __init__(self, monkeypatch, n: int = 5, fallo: Exception | None = None):
        self.llamadas: list[tuple] = []
        self.conservar: list[bool] = []            # conservar_subida recibido en cada subida
        self.temperaturas: list = []               # temperatura recibida en cada llamada a la API
        self.n = n
        self.fallo = fallo
        monkeypatch.setattr(gemini, "crear_cliente", lambda api_key, timeout_ms=0: SimpleNamespace(clave=api_key))
        monkeypatch.setattr(gemini, "subir_video", self.subir_video)
        monkeypatch.setattr(gemini, "analizar_video", self.analizar_video)
        monkeypatch.setattr(gemini, "eliminar_archivo", self.eliminar_archivo)
        monkeypatch.setattr(gemini, "refinar_con_capturas", self.refinar_con_capturas, raising=False)
        monkeypatch.setattr(gemini, "pulir_redaccion", self.pulir_redaccion)

    def de_tipo(self, tipo: str) -> list[tuple]:
        return [l for l in self.llamadas if l[0] == tipo]

    def subir_video(self, cliente, ruta, nombre, timeout_procesado=0, intervalo=0, *, conservar_subida=False, log=print):
        ruta = Path(ruta)
        assert ruta.is_file(), "se intenta subir un archivo que no existe"
        self.llamadas.append(("subir", ruta, nombre))
        self.conservar.append(conservar_subida)
        log(f"(doble) subido {ruta.name}")
        return types.File(name=f"files/{nombre}", uri=f"https://x/files/{nombre}", mime_type="video/mp4",
                          state=types.FileState.ACTIVE)

    def analizar_video(self, cliente, archivo, info, modelo=MODELO, **kw):
        self.llamadas.append(("analizar", archivo.name, dict(kw, modelo=modelo)))
        self.temperaturas.append(kw.get("temperatura"))
        if self.fallo is not None:
            raise self.fallo
        kw["log"](f"(doble) análisis de {info.nombre}")
        return analisis_falso(info, self.n, modelo)

    def eliminar_archivo(self, cliente, archivo, *, log=print):
        self.llamadas.append(("eliminar", archivo if isinstance(archivo, str) else archivo.name))

    def refinar_con_capturas(self, cliente, resultado, modelo, precios=None, equipo="", max_lado_px=1280, lote=15, *,
                             temperatura=None, log=print):
        # En este punto las capturas ya existen y todavía no hay anotaciones (orden de la especificación).
        capturas = [m.ruta_captura for m in resultado.momentos]
        assert all(c and Path(c).is_file() for c in capturas), "el refinado debe llegar tras las capturas"
        assert all(m.ruta_captura_anotada is None for m in resultado.momentos), "las anotaciones van después"
        assert not any(m.titulo.endswith("(pulido)") for m in resultado.momentos), "el refinado va antes que el redactor"
        self.llamadas.append(("refinar", modelo, len(capturas)))
        self.temperaturas.append(temperatura)
        nuevos = [replace(m, titulo=m.titulo + " (refinado)", rotacion=90 if (i == 0 and self.rotar_primero) else 0)
                  for i, m in enumerate(resultado.momentos)]
        uso = Uso(modelo=f"{modelo} (+refinado)", batch=resultado.uso.batch if resultado.uso else False)
        if resultado.uso is not None:        # como el real: con --solo-refinado el análisis previo no se vuelve a sumar
            uso.sumar(resultado.uso)
        uso.sumar(Uso(modelo=modelo, tokens_entrada=1500, tokens_salida=300, tokens_total=1800, llamadas=1, costo_usd=0.0008))
        return replace(resultado, momentos=nuevos, uso=uso)

    def pulir_redaccion(self, cliente, resultado, modelo_redactor, precios=None, *, temperatura=None, log=print):
        # G5: el redactor va DESPUÉS del refinado y de las capturas, y antes de las anotaciones.
        assert all(m.ruta_captura and Path(m.ruta_captura).is_file() for m in resultado.momentos), "el redactor va tras las capturas"
        assert all(m.ruta_captura_anotada is None for m in resultado.momentos), "las anotaciones van después del redactor"
        self.llamadas.append(("redactor", modelo_redactor))
        self.temperaturas.append(temperatura)
        nuevos = [replace(m, titulo=m.titulo + " (pulido)") for m in resultado.momentos]
        return replace(resultado, momentos=nuevos, resumen="Resumen pulido.")


def paginas_esperadas(momentos: list, por_pagina="auto", incluir_indice: bool = True) -> int:
    layout = documentos.calcular_layout(len(momentos), por_pagina)
    indice = documentos.contar_paginas_indice(momentos, por_pagina) if incluir_indice else 0
    return 1 + indice + math.ceil(len(momentos) / layout.por_pagina)


def comprobar_salida(carpeta: Path, nombre: str, r: ResultadoVideo) -> dict:
    """Comprueba la estructura ``salida/<nombre>/`` y devuelve el ``momentos.json`` leído."""
    assert r.exito and r.error is None, r.error
    assert carpeta == r.carpeta_salida and carpeta.name == nombre
    datos = json.loads((carpeta / config.NOMBRE_JSON).read_text(encoding="utf-8"))
    assert datos["video"]["nombre"] == nombre and datos["version"]
    assert datos["documentos"] == {"docx": f"{nombre}.docx", "pdf": f"{nombre}.pdf", "paginas": r.paginas_pdf}
    assert (carpeta / f"{nombre}.docx").is_file() and (carpeta / f"{nombre}.pdf").is_file()
    assert r.ruta_docx == carpeta / f"{nombre}.docx" and r.ruta_pdf == carpeta / f"{nombre}.pdf"
    assert not (carpeta / config.NOMBRE_ERROR).exists()
    for m in datos["analisis"]["momentos"]:
        assert m["captura"] and not Path(m["captura"]).is_absolute()     # relativa a la carpeta
        assert (carpeta / m["captura"]).is_file()
    return datos


# ----------------------------------------------------------------------------
# Modo simulado (circuito completo real: ffmpeg, Pillow, docx, pdf)
# ----------------------------------------------------------------------------
class TestSimulado:
    def test_procesar_carpeta_simulado(self, carpeta_videos, tmp_path):
        lineas, log = registro()
        op = opciones(carpeta_videos, tmp_path)
        resumen = pipeline.procesar_carpeta(op, log=log)
        assert len(resumen.resultados) == 1 and resumen.uso_total is None
        r = resumen.resultados[0]
        carpeta = tmp_path / "salida" / "maquina"
        datos = comprobar_salida(carpeta, "maquina", r)
        assert datos["analisis"]["modo"] == "simulado"
        n = len(r.analisis.momentos)
        assert n >= 5
        capturas = sorted((carpeta / config.CARPETA_CAPTURAS).glob("*.jpg"))
        anotadas = [c for c in capturas if c.name.endswith("_anotada.jpg")]
        assert len(capturas) - len(anotadas) == n and len(anotadas) >= 1
        assert all(len(c.name.split("_")[0]) == 2 for c in capturas)      # NN_mm-ss.jpg
        assert r.paginas_pdf == paginas_esperadas(r.analisis.momentos) == documentos.contar_paginas_pdf(r.ruta_pdf)
        # log.txt con hora en cada línea, y las mismas líneas en consola
        contenido = (carpeta / config.NOMBRE_LOG).read_text(encoding="utf-8").splitlines()
        assert contenido and all(len(l) > 9 and l[2] == ":" and l[5] == ":" and l[8] == " " for l in contenido)
        assert any("momentos.json guardado" in l for l in contenido)
        assert all(l in lineas for l in contenido)
        tabla = resumen.tabla()
        assert "sin API" in tabla and "| OK" in tabla and "1 OK, 0 con error" in tabla

    def test_ya_procesado_omitido_y_forzar(self, carpeta_videos, tmp_path, monkeypatch):
        llamadas = []
        original = local.analizar_simulado
        monkeypatch.setattr(local, "analizar_simulado", lambda info, *, log=print: (llamadas.append(info.nombre), original(info, log=log))[1])
        op = opciones(carpeta_videos, tmp_path)
        primero = pipeline.procesar_carpeta(op).resultados[0]
        assert llamadas == ["maquina"] and not primero.omitido
        lineas, log = registro()
        segundo = pipeline.procesar_carpeta(op, log=log).resultados[0]
        assert segundo.omitido and segundo.exito and llamadas == ["maquina"]
        assert len(segundo.analisis.momentos) == len(primero.analisis.momentos)
        assert segundo.paginas_pdf == primero.paginas_pdf and segundo.ruta_pdf == primero.ruta_pdf
        assert any("ya procesado" in l.lower() and "--forzar" in l for l in lineas)
        assert "ya procesado (omitido)" in pipeline.ResumenEjecucion([segundo], None, 0.1).tabla()
        tercero = pipeline.procesar_carpeta(replace(op, forzar=True)).resultados[0]
        assert not tercero.omitido and tercero.exito and llamadas == ["maquina", "maquina"]

    def test_video_corrupto_no_detiene_al_resto(self, carpeta_videos, tmp_path):
        (carpeta_videos / "malo.mp4").write_text("esto no es un video", encoding="utf-8")
        resumen = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path))
        por_nombre = {r.info.nombre: r for r in resumen.resultados}
        assert set(por_nombre) == {"malo", "maquina"}
        malo = por_nombre["malo"]
        assert not malo.exito and malo.error and malo.analisis is None
        error = (tmp_path / "salida" / "malo" / config.NOMBRE_ERROR).read_text(encoding="utf-8")
        assert "malo.mp4" in error and "Traceback" in error and malo.error in error
        comprobar_salida(tmp_path / "salida" / "maquina", "maquina", por_nombre["maquina"])
        assert len(resumen.fallidos()) == 1 and len(resumen.exitosos()) == 1
        assert "ERROR:" in resumen.tabla() and "1 OK, 1 con error" in resumen.tabla()

    def test_regenerar_reutiliza_json_editado(self, carpeta_videos, tmp_path, monkeypatch):
        op = opciones(carpeta_videos, tmp_path)
        primero = pipeline.procesar_carpeta(op).resultados[0]
        carpeta = primero.carpeta_salida
        ruta_json = carpeta / config.NOMBRE_JSON
        datos = json.loads(ruta_json.read_text(encoding="utf-8"))
        datos["analisis"]["momentos"][0]["titulo"] = "Título corregido a mano"
        datos["analisis"]["momentos"][0]["zona"] = {"x": 0.5, "y": 0.5}
        del datos["analisis"]["momentos"][-1]
        ruta_json.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        n = len(datos["analisis"]["momentos"])

        def no_analizar(*a, **k):
            raise AssertionError("con --regenerar no se debe volver a analizar")
        monkeypatch.setattr(local, "analizar_simulado", no_analizar)
        lineas, log = registro()
        r = pipeline.procesar_carpeta(replace(op, regenerar=True), log=log).resultados[0]
        nuevo = comprobar_salida(carpeta, "maquina", r)
        assert not r.omitido and len(r.analisis.momentos) == n
        assert nuevo["analisis"]["momentos"][0]["titulo"] == "Título corregido a mano"
        assert nuevo["analisis"]["momentos"][0]["captura_anotada"]
        assert any("regenerar" in l for l in lineas) and any("Regenerado desde" in a for a in nuevo["analisis"]["avisos"])
        capturas = [c for c in (carpeta / config.CARPETA_CAPTURAS).glob("*.jpg") if "_anotada" not in c.name]
        assert len(capturas) == n
        assert r.paginas_pdf == paginas_esperadas(r.analisis.momentos)

    def test_regenerar_con_json_roto(self, carpeta_videos, tmp_path):
        carpeta = tmp_path / "salida" / "maquina"
        carpeta.mkdir(parents=True)
        (carpeta / config.NOMBRE_JSON).write_text("{esto no es json", encoding="utf-8")
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, regenerar=True)).resultados[0]
        assert not r.exito and config.NOMBRE_JSON in r.error
        assert (carpeta / config.NOMBRE_ERROR).is_file()

    def test_regenerar_exige_momentos_json(self, carpeta_videos, tmp_path, monkeypatch):
        """U1: --regenerar nunca analiza; sin momentos.json es un error claro."""
        def no_analizar(*a, **k):
            raise AssertionError("con --regenerar no se debe analizar")
        monkeypatch.setattr(local, "analizar_simulado", no_analizar)
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, regenerar=True)).resultados[0]
        assert not r.exito and "--regenerar" in r.error and config.NOMBRE_JSON in r.error

    def test_regenerar_y_forzar_incompatibles(self, carpeta_videos, tmp_path):
        with pytest.raises(ValueError, match="incompatibles"):
            pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, regenerar=True, forzar=True))

    def test_regenerar_aplica_filtros_sin_perder_momentos(self, carpeta_videos, tmp_path):
        """H3: --max-momentos e --importancia-minima actúan con --regenerar; lo apartado queda en el JSON."""
        op = opciones(carpeta_videos, tmp_path)
        primero = pipeline.procesar_carpeta(op).resultados[0]
        todos = [m.tiempo_seg for m in primero.analisis.momentos]
        assert len(todos) >= 5
        lineas, log = registro()
        r = pipeline.procesar_carpeta(replace(op, regenerar=True, max_momentos=2, importancia_minima=3), log=log).resultados[0]
        datos = comprobar_salida(r.carpeta_salida, "maquina", r)
        assert len(r.analisis.momentos) == 2 and all(m.importancia >= 3 for m in r.analisis.momentos)
        assert r.paginas_pdf == paginas_esperadas(r.analisis.momentos)
        assert len(datos["analisis"]["momentos"]) == 2
        descartados = datos["analisis"]["momentos_descartados"]
        assert len(descartados) == len(todos) - 2 and all(m["captura"] is None for m in descartados)
        assert sorted(m["tiempo_seg"] for m in datos["analisis"]["momentos"] + descartados) == todos
        assert any("--max-momentos" in l for l in lineas) and any("momentos_descartados" in a for a in datos["analisis"]["avisos"])
        capturas = [c for c in (r.carpeta_salida / config.CARPETA_CAPTURAS).glob("*.jpg") if "_anotada" not in c.name]
        assert len(capturas) == 2
        # regenerar de nuevo sin filtros recupera el análisis completo
        r2 = pipeline.procesar_carpeta(replace(op, regenerar=True)).resultados[0]
        datos2 = comprobar_salida(r2.carpeta_salida, "maquina", r2)
        assert [m.tiempo_seg for m in r2.analisis.momentos] == todos and "momentos_descartados" not in datos2["analisis"]

    def test_fallo_tras_el_json_se_retoma(self, carpeta_videos, tmp_path, monkeypatch):
        """P-H4: momentos.json sin documentos no es "ya procesado": la siguiente ejecución retoma desde el JSON."""
        op = opciones(carpeta_videos, tmp_path)

        def roto(*a, **k):
            raise RuntimeError("fuente no encontrada")
        monkeypatch.setattr(documentos, "generar_documentos", roto)
        r1 = pipeline.procesar_carpeta(op).resultados[0]
        carpeta = r1.carpeta_salida
        assert not r1.exito and (carpeta / config.NOMBRE_JSON).is_file() and (carpeta / config.NOMBRE_ERROR).is_file()
        monkeypatch.undo()
        llamadas = []
        original = local.analizar_simulado
        monkeypatch.setattr(local, "analizar_simulado", lambda info, *, log=print: (llamadas.append(1), original(info, log=log))[1])
        lineas, log = registro()
        resumen = pipeline.procesar_carpeta(op, log=log)
        r2 = resumen.resultados[0]
        datos = comprobar_salida(carpeta, "maquina", r2)
        assert not r2.omitido and llamadas == [] and r2.uso_ejecucion is None
        assert any("se retoma" in l for l in lineas) and any("Retomado desde" in a for a in datos["analisis"]["avisos"])
        assert "| OK" in resumen.tabla() and "omitido" not in resumen.tabla().splitlines()[2]
        # ahora sí está completo: la tercera ejecución lo omite
        assert pipeline.procesar_carpeta(op).resultados[0].omitido

    def test_opciones_de_documento(self, carpeta_videos, tmp_path):
        op = opciones(carpeta_videos, tmp_path, por_pagina=4, incluir_indice=False, anotar=False)
        r = pipeline.procesar_carpeta(op).resultados[0]
        comprobar_salida(r.carpeta_salida, "maquina", r)
        assert r.paginas_pdf == paginas_esperadas(r.analisis.momentos, 4, incluir_indice=False)
        assert not list((r.carpeta_salida / config.CARPETA_CAPTURAS).glob("*_anotada.jpg"))
        assert all(m.ruta_captura_anotada is None for m in r.analisis.momentos)


# ----------------------------------------------------------------------------
# Modo gemini con dobles
# ----------------------------------------------------------------------------
class TestGeminiConDobles:
    def test_flujo_completo_con_refinado(self, carpeta_videos, tmp_path, monkeypatch):
        dobles = DoblesGemini(monkeypatch)
        lineas, log = registro()
        op = opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave-falsa", equipo="arco en C",
                      resolucion="alta", max_momentos=7, tramo_min=30)
        resumen = pipeline.procesar_carpeta(op, log=log)
        r = resumen.resultados[0]
        carpeta = tmp_path / "salida" / "maquina"
        datos = comprobar_salida(carpeta, "maquina", r)
        # orden de llamadas: subir → analizar → eliminar → (capturas) → refinar
        assert [l[0] for l in dobles.llamadas] == ["subir", "analizar", "eliminar", "refinar"]
        assert dobles.llamadas[0][1] == (carpeta_videos / "maquina.mp4").resolve()     # mp4 pequeño: se sube tal cual
        kw = dobles.llamadas[1][2]
        assert kw["resolucion"] == "alta" and kw["equipo"] == "arco en C" and kw["max_momentos"] == 7
        assert kw["tramo_max_seg"] == 30 * 60 and kw["modelo"] == MODELO and kw["precios"] is None
        assert dobles.llamadas[2][1] == "files/maquina"
        # el refinado cambió los títulos, y las anotaciones se hicieron después sobre las capturas
        assert all(m.titulo.endswith("(refinado)") for m in r.analisis.momentos)
        assert all(m["titulo"].endswith("(refinado)") and m["captura_anotada"] for m in datos["analisis"]["momentos"])
        assert len(list((carpeta / config.CARPETA_CAPTURAS).glob("*_anotada.jpg"))) == 5
        # uso y costo (análisis + refinado) en el JSON y en el resumen
        uso = datos["analisis"]["uso"]
        assert uso["tokens_total"] == 9900 + 1800 and uso["costo_usd"] == round(0.0025 + 0.0008, 4)
        assert uso["modelo"].endswith("(+refinado)")
        assert resumen.uso_total is not None and resumen.uso_total.tokens_total == 11700
        assert abs(resumen.uso_total.costo_usd - 0.0033) < 1e-9
        tabla = resumen.tabla()
        assert "US$ 0.0033" in tabla and "11700" in tabla
        assert "ESTIMACIÓN de costo de esta ejecución: US$ 0.0033 (11700 tokens)" in tabla
        assert "acumulado de estos videos en todas las ejecuciones: US$ 0.0033 (11700 tokens)" in tabla
        # P-H5: esta ejecución y acumulado del video (primer análisis: iguales), también en el JSON
        assert r.uso_ejecucion.tokens_total == 11700 and r.uso_acumulado.tokens_total == 11700
        assert datos["uso_acumulado"]["tokens_total"] == 11700 and datos["uso_acumulado"]["costo_usd"] == 0.0033
        assert dobles.temperaturas == [None, None] and dobles.conservar == [False]      # G10 por defecto, G9
        assert r.paginas_pdf == paginas_esperadas(r.analisis.momentos)

    def test_sin_refinado_y_redactor(self, carpeta_videos, tmp_path, monkeypatch):
        dobles = DoblesGemini(monkeypatch)
        op = opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave", refinar=False, redactor="modelo-pro",
                      conservar_subida=True, temperatura=0.3)
        r = pipeline.procesar_carpeta(op).resultados[0]
        assert r.exito
        assert [l[0] for l in dobles.llamadas] == ["subir", "analizar", "redactor"]
        assert dobles.llamadas[2][1] == "modelo-pro" and r.analisis.resumen == "Resumen pulido."
        assert not any(m.titulo.endswith("(refinado)") for m in r.analisis.momentos)
        assert dobles.temperaturas == [0.3, 0.3] and dobles.conservar == [True]      # --temperatura llega a todo

    def test_refinado_antes_que_redactor(self, carpeta_videos, tmp_path, monkeypatch):
        """G5: análisis → capturas → refinado → redactor → anotaciones → documentos."""
        dobles = DoblesGemini(monkeypatch)
        op = opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave", redactor="modelo-pro")
        r = pipeline.procesar_carpeta(op).resultados[0]
        assert r.exito, r.error
        assert [l[0] for l in dobles.llamadas] == ["subir", "analizar", "eliminar", "refinar", "redactor"]
        assert all(m.titulo.endswith("(refinado) (pulido)") for m in r.analisis.momentos)
        assert all(m.ruta_captura_anotada for m in r.analisis.momentos) and r.analisis.resumen == "Resumen pulido."
        datos = json.loads((r.carpeta_salida / config.NOMBRE_JSON).read_text(encoding="utf-8"))
        assert all(m["titulo"].endswith("(pulido)") for m in datos["analisis"]["momentos"])

    def test_refinado_que_falla_conserva_el_analisis(self, carpeta_videos, tmp_path, monkeypatch):
        dobles = DoblesGemini(monkeypatch)

        def refinar_roto(*a, **k):
            raise RuntimeError("cuota del refinado agotada")
        monkeypatch.setattr(gemini, "refinar_con_capturas", refinar_roto, raising=False)
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave")).resultados[0]
        assert r.exito and len(r.analisis.momentos) == 5
        assert any("cuota del refinado agotada" in a for a in r.analisis.avisos)
        assert dobles.de_tipo("eliminar")

    def test_error_de_api_deja_error_txt_y_borra_el_remoto(self, carpeta_videos, tmp_path, monkeypatch):
        fallo = errors.APIError(429, {"error": {"code": 429, "message": "cuota agotada", "status": "RESOURCE_EXHAUSTED"}})
        dobles = DoblesGemini(monkeypatch, fallo=fallo)
        resumen = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave"))
        r = resumen.resultados[0]
        assert not r.exito and "429" in r.error and "cuota agotada" in r.error and "RESOURCE_EXHAUSTED" in r.error
        assert "--pausa" in r.error
        error = (tmp_path / "salida" / "maquina" / config.NOMBRE_ERROR).read_text(encoding="utf-8")
        assert "429" in error and "APIError" in error
        assert dobles.de_tipo("eliminar") == [("eliminar", "files/maquina")]      # finally
        assert not (tmp_path / "salida" / "maquina" / config.NOMBRE_JSON).exists()
        assert resumen.uso_total is None and "ERROR: Error de la API de Gemini (429" in resumen.tabla()

    def test_sin_clave_en_modo_gemini(self, carpeta_videos, tmp_path, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini"))

    def test_regenerar_nunca_llama_a_la_api(self, carpeta_videos, tmp_path, monkeypatch):
        """U1 / P-H2: con clave presente, --regenerar no refina ni analiza; respeta correcciones a mano."""
        dobles = DoblesGemini(monkeypatch)
        op = opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave", redactor="modelo-pro")
        r1 = pipeline.procesar_carpeta(op).resultados[0]
        carpeta = r1.carpeta_salida
        ruta_json = carpeta / config.NOMBRE_JSON
        datos = json.loads(ruta_json.read_text(encoding="utf-8"))
        datos["analisis"]["momentos"][0]["titulo"] = "Título corregido a mano"
        ruta_json.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        dobles.llamadas.clear()
        lineas, log = registro()
        resumen = pipeline.procesar_carpeta(replace(op, regenerar=True), log=log)
        r2 = resumen.resultados[0]
        nuevo = comprobar_salida(carpeta, "maquina", r2)
        assert dobles.llamadas == [] and not r2.omitido
        assert nuevo["analisis"]["momentos"][0]["titulo"] == "Título corregido a mano"
        assert any("no se llama a la API" in l for l in lineas)
        # P-H5: esta ejecución no costó nada; el acumulado del video es el del análisis original (no crece)
        assert resumen.uso_total is None and r2.uso_ejecucion is None
        assert r2.uso_acumulado.tokens_total == 11700 and nuevo["uso_acumulado"]["tokens_total"] == 11700
        assert nuevo["analisis"]["uso"]["tokens_total"] == 11700
        tabla = resumen.tabla()
        assert "esta ejecución: US$ 0.0000 (sin API)" in tabla and "todas las ejecuciones: US$ 0.0033" in tabla
        fila = [c.strip() for c in tabla.splitlines()[2].split("|")]
        assert fila[3] == "-" and fila[4] == "-" and fila[5] == "US$ 0.0033" and fila[6] == "OK"
        # tercera regeneración: sigue sin API y sin acumular
        r3 = pipeline.procesar_carpeta(replace(op, regenerar=True)).resultados[0]
        assert dobles.llamadas == [] and r3.uso_acumulado.tokens_total == 11700

    def test_regenerar_de_un_analisis_local_no_sube_capturas(self, carpeta_videos, tmp_path, monkeypatch):
        """P-H2 (privacidad): análisis --local y luego --regenerar en modo gemini con clave → ninguna llamada."""
        dobles = DoblesGemini(monkeypatch)
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="local", whisper_modelo=None))
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave", regenerar=True)).resultados[0]
        assert r.exito and dobles.llamadas == [] and r.analisis.modo == "local"

    def test_forzar_acumula_lo_ya_pagado(self, carpeta_videos, tmp_path, monkeypatch):
        """P-H5: al volver a analizar con --forzar, el acumulado del video suma las dos ejecuciones."""
        DoblesGemini(monkeypatch)
        op = opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave")
        pipeline.procesar_carpeta(op)
        resumen = pipeline.procesar_carpeta(replace(op, forzar=True))
        r = resumen.resultados[0]
        assert r.uso_ejecucion.tokens_total == 11700 and r.uso_acumulado.tokens_total == 23400
        assert abs(r.uso_acumulado.costo_usd - 0.0066) < 1e-9
        datos = json.loads((r.carpeta_salida / config.NOMBRE_JSON).read_text(encoding="utf-8"))
        assert datos["uso_acumulado"]["tokens_total"] == 23400 and datos["analisis"]["uso"]["tokens_total"] == 11700
        assert "esta ejecución: US$ 0.0033" in resumen.tabla() and "todas las ejecuciones: US$ 0.0066" in resumen.tabla()
        omitido = pipeline.procesar_carpeta(op).resultados[0]
        assert omitido.omitido and omitido.uso_ejecucion is None and omitido.uso_acumulado.tokens_total == 23400

    def test_regenerar_sin_clave_no_falla(self, carpeta_videos, tmp_path, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path))          # simulado: crea momentos.json
        lineas, log = registro()
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", regenerar=True), log=log).resultados[0]
        assert r.exito and not r.omitido
        assert any("sin refinado" in l for l in lineas)


class TestSubida:
    @pytest.fixture
    def transcodificar_falso(self, monkeypatch):
        llamadas = []

        def doble(ruta_video, destino_mp4, ffmpeg, alto=config.TRANSCODIFICAR_ALTO, fps=config.TRANSCODIFICAR_FPS, *, log=print):
            llamadas.append((Path(ruta_video), Path(destino_mp4), alto, fps))
            shutil.copy(ruta_video, destino_mp4)
            return Path(destino_mp4)
        monkeypatch.setattr(video, "transcodificar_para_subida", doble)
        return llamadas

    def test_mov_se_sube_como_copia_ligera_temporal(self, carpeta_videos, tmp_path, monkeypatch, transcodificar_falso):
        (carpeta_videos / "maquina.mp4").rename(carpeta_videos / "iphone.MOV")
        dobles = DoblesGemini(monkeypatch)
        lineas, log = registro()
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave", copia_alto=480),
                                      log=log).resultados[0]
        assert r.exito
        assert len(transcodificar_falso) == 1
        origen, destino, alto, _fps = transcodificar_falso[0]
        assert origen == (carpeta_videos / "iphone.MOV").resolve() and alto == 480
        subida = dobles.de_tipo("subir")[0][1]
        assert subida == destino and subida.name == "iphone_subida.mp4" and subida != origen
        assert not destino.exists() and not destino.parent.exists()          # temporal borrado al terminar
        assert any("copia ligera" in l and "480p" in l for l in lineas)
        # las capturas salen del ORIGINAL
        assert all(Path(m.ruta_captura).is_file() for m in r.analisis.momentos)

    def test_subir_original(self, carpeta_videos, tmp_path, monkeypatch, transcodificar_falso):
        (carpeta_videos / "maquina.mp4").rename(carpeta_videos / "iphone.mov")
        dobles = DoblesGemini(monkeypatch)
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave",
                                               subir_original=True)).resultados[0]
        assert r.exito and transcodificar_falso == []
        assert dobles.de_tipo("subir")[0][1] == (carpeta_videos / "iphone.mov").resolve()

    def test_subir_original_demasiado_grande_usa_copia(self, carpeta_videos, tmp_path, monkeypatch, transcodificar_falso):
        dobles = DoblesGemini(monkeypatch)
        lineas, log = registro()
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave",
                                               subir_original=True, max_subida_mb=0), log=log).resultados[0]
        assert not r.exito and "límite de subida" in r.error          # la copia tampoco cabe en 0 MB
        assert len(transcodificar_falso) == 1 and dobles.de_tipo("subir") == []
        assert any("--subir-original no aplicable" in l for l in lineas)

    def test_mp4_grande_se_transcodifica(self, carpeta_videos, tmp_path, monkeypatch, transcodificar_falso):
        monkeypatch.setattr(config, "UMBRAL_TRANSCODIFICAR_MB", 0)
        dobles = DoblesGemini(monkeypatch)
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave")).resultados[0]
        assert r.exito and len(transcodificar_falso) == 1
        assert transcodificar_falso[0][2] == config.TRANSCODIFICAR_ALTO
        assert dobles.de_tipo("subir")[0][1].name == "maquina_subida.mp4"


# ----------------------------------------------------------------------------
# Modo batch
# ----------------------------------------------------------------------------
class TestBatch:
    @pytest.fixture
    def lote(self, monkeypatch):
        dobles = DoblesGemini(monkeypatch)
        estado = {"state": types.JobState.JOB_STATE_RUNNING, "consultas": 0, "lotes": 0}

        def enviar_lote(cliente, peticiones, modelo, nombre_lote, *, equipo="", fps=None, resolucion="", temperatura=None,
                        opciones_lote=None, log=print):
            dobles.llamadas.append(("lote", [(p["info"].nombre, p["tramo"]) for p in peticiones], modelo, resolucion, equipo,
                                    [p.get("clave") for p in peticiones], opciones_lote))
            estado["lotes"] += 1
            return types.BatchJob(name=f"batches/abc-{123 * estado['lotes']}", state=types.JobState.JOB_STATE_PENDING,
                                  model=f"models/{modelo}")

        def estado_lote(cliente, nombre_job):
            estado["consultas"] += 1
            assert nombre_job.startswith("batches/abc-")
            return types.BatchJob(name=nombre_job, state=estado["state"])

        def recoger_lote(cliente, job, mapa_videos, precios=None, *, log=print):
            dobles.llamadas.append(("recoger", sorted(mapa_videos), precios))
            return {nombre: analisis_falso(e["info"], modo="gemini-batch", costo=0.001) for nombre, e in mapa_videos.items()}
        monkeypatch.setattr(gemini, "enviar_lote", enviar_lote)
        monkeypatch.setattr(gemini, "estado_lote", estado_lote)
        monkeypatch.setattr(gemini, "recoger_lote", recoger_lote)
        return dobles, estado

    def test_envio_y_recogida(self, carpeta_videos, tmp_path, lote):
        dobles, estado = lote
        shutil.copy(carpeta_videos / "maquina.mp4", carpeta_videos / "segundo.mp4")
        lineas, log = registro()
        op = opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave", equipo="arco en C", resolucion="baja")
        resumen = pipeline.procesar_carpeta(op, log=log)
        assert [l[0] for l in dobles.llamadas] == ["subir", "subir", "lote"]
        assert dobles.llamadas[2][1] == [("maquina", None), ("segundo", None)]
        assert dobles.llamadas[2][3] == "baja" and dobles.llamadas[2][4] == "arco en C"
        assert all(r.exito and r.analisis is None for r in resumen.resultados)
        assert "pendiente (lote)" in resumen.tabla() and resumen.uso_total is None
        ruta_lote = tmp_path / "salida" / config.CARPETA_LOTES / "abc-123.json"
        datos = json.loads(ruta_lote.read_text(encoding="utf-8"))
        assert datos["job"] == "batches/abc-123" and datos["modelo"] == MODELO and "recogido" not in datos
        assert [v["nombre"] for v in datos["videos"]] == ["maquina", "segundo"]
        assert datos["videos"][0]["archivo"] == "files/maquina" and datos["videos"][0]["tramos"] == []
        assert datos["videos"][0]["info"]["duracion_seg"] > 80
        assert not (tmp_path / "salida" / "maquina" / config.NOMBRE_JSON).exists()
        assert any("--batch-recoger abc-123" in l for l in lineas)

        # recoger sin --esperar: todavía en marcha
        op2 = opciones(carpeta_videos, tmp_path, modo="batch-recoger", lote_id="abc-123", api_key="clave")
        lineas2, log2 = registro()
        pendiente = pipeline.procesar_carpeta(op2, log=log2)
        assert all(r.exito and r.analisis is None for r in pendiente.resultados) and len(pendiente.resultados) == 2
        assert any("--esperar" in l for l in lineas2) and not dobles.de_tipo("recoger")

        # terminado: capturas + documentos y borrado de los archivos remotos
        estado["state"] = types.JobState.JOB_STATE_SUCCEEDED
        listo = pipeline.procesar_carpeta(op2)
        assert len(listo.resultados) == 2 and all(r.exito for r in listo.resultados)
        for r in listo.resultados:
            datos_video = comprobar_salida(tmp_path / "salida" / r.info.nombre, r.info.nombre, r)
            assert datos_video["analisis"]["modo"] == "gemini-batch" and datos_video["analisis"]["uso"]["batch"] is True
        assert dobles.de_tipo("recoger")[0][1] == ["maquina", "segundo"]
        assert sorted(l[1] for l in dobles.de_tipo("eliminar")) == ["files/maquina", "files/segundo"]
        assert listo.uso_total.batch and "batch" in listo.tabla()
        assert "recogido" in json.loads(ruta_lote.read_text(encoding="utf-8"))
        assert "escalera" in datos and datos["escalera"]["thinking"] is True and "nota" in datos["escalera"]   # G7
        assert isinstance(dobles.llamadas[2][6], gemini.OpcionesLote) and dobles.llamadas[2][5] == ["maquina", "segundo"]

        # P-H6: recoger otra vez el mismo lote no repite refinado, capturas ni borrados, y respeta el JSON
        ruta_json = tmp_path / "salida" / "maquina" / config.NOMBRE_JSON
        editado = json.loads(ruta_json.read_text(encoding="utf-8"))
        editado["analisis"]["momentos"][0]["titulo"] = "Corregido a mano"
        ruta_json.write_text(json.dumps(editado, ensure_ascii=False), encoding="utf-8")
        dobles.llamadas.clear()
        lineas3, log3 = registro()
        repetido = pipeline.procesar_carpeta(op2, log=log3)
        assert [l[0] for l in dobles.llamadas] == ["recoger"]
        assert all(r.omitido and r.exito for r in repetido.resultados) and repetido.uso_total is None
        assert repetido.uso_acumulado is not None and repetido.uso_acumulado.batch
        assert json.loads(ruta_json.read_text(encoding="utf-8"))["analisis"]["momentos"][0]["titulo"] == "Corregido a mano"
        assert any("ya se recogió" in l for l in lineas3)
        # con --forzar sí se rehace desde el lote (refinado incluido)
        dobles.llamadas.clear()
        forzado = pipeline.procesar_carpeta(replace(op2, forzar=True))
        assert [l[0] for l in dobles.llamadas] == ["recoger", "refinar", "refinar"]
        assert all(r.exito and not r.omitido for r in forzado.resultados)
        assert forzado.resultados[0].uso_acumulado.tokens_total == 2 * forzado.resultados[0].uso_ejecucion.tokens_total

    def test_recoger_esperando_y_sin_id(self, carpeta_videos, tmp_path, lote, monkeypatch):
        dobles, estado = lote
        monkeypatch.setattr(config, "INTERVALO_SONDEO_LOTE_SEG", 0)
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"))
        estados = iter([types.JobState.JOB_STATE_RUNNING, types.JobState.JOB_STATE_RUNNING, types.JobState.JOB_STATE_SUCCEEDED])
        original = gemini.estado_lote

        def avanzar(cliente, nombre_job):
            estado["state"] = next(estados)
            return original(cliente, nombre_job)
        monkeypatch.setattr(gemini, "estado_lote", avanzar)
        # sin lote_id: se toma el único pendiente
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave",
                                               esperar_lote=True)).resultados[0]
        assert r.exito and r.analisis is not None and estado["consultas"] == 3
        comprobar_salida(tmp_path / "salida" / "maquina", "maquina", r)
        # ya no hay lotes pendientes
        with pytest.raises(FileNotFoundError, match="pendientes"):
            pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave"))
        with pytest.raises(FileNotFoundError, match="no-existe"):
            pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave",
                                               lote_id="no-existe"))

    def test_error_por_video_en_el_lote(self, carpeta_videos, tmp_path, lote, monkeypatch):
        dobles, estado = lote
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"))
        monkeypatch.setattr(gemini, "recoger_lote",
                            lambda cliente, job, mapa, precios=None, *, log=print: {n: RuntimeError("respuesta inválida") for n in mapa})
        estado["state"] = types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave")).resultados[0]
        assert not r.exito and "respuesta inválida" in r.error
        assert (tmp_path / "salida" / "maquina" / config.NOMBRE_ERROR).is_file()
        assert dobles.de_tipo("eliminar") == [("eliminar", "files/maquina")]

    def test_ya_procesado_no_se_envia(self, carpeta_videos, tmp_path, lote):
        dobles, _ = lote
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path))          # simulado: momentos.json existe
        resumen = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"))
        assert resumen.resultados[0].omitido and dobles.llamadas == []
        assert not (tmp_path / "salida" / config.CARPETA_LOTES).exists()

    def test_incompleto_no_se_envia_se_retoma(self, carpeta_videos, tmp_path, lote, monkeypatch):
        """P-H4 en batch: momentos.json sin documentos se retoma desde el JSON en vez de omitirse o resubirse."""
        dobles, _ = lote
        op_sim = opciones(carpeta_videos, tmp_path)
        monkeypatch.setattr(documentos, "generar_documentos", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("roto")))
        assert not pipeline.procesar_carpeta(op_sim).resultados[0].exito
        monkeypatch.undo()
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave")).resultados[0]
        assert r.exito and not r.omitido and r.ruta_pdf.is_file() and dobles.llamadas == []

    def test_opciones_del_envio_valen_al_recoger(self, carpeta_videos, tmp_path, lote, monkeypatch):
        """P-H6: --max-momentos / --importancia-minima del envío se aplican al recoger si la CLI no dice otra cosa."""
        dobles, estado = lote
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave", max_momentos=3,
                                           importancia_minima=2))
        mapas = []
        original = gemini.recoger_lote
        monkeypatch.setattr(gemini, "recoger_lote", lambda c, j, m, p=None, *, log=print: (mapas.append(m), original(c, j, m, p, log=log))[1])
        estado["state"] = types.JobState.JOB_STATE_SUCCEEDED
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave"))
        assert mapas[0]["maquina"]["max_momentos"] == 3 and mapas[0]["maquina"]["importancia_minima"] == 2
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave", forzar=True,
                                           lote_id="abc-123", max_momentos=1, importancia_minima=4))   # ya recogido: por id
        assert mapas[1]["maquina"]["max_momentos"] == 1 and mapas[1]["maquina"]["importancia_minima"] == 4

    def test_nombres_repetidos_en_el_lote(self, carpeta_videos, tmp_path, lote, monkeypatch):
        """P-H9: IMG_0001.mp4 e IMG_0001.mov no comparten carpeta ni clave; al recoger son dos videos."""
        dobles, estado = lote
        (carpeta_videos / "maquina.mp4").rename(carpeta_videos / "IMG_0001.mp4")
        shutil.copy(carpeta_videos / "IMG_0001.mp4", carpeta_videos / "IMG_0001.mov")
        monkeypatch.setattr(video, "transcodificar_para_subida",
                            lambda ruta_video, destino, ffmpeg, alto=720, fps=2, *, log=print: (shutil.copy(ruta_video, destino), Path(destino))[1])
        op = opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave")
        resumen = pipeline.procesar_carpeta(op)
        carpetas = sorted(r.carpeta_salida.name for r in resumen.resultados)
        assert carpetas[0] == "IMG_0001" and carpetas[1].startswith("IMG_0001-") and len(carpetas[1]) == len("IMG_0001-") + 6
        claves = dobles.de_tipo("lote")[0][5]
        assert sorted(claves) == carpetas
        datos = json.loads(next((tmp_path / "salida" / config.CARPETA_LOTES).glob("*.json")).read_text(encoding="utf-8"))
        assert sorted(v["nombre"] for v in datos["videos"]) == carpetas
        assert {v["info"]["nombre"] for v in datos["videos"]} == {"IMG_0001"}
        estado["state"] = types.JobState.JOB_STATE_SUCCEEDED
        listo = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave"))
        assert sorted(dobles.de_tipo("recoger")[0][1]) == carpetas
        assert len(listo.resultados) == 2 and all(r.exito for r in listo.resultados)
        assert sorted(r.carpeta_salida.name for r in listo.resultados) == carpetas
        assert {r.info.ruta.name for r in listo.resultados} == {"IMG_0001.mp4", "IMG_0001.mov"}
        assert all((r.carpeta_salida / "IMG_0001.pdf").is_file() for r in listo.resultados)

    def test_lote_expirado_orienta_al_usuario(self, carpeta_videos, tmp_path, lote, monkeypatch):
        """P-H10: lote EXPIRED/FAILED → aviso claro con el paso siguiente (--batch)."""
        dobles, estado = lote
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"))
        monkeypatch.setattr(gemini, "estado_lote", lambda c, n: types.BatchJob(name=n, state=types.JobState.JOB_STATE_EXPIRED))
        monkeypatch.setattr(gemini, "recoger_lote", gemini.__dict__["recoger_lote"].__wrapped__
                            if hasattr(gemini.recoger_lote, "__wrapped__") else _recoger_lote_real)
        lineas, log = registro()
        r = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave"), log=log).resultados[0]
        assert not r.exito and "JOB_STATE_EXPIRED" in r.error and "--batch" in r.error
        assert any("AVISO" in l and "JOB_STATE_EXPIRED" in l and "--batch" in l for l in lineas)
        assert dobles.de_tipo("eliminar") == [("eliminar", "files/maquina")]
        # relanzar --batch reenvía solo el video sin momentos.json
        dobles.llamadas.clear()
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"))
        assert [l[0] for l in dobles.llamadas] == ["subir", "lote"]

    def test_ctrl_c_durante_las_subidas_borra_lo_subido(self, carpeta_videos, tmp_path, lote, monkeypatch):
        """G9 / P-H3: una interrupción a mitad del envío no deja copias del video en Google."""
        dobles, _ = lote
        shutil.copy(carpeta_videos / "maquina.mp4", carpeta_videos / "segundo.mp4")
        original = dobles.subir_video

        def subir(cliente, ruta, nombre, *a, **k):
            if nombre == "segundo":
                raise KeyboardInterrupt
            return original(cliente, ruta, nombre, *a, **k)
        monkeypatch.setattr(gemini, "subir_video", subir)
        lineas, log = registro()
        with pytest.raises(KeyboardInterrupt):
            pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"), log=log)
        assert dobles.de_tipo("eliminar") == [("eliminar", "files/maquina")]
        assert not (tmp_path / "salida" / config.CARPETA_LOTES).exists()
        assert any("interrumpido" in l for l in lineas)
        # si falla la creación del lote también se borran (salvo --conservar-subida)
        monkeypatch.setattr(gemini, "subir_video", original)
        monkeypatch.setattr(gemini, "enviar_lote", lambda *a, **k: (_ for _ in ()).throw(errors.APIError(400, {"error": {"code": 400, "message": "mal", "status": "INVALID_ARGUMENT"}})))
        dobles.llamadas.clear()
        resumen = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"))
        assert sorted(l[1] for l in dobles.de_tipo("eliminar")) == ["files/maquina", "files/segundo"]
        assert all(not r.exito and "400" in r.error for r in resumen.resultados)
        dobles.llamadas.clear()
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave", conservar_subida=True))
        assert dobles.de_tipo("eliminar") == [] and dobles.conservar[-2:] == [True, True]

    def test_lote_rechazado_por_una_opcion_se_reenvia(self, carpeta_videos, tmp_path, lote, monkeypatch):
        """G7: si todas las respuestas del lote son un 400 de configuración, se reenvía sin esa opción (máx. 2)."""
        dobles, estado = lote
        shutil.copy(carpeta_videos / "maquina.mp4", carpeta_videos / "segundo.mp4")
        pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch", api_key="clave"))
        carpeta_lotes = tmp_path / "salida" / config.CARPETA_LOTES
        rechazo = lambda c, j, m, p=None, *, log=print: {n: gemini.ErrorItemLote(n, 0, 3, "thinking_level is not supported") for n in m}  # noqa: E731
        monkeypatch.setattr(gemini, "recoger_lote", rechazo)
        recuperados = []
        monkeypatch.setattr(gemini, "obtener_archivo", lambda c, nombre: (recuperados.append(nombre),
                                                                          types.File(name=nombre, uri=f"https://x/{nombre}", mime_type="video/mp4"))[1])
        estado["state"] = types.JobState.JOB_STATE_SUCCEEDED
        lineas, log = registro()
        resumen = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", lote_id="abc-123",
                                                     api_key="clave"), log=log)
        # el lote se reenvió con los mismos archivos remotos, sin borrarlos ni refinar nada
        assert [l[0] for l in dobles.llamadas if l[0] != "subir"][-2:] == ["lote", "lote"] or dobles.de_tipo("lote")
        reenvio = dobles.de_tipo("lote")[-1]
        assert sorted(reenvio[5]) == ["maquina", "segundo"] and reenvio[6].thinking is False and reenvio[6].reintentos == 1
        assert sorted(recuperados) == ["files/maquina", "files/segundo"] and dobles.de_tipo("eliminar") == []
        assert all(r.exito and r.analisis is None for r in resumen.resultados) and "pendiente (lote)" in resumen.tabla()
        assert any("se reenvía" in l and "sin thinking" in l for l in lineas)
        viejo = json.loads((carpeta_lotes / "abc-123.json").read_text(encoding="utf-8"))
        assert viejo["reenviado_como"] == "abc-246" and "recogido" in viejo
        nuevo = json.loads((carpeta_lotes / "abc-246.json").read_text(encoding="utf-8"))
        assert nuevo["reenvio_de"] == "abc-123" and nuevo["escalera"]["thinking"] is False and nuevo["escalera"]["reintentos"] == 1
        assert [v["nombre"] for v in nuevo["videos"]] == ["maquina", "segundo"] and "recogido" not in nuevo
        # el lote nuevo es el único pendiente; si vuelve a fallar por el esquema se reenvía una segunda vez…
        monkeypatch.setattr(gemini, "recoger_lote", lambda c, j, m, p=None, *, log=print: {n: gemini.ErrorItemLote(n, 0, 3, "response_json_schema unsupported") for n in m})
        segundo = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave"))
        assert all(r.exito and r.analisis is None for r in segundo.resultados)
        tercero_datos = json.loads((carpeta_lotes / "abc-369.json").read_text(encoding="utf-8"))
        assert tercero_datos["escalera"]["esquema"] is False and tercero_datos["escalera"]["reintentos"] == 2
        # …y a la tercera ya no: se registran los errores y se borran los archivos remotos
        dobles.llamadas.clear()
        tercero = pipeline.procesar_carpeta(opciones(carpeta_videos, tmp_path, modo="batch-recoger", api_key="clave"))
        assert all(not r.exito and "schema" in r.error for r in tercero.resultados)
        assert sorted(l[1] for l in dobles.de_tipo("eliminar")) == ["files/maquina", "files/segundo"]
        assert not dobles.de_tipo("lote")


_recoger_lote_real = gemini.recoger_lote


# ----------------------------------------------------------------------------
# Piezas sueltas
# ----------------------------------------------------------------------------
def info_falsa(ruta: str, nombre: str = "video", duracion: float = 100.0) -> InfoVideo:
    return InfoVideo(ruta=Path(ruta), nombre=nombre, duracion=duracion, fps=30.0, ancho=1920, alto=1080, tamano_bytes=5_000_000)


class TestPiezas:
    def test_preparar_carpeta_salida_colision(self, tmp_path):
        op = pipeline.Opciones(carpeta_videos=tmp_path, carpeta_salida=tmp_path / "salida")
        a = info_falsa(tmp_path / "a" / "video.mp4")
        b = info_falsa(tmp_path / "b" / "video.mp4")
        carpeta_a = pipeline.preparar_carpeta_salida(a, op)
        assert carpeta_a == tmp_path / "salida" / "video" and carpeta_a.is_dir()
        pipeline.guardar_json(carpeta_a, a, ResultadoAnalisis(momentos=[], modo="simulado"))
        assert pipeline.preparar_carpeta_salida(a, op) == carpeta_a            # el mismo video repite carpeta
        carpeta_b = pipeline.preparar_carpeta_salida(b, op)
        assert carpeta_b != carpeta_a and carpeta_b.name.startswith("video-") and len(carpeta_b.name) == len("video-") + 6

    def test_guardar_y_cargar_json(self, tmp_path):
        info = info_falsa(tmp_path / "v.mp4", "v")
        momentos = momentos_falsos(100.0, 3)
        momentos[0].ruta_captura = str(tmp_path / "salida" / "capturas" / "01_00-25.jpg")
        momentos[0].ruta_captura_anotada = str(tmp_path / "salida" / "capturas" / "01_00-25_anotada.jpg")
        analisis = ResultadoAnalisis(momentos=momentos, modo="gemini", modelo=MODELO, resumen="R", titulo="T", tramos=2,
                                     uso=Uso(modelo=MODELO, tokens_total=10, llamadas=1, costo_usd=0.01), avisos=["a"])
        ruta = pipeline.guardar_json(tmp_path / "salida", info, analisis)
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        assert datos["analisis"]["momentos"][0]["captura"] == str(Path("capturas") / "01_00-25.jpg")
        cargado, docs = pipeline.cargar_json(tmp_path / "salida")
        assert docs == {} and cargado.modo == "gemini" and cargado.titulo == "T" and cargado.tramos == 2
        assert cargado.uso.costo_usd == 0.01 and cargado.avisos == ["a"]
        assert cargado.momentos[0].ruta_captura == momentos[0].ruta_captura
        assert cargado.momentos[0].ruta_captura_anotada == momentos[0].ruta_captura_anotada
        assert cargado.momentos[1].zona == momentos[1].zona and cargado.momentos[2].tiempo == momentos[2].tiempo
        assert pipeline.cargar_json(tmp_path / "no-existe") == (None, {})

    def test_tabla_alineada_y_costo(self):
        info_ok = info_falsa("/v/a.mp4", "a")
        info_largo = info_falsa("/v/b.mp4", "nombre " * 12)
        analisis_ok = analisis_falso(info_ok, 12)
        analisis_sin_precio = analisis_falso(info_largo, 3, modelo="modelo-x", costo=None)
        acumulado_d = Uso(modelo=MODELO, tokens_total=20000, llamadas=2, costo_usd=0.005)
        resultados = [
            ResultadoVideo(info=info_ok, carpeta_salida=Path("/s/a"), exito=True, analisis=analisis_ok, paginas_pdf=8,
                           uso_ejecucion=analisis_ok.uso, uso_acumulado=analisis_ok.uso),
            ResultadoVideo(info=info_largo, carpeta_salida=Path("/s/b"), exito=True, analisis=analisis_sin_precio, paginas_pdf=-1,
                           uso_ejecucion=analisis_sin_precio.uso, uso_acumulado=analisis_sin_precio.uso),
            ResultadoVideo(info=info_falsa("/v/c.mp4", "c"), carpeta_salida=Path("/s/c"), exito=False, error="falló\n  todo"),
            ResultadoVideo(info=info_falsa("/v/d.mp4", "d"), carpeta_salida=Path("/s/d"), exito=True, analisis=analisis_ok,
                           omitido=True, uso_acumulado=acumulado_d),
            ResultadoVideo(info=info_falsa("/v/e.mp4", "e"), carpeta_salida=Path("/s/e"), exito=True),
        ]
        resumen = pipeline.ResumenEjecucion(resultados, pipeline.sumar_uso(resultados, MODELO), 3725,
                                            pipeline.sumar_acumulado(resultados, MODELO))
        assert resumen.uso_total.tokens_total == 9900 * 2 and resumen.uso_total.costo_usd is None    # un precio desconocido
        assert resumen.uso_acumulado.tokens_total == 9900 * 2 + 20000 and resumen.uso_acumulado.costo_usd is None
        lineas = resumen.tabla().splitlines()
        cabecera, separador, filas = lineas[0], lineas[1], lineas[2:7]
        assert cabecera.startswith("Video") and len(separador) >= len(cabecera)
        assert "Costo est." in cabecera and "Acumulado" in cabecera
        posiciones = [i for i, c in enumerate(cabecera) if c == "|"]
        assert [i for i, c in enumerate(separador) if c == "+"] == posiciones
        for fila in filas:
            assert [i for i, c in enumerate(fila) if c == "|"] == posiciones
        assert "…" in filas[1] and "?" in filas[1] and "ERROR: falló todo" in filas[2]
        assert "ya procesado (omitido)" in filas[3] and "pendiente (lote)" in filas[4]
        assert "US$ 0.0025" in filas[0] and "8 |" in filas[0]
        # P-H5: el omitido no cuenta en esta ejecución (Tokens/Costo "-") pero sí muestra su acumulado
        celdas_d = [c.strip() for c in filas[3].split("|")]
        assert celdas_d[3] == "-" and celdas_d[4] == "-" and celdas_d[5] == "US$ 0.0050"
        assert any(l.startswith("ESTIMACIÓN de costo de esta ejecución: desconocida") for l in lineas)
        assert any("acumulado de estos videos en todas las ejecuciones: desconocida" in l for l in lineas)
        assert any("1 h 2 min" in l and "1 con error" in l and "1 omitido" in l for l in lineas)
        vacio = pipeline.ResumenEjecucion([], None, 5).tabla()
        assert "(ningún video)" in vacio and "esta ejecución: US$ 0.0000 (sin API)" in vacio
        assert "todas las ejecuciones: US$ 0.0000 (sin API)" in vacio

    def test_seleccionar_videos_con_solo(self, tmp_path):
        carpeta = tmp_path / "videos"
        carpeta.mkdir()
        for nombre in ("Arco en C parte 1.MOV", "otro.mp4", "notas.txt", ".oculto.mp4"):
            (carpeta / nombre).write_bytes(b"x")
        op = pipeline.Opciones(carpeta_videos=carpeta, carpeta_salida=tmp_path / "s")
        assert [v.name for v in pipeline.seleccionar_videos(op)] == ["Arco en C parte 1.MOV", "otro.mp4"]
        lineas, log = registro()
        op.solo = ["arco en c parte 1", "OTRO.mp4", "no-existe"]
        assert [v.name for v in pipeline.seleccionar_videos(op, log)] == ["Arco en C parte 1.MOV", "otro.mp4"]
        assert any("no-existe" in l for l in lineas)
        with pytest.raises(FileNotFoundError):
            pipeline.seleccionar_videos(pipeline.Opciones(carpeta_videos=tmp_path / "nada", carpeta_salida=tmp_path))

    def test_precios_de(self, tmp_path):
        op = pipeline.Opciones(carpeta_videos=tmp_path, carpeta_salida=tmp_path)
        assert pipeline.precios_de(op) is None
        assert pipeline.precios_de(replace(op, precio_entrada=0.5, precio_salida=2.0)) == {"entrada": 0.5, "salida": 2.0}
        parcial = pipeline.precios_de(replace(op, precio_entrada=0.5))
        assert parcial == {"entrada": 0.5, "salida": config.PRECIOS_USD_POR_MILLON[MODELO]["salida"]}
        with pytest.raises(ValueError, match="precio-salida"):
            pipeline.precios_de(replace(op, modelo="modelo-desconocido", precio_entrada=0.5))

    def test_mensaje_de_error(self):
        api = errors.APIError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
        mensaje = pipeline.mensaje_de_error(api)
        assert mensaje.startswith("Error de la API de Gemini (404 NOT_FOUND): model not found") and "--modelo" in mensaje
        assert pipeline.mensaje_de_error(FileNotFoundError("x.mp4")).startswith("No se encontró")
        assert pipeline.mensaje_de_error(RuntimeError("ffmpeg falló")) == "ffmpeg falló"
        assert pipeline.mensaje_de_error(KeyError("clave")) == "KeyError: 'clave'"

    def test_modo_desconocido(self, tmp_path):
        with pytest.raises(ValueError, match="Modo desconocido"):
            pipeline.procesar_carpeta(pipeline.Opciones(carpeta_videos=tmp_path, carpeta_salida=tmp_path, modo="otro"))

    def test_crear_log_escribe_en_ambos(self, tmp_path):
        lineas, consola = registro()
        log = pipeline.crear_log(tmp_path, consola)
        log("hola")
        log("mundo")
        archivo = (tmp_path / config.NOMBRE_LOG).read_text(encoding="utf-8").splitlines()
        assert lineas == archivo and all(l.endswith((" hola", " mundo")) and l[2] == ":" for l in archivo)

    def test_anotar_momentos_explica_en_el_log_la_zona_que_no_se_anota(self, tmp_path):
        """D8: el aviso de ``anotar_captura`` ("cubre casi toda la captura") debe llegar al log del video."""
        from PIL import Image

        captura = tmp_path / "01_00-10.jpg"
        Image.new("RGB", (320, 180), (90, 120, 150)).save(captura, "JPEG")
        marco = Momento(tiempo_seg=10.0, titulo="Marco", descripcion="", zona={"caja": [0, 0, 1, 1]},
                        ruta_captura=str(captura))
        punto = Momento(tiempo_seg=20.0, titulo="Punto", descripcion="", zona={"x": 0.5, "y": 0.5},
                        ruta_captura=str(captura))
        lineas, consola = registro()
        assert pipeline.anotar_momentos([marco, punto], log=consola) == 1
        assert marco.ruta_captura_anotada is None and punto.ruta_captura_anotada is not None
        assert any("casi toda la captura" in l for l in lineas) and lineas[-1].startswith("Anotaciones: 1 ")


# ----------------------------------------------------------------------------
# CLI (subprocess)
# ----------------------------------------------------------------------------
def ejecutar_cli(*args, cwd=None, sin_clave: bool = True) -> subprocess.CompletedProcess:
    """Ejecuta la CLI en un subproceso SIN clave de Gemini: las variables van vacías (no basta quitarlas, porque
    ``load_dotenv`` rellenaría las ausentes con el ``.env`` del usuario y el test llamaría a la API real)."""
    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "utf-8"
    if sin_clave:
        entorno["GEMINI_API_KEY"] = ""
        entorno["GOOGLE_API_KEY"] = ""
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, encoding="utf-8",
                          errors="replace", cwd=cwd or RAIZ, env=entorno, timeout=300)


class TestCLI:
    def test_version_y_help(self):
        version = ejecutar_cli("--version")
        assert version.returncode == 0 and version.stdout.strip() == f"resumen_videos {pipeline.__version__}"
        ayuda = ejecutar_cli("--help")
        assert ayuda.returncode == 0
        for opcion in ("--regenerar", "--resolucion", "--copia-alto", "--subir-original", "--sin-refinado",
                       "--batch-recoger", "--esperar", "--local", "--simular", "--equipo", "--redactor", "--solo"):
            assert opcion in ayuda.stdout, opcion
        assert "Ejemplos:" in ayuda.stdout and ayuda.stdout.count("python resumir_videos.py") >= 5

    def test_errores_de_configuracion(self, tmp_path, carpeta_videos):
        vacia = tmp_path / "vacia"
        vacia.mkdir()
        sin_videos = ejecutar_cli(vacia, "--simular", "--salida", tmp_path / "s")
        assert sin_videos.returncode == 2 and "No hay videos" in sin_videos.stderr
        no_existe = ejecutar_cli(tmp_path / "no-existe", "--simular")
        assert no_existe.returncode == 2 and "No existe la carpeta" in no_existe.stderr
        sin_clave = ejecutar_cli(carpeta_videos, "--salida", tmp_path / "s", cwd=tmp_path, sin_clave=True)
        assert sin_clave.returncode == 2 and "GEMINI_API_KEY" in sin_clave.stderr and ".env" in sin_clave.stderr
        assert "--simular" in sin_clave.stderr
        assert not (tmp_path / "s").exists() or not list((tmp_path / "s").rglob(config.NOMBRE_LOG))   # nunca llegó a subir nada
        mal_solo = ejecutar_cli(carpeta_videos, "--simular", "--solo", "nada", "--salida", tmp_path / "s")
        assert mal_solo.returncode == 2 and "--solo" in mal_solo.stderr
        mala_importancia = ejecutar_cli(carpeta_videos, "--simular", "--importancia-minima", "9")
        assert mala_importancia.returncode == 2 and "1 a 5" in mala_importancia.stderr

    def test_cli_sin_clave_con_env_del_usuario(self, tmp_path, carpeta_videos):
        """P-H1: aunque exista un .env con clave junto al script, el test 'sin clave' no debe tocar la API."""
        copia = tmp_path / "proyecto"
        for nombre in ("resumen_videos", "tools"):
            shutil.copytree(RAIZ / nombre, copia / nombre, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(CLI, copia / CLI.name)
        (copia / ".env").write_text("GEMINI_API_KEY=clave-de-prueba-que-no-debe-usarse\n", encoding="utf-8")
        entorno = dict(os.environ, PYTHONIOENCODING="utf-8", GEMINI_API_KEY="", GOOGLE_API_KEY="")
        proceso = subprocess.run([sys.executable, str(copia / CLI.name), str(carpeta_videos), "--salida", str(tmp_path / "s")],
                                 capture_output=True, encoding="utf-8", errors="replace", cwd=copia, env=entorno, timeout=300)
        assert proceso.returncode == 2 and "GEMINI_API_KEY" in proceso.stderr, proceso.stdout + proceso.stderr
        assert not list((tmp_path / "s").rglob("log.txt")) if (tmp_path / "s").exists() else True

    def test_temperatura_en_la_cli(self):
        import resumir_videos
        parser = resumir_videos.crear_parser()
        assert parser.parse_args([]).temperatura is None
        assert parser.parse_args(["--temperatura", "0.2"]).temperatura == 0.2

    def test_simular_completo(self, tmp_path, carpeta_videos):
        salida = tmp_path / "salida"
        proceso = ejecutar_cli(carpeta_videos, "--simular", "--salida", salida, "--por-pagina", "2", "--sin-indice")
        assert proceso.returncode == 0, proceso.stderr
        assert "ESTIMACIÓN de costo de esta ejecución: US$ 0.0000 (sin API)" in proceso.stdout and "| OK" in proceso.stdout
        assert (salida / "maquina" / config.NOMBRE_JSON).is_file() and (salida / "maquina" / "maquina.pdf").is_file()
        datos = json.loads((salida / "maquina" / config.NOMBRE_JSON).read_text(encoding="utf-8"))
        assert datos["documentos"]["paginas"] == 1 + math.ceil(len(datos["analisis"]["momentos"]) / 2)
        # segunda vez: omitido, código 0
        repetido = ejecutar_cli(carpeta_videos, "--simular", "--salida", salida)
        assert repetido.returncode == 0 and "omitido" in repetido.stdout

    def test_video_corrupto_devuelve_1(self, tmp_path, carpeta_videos):
        (carpeta_videos / "malo.mp4").write_text("no es un video", encoding="utf-8")
        proceso = ejecutar_cli(carpeta_videos, "--simular", "--salida", tmp_path / "salida")
        assert proceso.returncode == 1 and "ERROR" in proceso.stdout and "error.txt" in proceso.stderr
        assert (tmp_path / "salida" / "malo" / config.NOMBRE_ERROR).is_file()


# ----------------------------------------------------------------------------- rotación de capturas
class TestRotacion:
    def test_rotar_zona(self):
        assert pipeline.rotar_zona({"x": 0.2, "y": 0.7}, 90) == {"x": 0.3, "y": 0.2}
        assert pipeline.rotar_zona({"x": 0.2, "y": 0.7}, 180) == {"x": 0.8, "y": 0.3}
        assert pipeline.rotar_zona({"x": 0.2, "y": 0.7}, 270) == {"x": 0.7, "y": 0.8}
        assert pipeline.rotar_zona({"x": 0.2, "y": 0.7}, 0) == {"x": 0.2, "y": 0.7}
        assert pipeline.rotar_zona({"caja": [0.1, 0.2, 0.3, 0.6]}, 90) == {"caja": [0.4, 0.1, 0.8, 0.3]}
        assert pipeline.rotar_zona(None, 90) is None

    def test_rotar_capturas_gira_imagen_y_zona(self, tmp_path):
        from PIL import Image
        ruta = tmp_path / "c.jpg"
        im = Image.new("RGB", (400, 200), (20, 20, 20))
        for x in range(370, 400):                         # bloque rojo en la esquina superior derecha
            for y in range(0, 30):
                im.putpixel((x, y), (255, 0, 0))
        im.save(ruta, "JPEG", quality=100)
        m = Momento(tiempo_seg=1, titulo="t", descripcion="d", ruta_captura=str(ruta), zona={"x": 0.975, "y": 0.05},
                    rotacion=90)
        registro: list[str] = []
        assert pipeline.rotar_capturas([m], rotar_zona_tambien=True, log=registro.append) == 1
        with Image.open(ruta) as girada:
            assert girada.size == (200, 400)
            px = girada.getpixel((int(0.95 * 200), int(0.975 * 400)))    # 90° horario: arriba-derecha -> abajo-derecha
        assert px[0] > 150 and px[1] < 100
        assert m.zona == {"x": 0.95, "y": 0.975}
        assert any("enderezadas: 1" in r for r in registro)
        # sin rotar la zona (regenerar): solo la imagen
        m2 = Momento(tiempo_seg=1, titulo="t", descripcion="d", ruta_captura=str(ruta), zona={"x": 0.5, "y": 0.5},
                     rotacion=180)
        assert pipeline.rotar_capturas([m2], rotar_zona_tambien=False, log=lambda _: None) == 1
        assert m2.zona == {"x": 0.5, "y": 0.5}
        # sin rotación o sin captura: nada
        m3 = Momento(tiempo_seg=1, titulo="t", descripcion="d", ruta_captura=None, rotacion=90)
        assert pipeline.rotar_capturas([m3, Momento(tiempo_seg=2, titulo="t", descripcion="d")],
                                       rotar_zona_tambien=True, log=lambda _: None) == 0

    def test_rotacion_se_guarda_y_se_lee_del_json(self, tmp_path):
        m = Momento(tiempo_seg=1, titulo="t", descripcion="d", rotacion=270)
        assert m.a_dict()["rotacion"] == 270 and "rotacion" not in Momento(tiempo_seg=1, titulo="t", descripcion="d").a_dict()
        assert pipeline._momento_desde_dict({"tiempo_seg": 1, "titulo": "t", "descripcion": "d", "rotacion": 270}, tmp_path).rotacion == 270
        assert pipeline._momento_desde_dict({"tiempo_seg": 1, "titulo": "t", "descripcion": "d", "rotacion": 45}, tmp_path).rotacion == 0


class TestSoloRefinado:
    def test_solo_refinado_repite_el_refinado_y_endereza_capturas(self, carpeta_videos, tmp_path, monkeypatch):
        from PIL import Image
        dobles = DoblesGemini(monkeypatch)
        op = opciones(carpeta_videos, tmp_path, modo="gemini", api_key="clave")
        r1 = pipeline.procesar_carpeta(op).resultados[0]
        carpeta = r1.carpeta_salida
        with Image.open(r1.analisis.momentos[0].ruta_captura) as im:
            ancho, alto = im.size
        assert ancho > alto and r1.analisis.momentos[0].rotacion == 0
        # segunda pasada: solo refinado, y ahora el refinado dice que el primer paso está girado 90°
        dobles.llamadas.clear()
        dobles.rotar_primero = True
        lineas, log = registro()
        resumen = pipeline.procesar_carpeta(replace(op, solo_refinado=True), log=log)
        r2 = resumen.resultados[0]
        datos = comprobar_salida(carpeta, "maquina", r2)
        assert [l[0] for l in dobles.llamadas] == ["refinar"]             # ni subida ni análisis
        assert any("--solo-refinado" in l for l in lineas) and any("enderezadas: 1" in l for l in lineas)
        m0 = r2.analisis.momentos[0]
        assert m0.rotacion == 90 and datos["analisis"]["momentos"][0]["rotacion"] == 90
        with Image.open(m0.ruta_captura) as im:
            assert im.size == (alto, ancho)                                  # la captura quedó girada
        assert m0.ruta_captura_anotada and Path(m0.ruta_captura_anotada).is_file()
        assert all(m.rotacion == 0 for m in r2.analisis.momentos[1:])
        # se pagó solo el refinado y se acumuló
        assert r2.uso_ejecucion.tokens_total == 1800 and r2.uso_acumulado.tokens_total == 11700 + 1800
        # --regenerar después conserva la rotación guardada sin llamar a la API
        dobles.llamadas.clear()
        r3 = pipeline.procesar_carpeta(replace(op, regenerar=True)).resultados[0]
        assert dobles.llamadas == [] and r3.analisis.momentos[0].rotacion == 90
        with Image.open(r3.analisis.momentos[0].ruta_captura) as im:
            assert im.size == (alto, ancho)
