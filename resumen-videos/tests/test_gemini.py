"""Tests de ``resumen_videos.gemini`` sin red: cliente falso que devuelve objetos reales del SDK."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from google.genai import errors, types

from resumen_videos import config, gemini
from resumen_videos.modelos import InfoVideo, Momento, ResultadoAnalisis, Uso

MODELO = config.MODELO_POR_DEFECTO
ALTERNATIVO = config.MODELOS_ALTERNATIVOS[0]


# ----------------------------------------------------------------------------
# Utilidades: cliente falso y constructores de objetos del SDK
# ----------------------------------------------------------------------------

def info_video(duracion: float = 90.0, nombre: str = "prueba") -> InfoVideo:
    return InfoVideo(ruta=Path(f"/videos/{nombre}.mp4"), nombre=nombre, duracion=duracion, fps=30.0,
                     ancho=1280, alto=720, tamano_bytes=1_500_000)


def archivo_remoto(nombre: str = "files/abc123", estado=types.FileState.ACTIVE) -> types.File:
    return types.File(name=nombre, uri=f"https://generativelanguage.googleapis.com/v1beta/{nombre}",
                      mime_type="video/mp4", state=estado, display_name="prueba")


def respuesta(texto: str | None, finish=types.FinishReason.STOP, entrada: int = 1000, salida: int = 200,
              pensamiento: int = 50, parsed=None, con_uso: bool = True) -> types.GenerateContentResponse:
    """``GenerateContentResponse`` real con un candidato de texto y usage_metadata."""
    partes = [types.Part(text=texto)] if texto is not None else None
    uso = None
    if con_uso:
        uso = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=entrada, candidates_token_count=salida, thoughts_token_count=pensamiento,
            total_token_count=entrada + salida + pensamiento,
            prompt_tokens_details=[
                types.ModalityTokenCount(modality=types.MediaModality.VIDEO, token_count=entrada - 100),
                types.ModalityTokenCount(modality=types.MediaModality.AUDIO, token_count=60),
                types.ModalityTokenCount(modality=types.MediaModality.TEXT, token_count=40),
            ])
    resp = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=partes), finish_reason=finish)],
        usage_metadata=uso, model_version=MODELO)
    if parsed is not None:
        resp.parsed = parsed      # el SDK lo asigna tras construir la respuesta (en el constructor pydantic lo convierte)
    return resp


def error_cliente(codigo: int, mensaje: str, estado: str | None = None) -> errors.ClientError:
    """``ClientError`` construido como lo hace el SDK: ``ClientError(code, response_json, response=None)``."""
    if estado is None:
        estado = {400: "INVALID_ARGUMENT", 404: "NOT_FOUND", 403: "PERMISSION_DENIED"}.get(codigo, "UNKNOWN")
    return errors.ClientError(codigo, {"error": {"code": codigo, "message": mensaje, "status": estado}})


def momento_json(tiempo: str, titulo: str = "Paso", importancia: int = 3, seccion: str = "Preparación",
                 zona: dict | None = None, **extra) -> dict:
    datos = {"tiempo": tiempo, "titulo": titulo, "descripcion": f"Descripción de {titulo}.",
             "importancia": importancia, "fuente": "ambos", "seccion": seccion}
    if zona is not None:
        datos["zona"] = zona
    datos.update(extra)
    return datos


def documento_json(momentos: list[dict], titulo: str = "Manual del arco en C",
                   resumen: str = "El video enseña a operar el equipo.") -> str:
    return json.dumps({"titulo_video": titulo, "resumen": resumen, "momentos": momentos}, ensure_ascii=False)


class _Archivos:
    def __init__(self, cliente, estados: list):
        self._cliente = cliente
        self._estados = list(estados)      # estados que devuelve files.get, en orden (el último se repite)
        self.eliminados: list[str] = []
        self.fallar_delete = False

    def upload(self, *, file, config):
        self._cliente.llamadas.append({"tipo": "upload", "file": file, "config": config})
        estado = self._estados.pop(0) if self._estados else types.FileState.ACTIVE
        return types.File(name="files/subido", uri="https://x/files/subido", mime_type=config.mime_type,
                          display_name=config.display_name, state=estado)

    def get(self, *, name, config=None):
        self._cliente.llamadas.append({"tipo": "get", "name": name})
        estado = self._estados.pop(0) if len(self._estados) > 1 else (self._estados[0] if self._estados else types.FileState.ACTIVE)
        archivo = types.File(name=name, uri="https://x/" + name, mime_type="video/mp4", state=estado)
        if estado == types.FileState.FAILED:
            archivo.error = types.FileStatus(code=3, message="códec no soportado")
        return archivo

    def delete(self, *, name, config=None):
        self._cliente.llamadas.append({"tipo": "delete", "name": name})
        if self.fallar_delete:
            raise error_cliente(403, "sin permiso")
        self.eliminados.append(name)
        return types.DeleteFileResponse()


class _Modelos:
    def __init__(self, cliente, respuestas: list):
        self._cliente = cliente
        self._cola = list(respuestas)     # respuestas o excepciones, en el orden en que se consumen

    def generate_content(self, *, model, contents, config):
        self._cliente.llamadas.append({"tipo": "generate", "modelo": model, "contents": contents, "config": config})
        if not self._cola:
            raise AssertionError("el cliente falso se quedó sin respuestas")
        siguiente = self._cola.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente


class _Lotes:
    def __init__(self, cliente, job=None):
        self._cliente = cliente
        self.job = job

    def create(self, *, model, src, config=None):
        self._cliente.llamadas.append({"tipo": "batch_create", "modelo": model, "src": src, "config": config})
        return types.BatchJob(name="batches/lote1", display_name=config.display_name if config else None,
                              state=types.JobState.JOB_STATE_PENDING, model=f"models/{model}")

    def get(self, *, name, config=None):
        self._cliente.llamadas.append({"tipo": "batch_get", "name": name})
        return self.job


class ClienteFalso:
    """Misma forma que ``genai.Client`` (files/models/batches); registra todas las llamadas."""

    def __init__(self, respuestas: list | None = None, estados_archivo: list | None = None, job=None):
        self.llamadas: list[dict] = []
        self.files = _Archivos(self, estados_archivo or [types.FileState.ACTIVE])
        self.models = _Modelos(self, respuestas or [])
        self.batches = _Lotes(self, job)

    def generaciones(self) -> list[dict]:
        return [l for l in self.llamadas if l["tipo"] == "generate"]


def registro():
    lineas: list[str] = []
    return lineas, lineas.append


def resolucion_en_wire(cfg) -> str | None:
    """``generationConfig.mediaResolution`` tal como el SDK lo serializa para la API (respaldo: el campo)."""
    try:
        from google.genai.models import _GenerateContentConfig_to_mldev
        valor = _GenerateContentConfig_to_mldev(None, cfg, {}).get("mediaResolution")
    except Exception:  # noqa: BLE001 - función privada del SDK: si cambia, vale el campo del objeto
        valor = cfg.media_resolution
    return None if valor is None else getattr(valor, "value", str(valor))


# ----------------------------------------------------------------------------
# Prompt y esquema
# ----------------------------------------------------------------------------

class TestPromptYEsquema:
    def test_prompt_sin_rangos_numericos_de_momentos(self):
        texto = gemini.PROMPT_SISTEMA + gemini.PROMPT_USUARIO
        patrones = [
            r"entre\s+\d+\s+y\s+\d+\s+momentos", r"m[aá]ximo\s+(de\s+)?\d+\s+momentos", r"m[ií]nimo\s+(de\s+)?\d+\s+momentos",
            r"al menos\s+\d+\s+momentos", r"no m[aá]s de\s+\d+\s+momentos", r"\d+\s*[-–a]\s*\d+\s+momentos",
            r"\d+\s+momentos\s+como\s+m[aá]ximo",
        ]
        for patron in patrones:
            assert re.search(patron, texto, re.I) is None, patron

    def test_prompt_contenido_obligatorio(self):
        sistema = gemini.construir_prompt_sistema("arco en C")
        assert "arco en C" in sistema and "{equipo}" not in sistema
        assert "instructor clínico" in sistema
        assert "no inventes" in sistema.lower()
        assert "mm:ss" in sistema and "hh:mm:ss" in sistema
        assert "se DICE" in sistema and "se VE" in sistema
        assert "zona" in sistema and "seccion" in sistema and "titulo_video" in sistema and "resumen" in sistema
        assert "sin emojis" in sistema.lower()
        # sin emojis en el propio prompt
        assert re.search(r"[\U0001F300-\U0001FAFF☀-➿]", sistema + gemini.PROMPT_USUARIO) is None

    def test_equipo_por_defecto_si_vacio(self):
        assert config.EQUIPO_POR_DEFECTO in gemini.construir_prompt_sistema("  ")

    def test_prompt_tramo(self):
        texto = gemini.construir_prompt_usuario((2700.0, 5400.0))
        assert texto.startswith(gemini.PROMPT_USUARIO)
        assert "45:00–90:00" in texto and "relativos al inicio del fragmento" in texto
        assert gemini.construir_prompt_usuario(None) == gemini.PROMPT_USUARIO

    def test_esquema_plano_compatible(self):
        esquema = gemini.ESQUEMA_RESPUESTA
        json.dumps(esquema)   # serializable
        assert esquema["required"] == ["titulo_video", "resumen", "momentos"]
        item = esquema["properties"]["momentos"]["items"]
        assert "zona" in item["properties"] and "zona" not in item["required"]
        assert item["properties"]["fuente"]["enum"] == ["visual", "audio", "ambos"]

        def sin_null(nodo):
            if isinstance(nodo, dict):
                assert not isinstance(nodo.get("type"), list), "type como lista no es compatible"
                assert "nullable" not in nodo and "anyOf" not in nodo
                for v in nodo.values():
                    sin_null(v)
            elif isinstance(nodo, list):
                for v in nodo:
                    sin_null(v)
        sin_null(esquema)

    def test_config_usa_copia_del_esquema(self):
        cfg = gemini._construir_config("sistema", 100)
        assert cfg.response_json_schema == gemini.ESQUEMA_RESPUESTA
        assert cfg.response_json_schema is not gemini.ESQUEMA_RESPUESTA
        assert cfg.response_json_schema["properties"] is not gemini.ESQUEMA_RESPUESTA["properties"]
        assert cfg.response_mime_type == "application/json"
        assert cfg.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_MEDIUM     # config.RESOLUCION_VIDEO
        assert cfg.thinking_config.thinking_level == types.ThinkingLevel.LOW
        assert cfg.temperature == config.TEMPERATURA and cfg.max_output_tokens == 100

    def test_temperatura_por_defecto_no_se_envia(self):
        assert config.TEMPERATURA is None      # G10: el valor por defecto del modelo (Google lo recomienda en Gemini 3)
        assert gemini._construir_config("s", 1).temperature is None
        assert gemini._construir_config("s", 1, temperatura=0.3).temperature == 0.3
        assert gemini._construir_config("s", 1, temperatura=0).temperature == 0.0

    def test_prompt_refinado_conserva_el_audio_y_admite_zona_null(self):
        sistema = gemini.PROMPT_REFINADO.lower()
        usuario = gemini.PROMPT_REFINADO_USUARIO.lower()
        assert "audio" in sistema and "consérvala siempre" in sistema and "escribe ambos" in sistema
        assert 'fuente "audio"' in sistema and "solo se completan" in sistema
        assert "null" in sistema and "null" in usuario
        assert "conserva siempre" in usuario and "escribe ambos" in usuario and "solo se completan" in usuario
        assert "null" in gemini.PROMPT_REFINADO_SIN_ESQUEMA

    def test_prompt_pide_agrupar_valores_de_la_misma_frase(self):
        assert "misma frase" in gemini.PROMPT_SISTEMA and "un solo momento" in gemini.PROMPT_SISTEMA

    def test_prompt_pide_leer_textos_e_iconos(self):
        sistema = gemini.PROMPT_SISTEMA.lower()
        assert "texto" in sistema and "icono" in sistema and "no legible" in sistema
        assert "valores" in sistema and "unidades" in sistema

    def test_prompt_refinado(self):
        sistema = gemini.construir_prompt_refinado("arco en C")
        assert "arco en C" in sistema and "{equipo}" not in sistema
        assert config.EQUIPO_POR_DEFECTO in gemini.construir_prompt_refinado("")
        usuario = gemini.PROMPT_REFINADO_USUARIO
        assert "NO cambies los tiempos" in usuario and "NO agregues ni quites pasos" in usuario and "NO inventes" in usuario
        assert "mismo número" in usuario
        assert gemini.PROMPT_CAPTURA.format(numero=3, tiempo="01:05") == "Captura del paso 3 (01:05)"
        assert re.search(r"[\U0001F300-\U0001FAFF☀-➿]", sistema + usuario) is None

    def test_esquema_refinado(self):
        esquema = gemini.ESQUEMA_REFINADO
        json.dumps(esquema)
        assert esquema["required"] == ["momentos"]
        item = esquema["properties"]["momentos"]["items"]
        assert item["required"] == ["numero", "titulo", "descripcion"]
        assert item["properties"]["numero"]["type"] == "integer"
        # zona: la del análisis o null ("no señalar": el elemento no aparece en la captura)
        assert item["properties"]["zona"] == {"anyOf": [gemini.ESQUEMA_ZONA, {"type": "null"}]}
        assert gemini.ESQUEMA_RESPUESTA["properties"]["momentos"]["items"]["properties"]["zona"] == gemini.ESQUEMA_ZONA
        assert "tiempo" not in item["properties"]
        cfg = gemini._construir_config("sistema", 100, esquema_json=esquema)
        assert cfg.response_json_schema == esquema and cfg.response_json_schema is not esquema

    def test_resoluciones_publicas(self):
        assert gemini.RESOLUCIONES == {
            "baja": types.MediaResolution.MEDIA_RESOLUTION_LOW,
            "media": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
            "alta": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        }
        assert config.RESOLUCION_VIDEO in gemini.RESOLUCIONES
        assert gemini._construir_config("s", 1, resolucion=None).media_resolution is None
        with pytest.raises(ValueError, match="baja, media, alta"):
            gemini._construir_config("s", 1, resolucion="ultra")


# ----------------------------------------------------------------------------
# Cliente y subida
# ----------------------------------------------------------------------------

class TestClienteYSubida:
    def test_crear_cliente(self, monkeypatch):
        recibido = {}

        class ClienteRegistro:
            def __init__(self, **kwargs):
                recibido.update(kwargs)
        monkeypatch.setattr(gemini.genai, "Client", ClienteRegistro)
        gemini.crear_cliente("clave-falsa", timeout_ms=1234)
        assert recibido["api_key"] == "clave-falsa"
        assert recibido["http_options"].timeout == 1234
        assert isinstance(recibido["http_options"].retry_options, types.HttpRetryOptions)

    def test_crear_cliente_sin_clave(self):
        with pytest.raises(ValueError):
            gemini.crear_cliente("")

    def test_obtener_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert gemini.obtener_api_key() is None
        monkeypatch.setenv("GOOGLE_API_KEY", "google")
        assert gemini.obtener_api_key() == "google"
        monkeypatch.setenv("GEMINI_API_KEY", "   ")
        assert gemini.obtener_api_key() == "google"
        monkeypatch.setenv("GEMINI_API_KEY", "gemini")
        assert gemini.obtener_api_key() == "gemini"

    def test_subida_processing_a_active(self, tmp_path):
        ruta = tmp_path / "video.mp4"
        ruta.write_bytes(b"0" * 100)
        cliente = ClienteFalso(estados_archivo=[types.FileState.PROCESSING, types.FileState.PROCESSING, types.FileState.ACTIVE])
        lineas, log = registro()
        archivo = gemini.subir_video(cliente, ruta, "video", timeout_procesado=60, intervalo=0, log=log)
        assert archivo.state == types.FileState.ACTIVE and archivo.name == "files/subido"
        subida = cliente.llamadas[0]
        assert subida["tipo"] == "upload" and subida["file"] == str(ruta)
        assert subida["config"].mime_type == "video/mp4" and subida["config"].display_name == "video"
        assert [l["tipo"] for l in cliente.llamadas[1:]] == ["get", "get"]
        assert any("ACTIVE" in l for l in lineas)

    def test_subida_failed(self, tmp_path):
        ruta = tmp_path / "video.mov"
        ruta.write_bytes(b"0" * 100)
        cliente = ClienteFalso(estados_archivo=[types.FileState.PROCESSING, types.FileState.FAILED])
        with pytest.raises(RuntimeError, match="códec no soportado"):
            gemini.subir_video(cliente, ruta, "video", intervalo=0, log=lambda _: None)
        assert cliente.llamadas[0]["config"].mime_type == "video/quicktime"
        assert cliente.files.eliminados == ["files/subido"]      # G9: la copia no se queda en Google

    def test_subida_timeout(self, tmp_path):
        ruta = tmp_path / "video.mp4"
        ruta.write_bytes(b"0" * 100)
        cliente = ClienteFalso(estados_archivo=[types.FileState.PROCESSING])
        with pytest.raises(RuntimeError, match="PROCESSING"):
            gemini.subir_video(cliente, ruta, "video", timeout_procesado=0, intervalo=0, log=lambda _: None)
        assert cliente.files.eliminados == ["files/subido"]

    def test_subida_interrumpida_borra_el_remoto(self, tmp_path, monkeypatch):
        ruta = tmp_path / "video.mp4"
        ruta.write_bytes(b"0" * 100)
        cliente = ClienteFalso(estados_archivo=[types.FileState.PROCESSING, types.FileState.PROCESSING])

        def ctrl_c(**kw):
            raise KeyboardInterrupt
        monkeypatch.setattr(cliente.files, "get", ctrl_c)
        with pytest.raises(KeyboardInterrupt):
            gemini.subir_video(cliente, ruta, "video", timeout_procesado=60, intervalo=0, log=lambda _: None)
        assert cliente.files.eliminados == ["files/subido"]
        # con --conservar-subida no se borra (y se avisa)
        cliente = ClienteFalso(estados_archivo=[types.FileState.PROCESSING, types.FileState.FAILED])
        lineas, log = registro()
        with pytest.raises(RuntimeError):
            gemini.subir_video(cliente, ruta, "video", intervalo=0, conservar_subida=True, log=log)
        assert cliente.files.eliminados == [] and any("conservado" in l for l in lineas)
        # el borrado que falla no oculta el error original
        cliente = ClienteFalso(estados_archivo=[types.FileState.PROCESSING, types.FileState.FAILED])
        cliente.files.fallar_delete = True
        with pytest.raises(RuntimeError, match="códec no soportado"):
            gemini.subir_video(cliente, ruta, "video", intervalo=0, log=lambda _: None)

    def test_subida_extension_sin_mime(self, tmp_path):
        ruta = tmp_path / "video.mkv"
        ruta.write_bytes(b"0")
        with pytest.raises(ValueError, match="MIME"):
            gemini.subir_video(ClienteFalso(), ruta, "video", log=lambda _: None)

    def test_subida_archivo_inexistente(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            gemini.subir_video(ClienteFalso(), tmp_path / "no.mp4", "no", log=lambda _: None)

    def test_eliminar_archivo_nunca_lanza(self):
        cliente = ClienteFalso()
        lineas, log = registro()
        gemini.eliminar_archivo(cliente, archivo_remoto("files/uno"), log=log)
        assert cliente.files.eliminados == ["files/uno"]
        cliente.files.fallar_delete = True
        gemini.eliminar_archivo(cliente, "files/dos", log=log)      # también acepta el nombre
        gemini.eliminar_archivo(cliente, None, log=log)
        assert any("no se pudo eliminar" in l for l in lineas)


# ----------------------------------------------------------------------------
# analizar_video
# ----------------------------------------------------------------------------

class TestAnalizarVideo:
    def test_respuesta_con_fences(self):
        momentos = [momento_json("00:10", "Encender el equipo", 5, zona={"x": 500, "y": 250}),
                    momento_json("00:30", "Ajustar el colimador", 4, "Ajustes"),
                    momento_json("01:05", "Guardar la imagen", 3, "Adquisición")]
        texto = "Aquí tienes el resultado:\n```json\n" + documento_json(momentos) + "\n```\nEspero que sirva."
        cliente = ClienteFalso([respuesta(texto)])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), equipo="arco en C", log=lambda _: None)

        assert isinstance(resultado, ResultadoAnalisis)
        assert resultado.modo == "gemini" and resultado.modelo == MODELO and resultado.tramos == 1
        assert [m.tiempo_seg for m in resultado.momentos] == [10.0, 30.0, 65.0]
        assert resultado.momentos[0].zona == {"x": 0.5, "y": 0.25} and resultado.momentos[1].zona is None
        assert resultado.momentos[0].importancia == 5 and resultado.momentos[0].seccion == "Preparación"
        assert resultado.titulo == "Manual del arco en C" and resultado.resumen.startswith("El video")
        assert resultado.truncado is False and resultado.texto_bruto == texto
        assert resultado.uso.tokens_entrada == 1000 and resultado.uso.tokens_salida == 200
        assert resultado.uso.tokens_pensamiento == 50 and resultado.uso.llamadas == 1
        assert resultado.uso.detalle_entrada == {"VIDEO": 900, "AUDIO": 60, "TEXT": 40}
        assert resultado.uso.costo_usd == pytest.approx((1000 * 0.25 + 250 * 1.50) / 1e6)

        llamada = cliente.generaciones()[0]
        assert llamada["modelo"] == MODELO
        cfg = llamada["config"]
        assert "arco en C" in cfg.system_instruction and "instructor" in cfg.system_instruction
        assert cfg.response_json_schema == gemini.ESQUEMA_RESPUESTA and cfg.response_json_schema is not gemini.ESQUEMA_RESPUESTA
        assert cfg.response_mime_type == "application/json"
        assert cfg.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_MEDIUM     # config.RESOLUCION_VIDEO
        assert cfg.thinking_config.thinking_level == types.ThinkingLevel.LOW
        assert cfg.max_output_tokens == config.MAX_TOKENS_SALIDA and cfg.temperature == config.TEMPERATURA
        partes = llamada["contents"][0].parts
        assert llamada["contents"][0].role == "user"
        assert partes[0].file_data.file_uri == archivo_remoto().uri and partes[0].file_data.mime_type == "video/mp4"
        assert partes[0].video_metadata is None
        assert partes[1].text == gemini.PROMPT_USUARIO

    def test_parsed_dict_se_usa_directo(self):
        datos = {"titulo_video": "T", "resumen": "R", "momentos": [momento_json("00:05")]}
        cliente = ClienteFalso([respuesta("no importa este texto", parsed=datos)])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(resultado.momentos) == 1 and resultado.titulo == "T"

    def test_fps_produce_video_metadata(self):
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:05")]))])
        gemini.analizar_video(cliente, archivo_remoto(), info_video(), fps=0.5, log=lambda _: None)
        meta = cliente.generaciones()[0]["contents"][0].parts[0].video_metadata
        assert meta.fps == 0.5 and meta.start_offset is None and meta.end_offset is None

    def test_sin_usage_metadata(self):
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:05")]), con_uso=False)])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert resultado.uso.tokens_total == 0 and resultado.uso.llamadas == 1 and resultado.uso.costo_usd == 0

    def test_filtros_max_momentos_e_importancia(self):
        momentos = [momento_json("00:05", "A", 2), momento_json("00:20", "B", 5), momento_json("00:40", "C", 4),
                    momento_json("01:00", "D", 1)]
        cliente = ClienteFalso([respuesta(documento_json(momentos))])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), max_momentos=2,
                                          importancia_minima=2, log=lambda _: None)
        assert [m.titulo for m in resultado.momentos] == ["B", "C"]

    def test_truncada_con_reintento(self):
        completo = documento_json([momento_json("00:10", "A"), momento_json("00:20", "B"), momento_json("00:30", "C")])
        cortado = completo[: completo.index('"tiempo": "00:30"') + 12]
        cliente = ClienteFalso([respuesta(cortado, finish=types.FinishReason.MAX_TOKENS), respuesta(completo)])
        lineas, log = registro()
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=log)
        llamadas = cliente.generaciones()
        assert len(llamadas) == 2
        assert llamadas[0]["config"].max_output_tokens == config.MAX_TOKENS_SALIDA
        assert llamadas[1]["config"].max_output_tokens == config.MAX_TOKENS_SALIDA * 2
        assert llamadas[1]["modelo"] == MODELO
        assert resultado.truncado is False and len(resultado.momentos) == 3
        assert resultado.uso.llamadas == 2 and resultado.uso.tokens_entrada == 2000
        assert any("MAX_TOKENS" in l for l in lineas)

    def test_json_truncado_sin_max_tokens_tambien_reintenta(self):
        completo = documento_json([momento_json("00:10", "A"), momento_json("00:20", "B")])
        cortado = completo[: completo.index('"tiempo": "00:20"') + 5]
        cliente = ClienteFalso([respuesta(cortado), respuesta(completo)])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == 2 and len(resultado.momentos) == 2 and not resultado.truncado

    def test_truncada_persistente_conserva_rescatado(self):
        completo = documento_json([momento_json("00:10", "A"), momento_json("00:20", "B"), momento_json("00:30", "C")])
        corte1 = completo[: completo.index('"tiempo": "00:20"') + 5]      # 1 momento completo
        corte2 = completo[: completo.index('"tiempo": "00:30"') + 5]      # 2 momentos completos
        cliente = ClienteFalso([respuesta(corte1, finish=types.FinishReason.MAX_TOKENS),
                                respuesta(corte2, finish=types.FinishReason.MAX_TOKENS)])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == 2
        assert resultado.truncado is True
        assert [m.titulo for m in resultado.momentos] == ["A", "B"]
        assert any("cortada" in a for a in resultado.avisos)

    def test_max_tokens_con_json_completo_reintenta_una_vez(self):
        completo = documento_json([momento_json("00:10", "A")])
        cliente = ClienteFalso([respuesta(completo, finish=types.FinishReason.MAX_TOKENS), respuesta(completo)])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == 2 and len(resultado.momentos) == 1 and not resultado.truncado

    @pytest.mark.parametrize("partes", [[], None, [types.Part(text="pensando…", thought=True)]])
    def test_max_tokens_sin_texto_reintenta_una_vez(self, partes):
        """G1: el pensamiento consumió el presupuesto (sin texto) → reintento con el doble de tokens, no error."""
        vacia = types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=partes), finish_reason=types.FinishReason.MAX_TOKENS)],
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=1000, thoughts_token_count=16000,
                                                                      total_token_count=17000))
        completo = documento_json([momento_json("00:10", "A"), momento_json("00:20", "B")])
        cliente = ClienteFalso([vacia, respuesta(completo)])
        lineas, log = registro()
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=log)
        llamadas = cliente.generaciones()
        assert len(llamadas) == 2 and llamadas[1]["config"].max_output_tokens == config.MAX_TOKENS_SALIDA * 2
        assert [m.titulo for m in resultado.momentos] == ["A", "B"] and not resultado.truncado
        assert resultado.uso.llamadas == 2 and resultado.uso.tokens_pensamiento == 16050
        assert any("MAX_TOKENS" in l and "pensamiento" in l for l in lineas)

    def test_max_tokens_sin_texto_dos_veces_propaga(self):
        vacia = types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[]), finish_reason=types.FinishReason.MAX_TOKENS)])
        cliente = ClienteFalso([vacia, vacia])
        with pytest.raises(RuntimeError, match="MAX_TOKENS.*pensamiento"):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == 2

    def test_reintento_falla_conserva_rescatado(self):
        completo = documento_json([momento_json("00:10", "A"), momento_json("00:20", "B")])
        cortado = completo[: completo.index('"tiempo": "00:20"') + 5]
        cliente = ClienteFalso([respuesta(cortado, finish=types.FinishReason.MAX_TOKENS),
                                error_cliente(400, "max_output_tokens too large")])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert resultado.truncado and [m.titulo for m in resultado.momentos] == ["A"]
        assert any("reintento" in a for a in resultado.avisos)

    def test_respuesta_bloqueada(self):
        bloqueada = types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason.SAFETY))
        with pytest.raises(RuntimeError, match="SAFETY"):
            gemini.analizar_video(ClienteFalso([bloqueada]), archivo_remoto(), info_video(), log=lambda _: None)

    def test_respuesta_sin_texto_con_finish_reason(self):
        cliente = ClienteFalso([respuesta(None, finish=types.FinishReason.RECITATION)])
        with pytest.raises(RuntimeError, match="RECITATION"):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)

    def test_respuesta_sin_json(self):
        cliente = ClienteFalso([respuesta("No puedo analizar este video.")])
        with pytest.raises(RuntimeError, match="JSON"):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)


class TestTemperatura:
    def test_analisis_refinado_redactor_y_lote(self, tmp_path):
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:05")]))] * 2)
        gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        gemini.analizar_video(cliente, archivo_remoto(), info_video(), temperatura=0.4, log=lambda _: None)
        assert [l["config"].temperature for l in cliente.generaciones()] == [None, 0.4]
        original = resultado_con_capturas(tmp_path)
        cliente = ClienteFalso([respuesta(refinado_json([{"numero": 1, "titulo": "Uno", "descripcion": "d"}]))])
        gemini.refinar_con_capturas(cliente, original, MODELO, temperatura=0.2, log=lambda _: None)
        assert cliente.generaciones()[0]["config"].temperature == 0.2
        pulido = json.dumps({"titulo_video": "T", "resumen": "R", "momentos": [
            {"tiempo": "00:10", "titulo": "X", "descripcion": "Y", "importancia": 5, "fuente": "ambos", "seccion": "S"},
            {"tiempo": "00:30", "titulo": "X", "descripcion": "Y", "importancia": 4, "fuente": "audio", "seccion": "S"}]})
        cliente = ClienteFalso([respuesta(pulido)])
        gemini.pulir_redaccion(cliente, resultado_base(), "gemini-pro", temperatura=1.0, log=lambda _: None)
        assert cliente.generaciones()[0]["config"].temperature == 1.0
        cliente = ClienteFalso()
        peticiones = [{"archivo": archivo_remoto(), "info": info_video(), "tramo": None}]
        gemini.enviar_lote(cliente, peticiones, MODELO, "lote", log=lambda _: None)
        gemini.enviar_lote(cliente, peticiones, MODELO, "lote", temperatura=0.5, log=lambda _: None)
        lotes = [l for l in cliente.llamadas if l["tipo"] == "batch_create"]
        assert [l["src"][0].config.temperature for l in lotes] == [None, 0.5]


class TestEscaleraFallbacks:
    def test_escalera_completa(self):
        exito = respuesta(documento_json([momento_json("00:10")]))
        cliente = ClienteFalso([
            error_cliente(400, "Invalid value at 'generation_config.thinking_config.thinking_level'"),
            error_cliente(400, "mediaResolution is not supported by this model"),
            error_cliente(404, f"models/{MODELO} is not found for API version v1beta, or is not supported for generateContent."),
            exito,
        ])
        lineas, log = registro()
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=log)
        llamadas = cliente.generaciones()
        assert [l["modelo"] for l in llamadas] == [MODELO, MODELO, MODELO, ALTERNATIVO]
        assert llamadas[0]["config"].thinking_config is not None and llamadas[0]["config"].media_resolution is not None
        assert llamadas[1]["config"].thinking_config is None and llamadas[1]["config"].media_resolution is not None
        assert llamadas[2]["config"].thinking_config is None and llamadas[2]["config"].media_resolution is None
        assert llamadas[2]["config"].response_json_schema is not None
        # el nuevo modelo vuelve a empezar la escalera con todas las opciones
        assert llamadas[3]["config"].thinking_config is not None and llamadas[3]["config"].media_resolution is not None
        assert resultado.modelo == ALTERNATIVO and resultado.uso.modelo == ALTERNATIVO
        assert len(resultado.avisos) >= 3
        assert any("thinking" in a for a in resultado.avisos)
        assert any("media_resolution" in a for a in resultado.avisos)
        assert any(ALTERNATIVO in a for a in resultado.avisos)
        assert any("Aviso" in l for l in lineas)

    def test_fallback_sin_esquema(self):
        exito = respuesta(documento_json([momento_json("00:10")]))
        cliente = ClienteFalso([error_cliente(400, "responseJsonSchema is not supported"), exito])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        llamadas = cliente.generaciones()
        assert llamadas[0]["config"].response_json_schema is not None
        assert llamadas[1]["config"].response_json_schema is None
        assert llamadas[1]["config"].response_mime_type == "application/json"
        assert llamadas[1]["config"].thinking_config is not None
        assert "Responde SOLO con el JSON" in llamadas[1]["contents"][0].parts[1].text
        assert llamadas[0]["contents"][0].parts[1].text == gemini.PROMPT_USUARIO
        assert len(resultado.momentos) == 1 and resultado.modelo == MODELO

    def test_reintento_por_tokens_conserva_la_variante(self):
        completo = documento_json([momento_json("00:10", "A"), momento_json("00:20", "B")])
        cortado = completo[: completo.index('"tiempo": "00:20"') + 5]
        cliente = ClienteFalso([error_cliente(400, "thinking not supported"),
                                respuesta(cortado, finish=types.FinishReason.MAX_TOKENS), respuesta(completo)])
        gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        llamadas = cliente.generaciones()
        assert len(llamadas) == 3 and llamadas[2]["config"].thinking_config is None
        assert llamadas[2]["config"].max_output_tokens == config.MAX_TOKENS_SALIDA * 2

    def test_404_en_ultimo_modelo_se_propaga(self):
        cliente = ClienteFalso([error_cliente(404, "not found")] * (1 + len(config.MODELOS_ALTERNATIVOS)))
        with pytest.raises(errors.ClientError):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert [l["modelo"] for l in cliente.generaciones()] == [MODELO] + config.MODELOS_ALTERNATIVOS

    def test_modelo_alternativo_como_principal_no_se_repite(self):
        cliente = ClienteFalso([error_cliente(404, "not found")] * len(config.MODELOS_ALTERNATIVOS))
        with pytest.raises(errors.ClientError):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), modelo=ALTERNATIVO, log=lambda _: None)
        modelos = [l["modelo"] for l in cliente.generaciones()]
        assert modelos == [ALTERNATIVO] + [m for m in config.MODELOS_ALTERNATIVOS if m != ALTERNATIVO]

    def test_otros_errores_se_propagan(self, monkeypatch):
        cliente = ClienteFalso([error_cliente(400, "API key not valid. Please pass a valid API key.")])
        with pytest.raises(errors.ClientError):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == 1
        # un 5xx se reintenta (REINTENTOS_RED veces, con pausa) y solo entonces se propaga
        monkeypatch.setattr(gemini.time, "sleep", lambda _s: None)
        servidor = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        cliente = ClienteFalso([servidor] * (gemini.REINTENTOS_RED + 1))
        with pytest.raises(errors.ServerError):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == gemini.REINTENTOS_RED + 1


# ----------------------------------------------------------------------------
# Resolución del video (media_resolution)
# ----------------------------------------------------------------------------

class TestResolucion:
    def test_media_por_defecto(self):
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:05")]))])
        lineas, log = registro()
        gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=log)
        cfg = cliente.generaciones()[0]["config"]
        assert config.RESOLUCION_VIDEO == "media"
        assert cfg.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_MEDIUM
        assert resolucion_en_wire(cfg) == "MEDIA_RESOLUTION_MEDIUM"
        assert any("resolución media" in l for l in lineas)

    @pytest.mark.parametrize("nombre, esperado", [
        ("baja", "MEDIA_RESOLUTION_LOW"), ("media", "MEDIA_RESOLUTION_MEDIUM"), ("alta", "MEDIA_RESOLUTION_HIGH"),
        (" Alta ", "MEDIA_RESOLUTION_HIGH"),
    ])
    def test_baja_media_alta(self, nombre, esperado):
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:05")]))])
        gemini.analizar_video(cliente, archivo_remoto(), info_video(), resolucion=nombre, log=lambda _: None)
        cfg = cliente.generaciones()[0]["config"]
        assert resolucion_en_wire(cfg) == esperado and cfg.media_resolution == types.MediaResolution(esperado)

    def test_resolucion_invalida_no_llama(self):
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:05")]))])
        with pytest.raises(ValueError, match="ultra"):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), resolucion="ultra", log=lambda _: None)
        assert cliente.generaciones() == []
        with pytest.raises(ValueError):
            gemini.enviar_lote(cliente, [{"archivo": archivo_remoto(), "info": info_video(), "tramo": None}], MODELO,
                               "lote", resolucion="", log=lambda _: None)
        assert cliente.llamadas == []

    def test_fallback_quita_media_resolution_con_cualquier_valor(self):
        exito = respuesta(documento_json([momento_json("00:10")]))
        cliente = ClienteFalso([error_cliente(400, "media_resolution is not supported"),
                                error_cliente(404, "not found"), exito])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), resolucion="alta", log=lambda _: None)
        llamadas = cliente.generaciones()
        assert [l["modelo"] for l in llamadas] == [MODELO, MODELO, ALTERNATIVO]
        assert resolucion_en_wire(llamadas[0]["config"]) == "MEDIA_RESOLUTION_HIGH"
        assert llamadas[1]["config"].media_resolution is None and llamadas[1]["config"].thinking_config is not None
        # el siguiente modelo vuelve a empezar con la resolución pedida, no con la de config
        assert resolucion_en_wire(llamadas[2]["config"]) == "MEDIA_RESOLUTION_HIGH"
        assert resultado.modelo == ALTERNATIVO and any("media_resolution" in a for a in resultado.avisos)

    def test_tramos_conservan_la_resolucion(self):
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:10")])),
                                respuesta(documento_json([momento_json("00:10")]))])
        gemini.analizar_video(cliente, archivo_remoto(), info_video(duracion=5500.0), resolucion="baja", log=lambda _: None)
        assert [resolucion_en_wire(l["config"]) for l in cliente.generaciones()] == ["MEDIA_RESOLUTION_LOW"] * 2

    def test_enviar_lote_con_resolucion(self):
        cliente = ClienteFalso()
        peticiones = [{"archivo": archivo_remoto(), "info": info_video(), "tramo": None}]
        gemini.enviar_lote(cliente, peticiones, MODELO, "lote", log=lambda _: None)
        gemini.enviar_lote(cliente, peticiones, MODELO, "lote", resolucion="alta", log=lambda _: None)
        lotes = [l for l in cliente.llamadas if l["tipo"] == "batch_create"]
        assert resolucion_en_wire(lotes[0]["src"][0].config) == "MEDIA_RESOLUTION_MEDIUM"
        assert resolucion_en_wire(lotes[1]["src"][0].config) == "MEDIA_RESOLUTION_HIGH"


# ----------------------------------------------------------------------------
# Tramos
# ----------------------------------------------------------------------------

class TestTramos:
    def test_calcular_tramos(self):
        assert gemini.calcular_tramos(90, 2700) == [(0.0, 90.0)]
        assert gemini.calcular_tramos(2700, 2700) == [(0.0, 2700.0)]
        assert gemini.calcular_tramos(6000, 2700) == [(0.0, 2700.0), (2700.0, 5400.0), (5400.0, 6000.0)]
        assert gemini.calcular_tramos(2900, 2700) == [(0.0, 2900.0)]              # último < 5 min se fusiona
        assert gemini.calcular_tramos(5500, 2700) == [(0.0, 2700.0), (2700.0, 5500.0)]
        assert gemini.calcular_tramos(6000, None) == [(0.0, 6000.0)]
        assert gemini.calcular_tramos(6000, 0) == [(0.0, 6000.0)]

    def test_tramos_con_heuristica_de_tiempos(self):
        info = info_video(duracion=6000.0)      # 100 min → 3 tramos de 45, 45 y 10 min
        r1 = respuesta(documento_json([momento_json("00:10", "T1a"), momento_json("10:00", "T1b")], resumen="Resumen uno"))
        r2 = respuesta(documento_json([momento_json("00:10", "T2a")], resumen="Resumen dos, un poco más largo"))
        r3 = respuesta(documento_json([momento_json("91:00", "T3a")], resumen=""))   # absolutos: 5460 > 600 + 5
        cliente = ClienteFalso([r1, r2, r3])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info, log=lambda _: None)
        assert resultado.tramos == 3 and resultado.uso.llamadas == 3
        assert [m.titulo for m in resultado.momentos] == ["T1a", "T1b", "T2a", "T3a"]
        assert [m.tiempo_seg for m in resultado.momentos] == [10.0, 600.0, 2710.0, 5460.0]
        assert resultado.resumen == "Resumen uno"
        assert any("absolutos" in a for a in resultado.avisos)
        llamadas = cliente.generaciones()
        metas = [l["contents"][0].parts[0].video_metadata for l in llamadas]
        assert [(m.start_offset, m.end_offset) for m in metas] == [("0s", "2700s"), ("2700s", "5400s"), ("5400s", "6000s")]
        assert "45:00–90:00" in llamadas[1]["contents"][0].parts[1].text
        assert "--- tramo" in resultado.texto_bruto

    def test_tramos_fallback_de_modelo_solo_una_vez(self):
        info = info_video(duracion=5500.0)
        cliente = ClienteFalso([error_cliente(404, "not found"),
                                respuesta(documento_json([momento_json("00:10", "A")])),
                                respuesta(documento_json([momento_json("00:10", "B")]))])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info, log=lambda _: None)
        assert [l["modelo"] for l in cliente.generaciones()] == [MODELO, ALTERNATIVO, ALTERNATIVO]
        assert resultado.modelo == ALTERNATIVO and [m.tiempo_seg for m in resultado.momentos] == [10.0, 2710.0]

    def test_tramos_resumen_mas_largo_si_el_primero_falta(self):
        info = info_video(duracion=5500.0)
        cliente = ClienteFalso([respuesta(documento_json([momento_json("00:10")], resumen="")),
                                respuesta(documento_json([momento_json("00:10")], resumen="Segundo resumen"))])
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info, log=lambda _: None)
        assert resultado.resumen == "Segundo resumen"


# ----------------------------------------------------------------------------
# Batch
# ----------------------------------------------------------------------------

class TestBatch:
    def test_enviar_lote(self):
        cliente = ClienteFalso()
        info_a, info_b = info_video(90, "video_a"), info_video(5500, "video_b")
        peticiones = [
            {"archivo": archivo_remoto("files/a"), "info": info_a, "tramo": None},
            {"archivo": archivo_remoto("files/b"), "info": info_b, "tramo": (0.0, 2700.0)},
            {"archivo": archivo_remoto("files/b"), "info": info_b, "tramo": (2700.0, 5500.0), "fps": 0.5},
        ]
        lineas, log = registro()
        job = gemini.enviar_lote(cliente, peticiones, MODELO, "lote de prueba", equipo="acelerador lineal", log=log)
        assert job.name == "batches/lote1" and job.state == types.JobState.JOB_STATE_PENDING
        llamada = cliente.llamadas[-1]
        assert llamada["tipo"] == "batch_create" and llamada["modelo"] == MODELO
        assert llamada["config"].display_name == "lote de prueba"
        src = llamada["src"]
        assert len(src) == 3 and all(isinstance(s, types.InlinedRequest) for s in src)
        assert src[0].metadata == {"video": "video_a", "tramo": "0", "inicio": "0", "fin": "90"}
        assert src[1].metadata == {"video": "video_b", "tramo": "0", "inicio": "0", "fin": "2700"}
        assert src[2].metadata == {"video": "video_b", "tramo": "1", "inicio": "2700", "fin": "5500"}
        assert src[0].contents[0].parts[0].video_metadata is None
        assert src[2].contents[0].parts[0].video_metadata.start_offset == "2700s"
        assert src[2].contents[0].parts[0].video_metadata.fps == 0.5
        assert "45:00–91:40" in src[2].contents[0].parts[1].text
        assert src[0].config.response_json_schema == gemini.ESQUEMA_RESPUESTA
        assert "acelerador lineal" in src[0].config.system_instruction
        assert any("Lote enviado" in l for l in lineas)
        with pytest.raises(ValueError):
            gemini.enviar_lote(cliente, [], MODELO, "vacío", log=log)

    def test_estado_y_terminado(self):
        job = types.BatchJob(name="batches/lote1", state=types.JobState.JOB_STATE_RUNNING)
        cliente = ClienteFalso(job=job)
        assert gemini.estado_lote(cliente, "batches/lote1") is job
        assert cliente.llamadas[-1] == {"tipo": "batch_get", "name": "batches/lote1"}
        assert not gemini.lote_terminado(job)
        for estado in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED", "JOB_STATE_FAILED",
                       "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"):
            assert gemini.lote_terminado(types.BatchJob(state=types.JobState(estado)))
        for estado in ("JOB_STATE_PENDING", "JOB_STATE_QUEUED", "JOB_STATE_RUNNING"):
            assert not gemini.lote_terminado(types.BatchJob(state=types.JobState(estado)))
        assert not gemini.lote_terminado(types.BatchJob())

    def test_recoger_lote_mapeo_por_metadata_y_error_por_item(self):
        info_a, info_b, info_c = info_video(90, "video_a"), info_video(120, "video_b"), info_video(60, "video_c")
        # respuestas en orden distinto al del mapa; parsed None como en batch real
        respuestas = [
            types.InlinedResponse(response=respuesta(documento_json([momento_json("00:20", "B1")])), metadata={"video": "video_b", "tramo": "0"}),
            types.InlinedResponse(error=types.JobError(code=13, message="internal error"), metadata={"video": "video_c", "tramo": "0"}),
            types.InlinedResponse(response=respuesta(documento_json([momento_json("00:10", "A1"), momento_json("00:30", "A2")])), metadata={"video": "video_a", "tramo": "0"}),
        ]
        job = types.BatchJob(name="batches/lote1", state=types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
                             model=f"models/{MODELO}", dest=types.BatchJobDestination(inlined_responses=respuestas))
        mapa = {"video_a": {"info": info_a}, "video_b": {"info": info_b}, "video_c": {"info": info_c},
                "video_d": {"info": info_video(30, "video_d")}}
        resultados = gemini.recoger_lote(ClienteFalso(), job, mapa, log=lambda _: None)
        assert set(resultados) == {"video_a", "video_b", "video_c", "video_d"}
        a, b = resultados["video_a"], resultados["video_b"]
        assert isinstance(a, ResultadoAnalisis) and [m.titulo for m in a.momentos] == ["A1", "A2"]
        assert [m.titulo for m in b.momentos] == ["B1"]
        assert a.modo == "gemini-batch" and a.modelo == MODELO and a.tramos == 1
        assert a.uso.batch is True and a.uso.llamadas == 1
        assert a.uso.costo_usd == pytest.approx(((1000 * 0.25 + 250 * 1.50) / 1e6) * config.DESCUENTO_BATCH)
        assert isinstance(resultados["video_c"], Exception) and "internal error" in str(resultados["video_c"])
        assert isinstance(resultados["video_d"], Exception)

    def test_recoger_lote_por_indice_sin_metadata_y_tramos(self):
        info_a, info_b = info_video(90, "video_a"), info_video(5500, "video_b")
        respuestas = [
            types.InlinedResponse(response=respuesta(documento_json([momento_json("00:10", "A1")]))),
            types.InlinedResponse(response=respuesta(documento_json([momento_json("00:10", "B1")]))),
            types.InlinedResponse(response=respuesta(documento_json([momento_json("00:10", "B2")]))),
        ]
        job = types.BatchJob(state=types.JobState.JOB_STATE_SUCCEEDED, model=MODELO,
                             dest=types.BatchJobDestination(inlined_responses=respuestas))
        mapa = {"video_a": {"info": info_a}, "video_b": {"info": info_b, "tramos": [[0, 2700], [2700, 5500]]}}
        resultados = gemini.recoger_lote(ClienteFalso(), job, mapa, log=lambda _: None)
        assert [m.titulo for m in resultados["video_a"].momentos] == ["A1"]
        b = resultados["video_b"]
        assert b.tramos == 2 and [m.tiempo_seg for m in b.momentos] == [10.0, 2710.0]
        assert b.uso.llamadas == 2 and b.uso.tokens_entrada == 2000

    def test_recoger_lote_fallido_sin_respuestas(self):
        job = types.BatchJob(state=types.JobState.JOB_STATE_FAILED, error=types.JobError(code=8, message="cuota agotada"))
        resultados = gemini.recoger_lote(ClienteFalso(), job, {"v": {"info": info_video()}}, log=lambda _: None)
        assert isinstance(resultados["v"], Exception) and "cuota agotada" in str(resultados["v"])
        assert "--batch" in str(resultados["v"])        # P-H10: qué hacer a continuación
        expirado = types.BatchJob(state=types.JobState.JOB_STATE_EXPIRED)
        error = gemini.recoger_lote(ClienteFalso(), expirado, {"v": {"info": info_video()}}, log=lambda _: None)["v"]
        assert "JOB_STATE_EXPIRED" in str(error) and "JobState." not in str(error) and "--batch" in str(error)

    def test_enviar_lote_reintenta_sin_la_opcion_rechazada(self):
        """G7: el batch no tiene escalera; si ``batches.create`` rechaza una opción se reenvía sin ella (máx. 2)."""
        class LotesQueRechazan(_Lotes):
            def __init__(self, cliente, errores):
                super().__init__(cliente)
                self.errores = list(errores)

            def create(self, *, model, src, config=None):
                self._cliente.llamadas.append({"tipo": "batch_create", "modelo": model, "src": src, "config": config})
                if self.errores:
                    raise self.errores.pop(0)
                return types.BatchJob(name="batches/lote1", state=types.JobState.JOB_STATE_PENDING, model=f"models/{model}")

        cliente = ClienteFalso()
        cliente.batches = LotesQueRechazan(cliente, [error_cliente(400, "thinking_level is not supported"),
                                                    error_cliente(400, "responseJsonSchema is not supported")])
        peticiones = [{"archivo": archivo_remoto(), "info": info_video(), "tramo": None}]
        opciones = gemini.OpcionesLote()
        lineas, log = registro()
        job = gemini.enviar_lote(cliente, peticiones, MODELO, "lote", opciones_lote=opciones, log=log)
        lotes = [l for l in cliente.llamadas if l["tipo"] == "batch_create"]
        assert job.name == "batches/lote1" and len(lotes) == 3
        assert lotes[0]["src"][0].config.thinking_config is not None and lotes[0]["src"][0].config.response_json_schema is not None
        assert lotes[1]["src"][0].config.thinking_config is None and lotes[1]["src"][0].config.response_json_schema is not None
        assert lotes[2]["src"][0].config.thinking_config is None and lotes[2]["src"][0].config.response_json_schema is None
        assert lotes[2]["src"][0].config.media_resolution is not None
        assert "Responde SOLO con el JSON" in lotes[2]["src"][0].contents[0].parts[1].text
        assert gemini.PROMPT_SIN_ESQUEMA not in lotes[1]["src"][0].contents[0].parts[1].text
        assert (opciones.thinking, opciones.esquema, opciones.con_resolucion, opciones.reintentos) == (False, False, True, 2)
        assert len(opciones.avisos) == 2 and all("se reenvía" in a for a in opciones.avisos)
        assert opciones.a_dict()["reintentos"] == 2 and gemini.OpcionesLote.desde_dict(opciones.a_dict()) == opciones
        assert sum("Aviso" in l for l in lineas) == 2
        # tercer rechazo: ya no quedan reintentos → se propaga
        cliente = ClienteFalso()
        cliente.batches = LotesQueRechazan(cliente, [error_cliente(400, "thinking not supported"),
                                                    error_cliente(400, "mediaResolution not supported"),
                                                    error_cliente(400, "schema not supported")])
        with pytest.raises(errors.ClientError):
            gemini.enviar_lote(cliente, peticiones, MODELO, "lote", log=lambda _: None)
        assert len([l for l in cliente.llamadas if l["tipo"] == "batch_create"]) == 3
        # otros errores se propagan sin reintentar
        cliente = ClienteFalso()
        cliente.batches = LotesQueRechazan(cliente, [error_cliente(400, "API key not valid")])
        with pytest.raises(errors.ClientError):
            gemini.enviar_lote(cliente, peticiones, MODELO, "lote", log=lambda _: None)
        assert len([l for l in cliente.llamadas if l["tipo"] == "batch_create"]) == 1

    def test_opcion_rechazada_en_lote(self):
        info = info_video()
        rechazo = gemini.ErrorItemLote("v", 0, 3, "Invalid value at 'generation_config.thinking_config.thinking_level'")
        assert isinstance(rechazo, RuntimeError) and "thinking_level" in str(rechazo)
        assert gemini.opcion_rechazada_en_lote({"v": rechazo, "w": rechazo}, gemini.OpcionesLote(), MODELO) == "sin_thinking"
        # ya sin thinking → el siguiente peldaño que mencione el mensaje
        assert gemini.opcion_rechazada_en_lote({"v": rechazo}, gemini.OpcionesLote(thinking=False), MODELO) is None
        esquema = gemini.ErrorItemLote("v", 0, "INVALID_ARGUMENT", "response_json_schema is not supported")
        assert gemini.opcion_rechazada_en_lote({"v": esquema}, gemini.OpcionesLote(thinking=False), MODELO) == "sin_esquema"
        # un video que sí respondió, un error de otro tipo o un lote vacío → no es un rechazo de configuración
        ok = ResultadoAnalisis(momentos=[], modo="gemini-batch")
        assert gemini.opcion_rechazada_en_lote({"v": rechazo, "w": ok}, gemini.OpcionesLote(), MODELO) is None
        assert gemini.opcion_rechazada_en_lote({"v": gemini.ErrorItemLote("v", 0, 13, "internal error")}, gemini.OpcionesLote(), MODELO) is None
        assert gemini.opcion_rechazada_en_lote({"v": RuntimeError("thinking")}, gemini.OpcionesLote(), MODELO) is None
        assert gemini.opcion_rechazada_en_lote({}, gemini.OpcionesLote(), MODELO) is None
        respuestas = [types.InlinedResponse(error=types.JobError(code=3, message="thinking_level not supported"),
                                            metadata={"video": "v", "tramo": "0"})]
        job = types.BatchJob(state=types.JobState.JOB_STATE_FAILED, dest=types.BatchJobDestination(inlined_responses=respuestas))
        recogido = gemini.recoger_lote(ClienteFalso(), job, {"v": {"info": info}}, log=lambda _: None)
        assert isinstance(recogido["v"], gemini.ErrorItemLote) and recogido["v"].codigo == 3
        assert gemini.opcion_rechazada_en_lote(recogido, gemini.OpcionesLote(), MODELO) == "sin_thinking"

    def test_enviar_lote_con_clave_propia(self):
        """P-H9: dos videos con el mismo nombre base van con claves distintas en los metadatos."""
        cliente = ClienteFalso()
        a, b = info_video(90, "IMG_0001"), info_video(90, "IMG_0001")
        peticiones = [{"archivo": archivo_remoto("files/a"), "info": a, "tramo": None, "clave": "IMG_0001"},
                      {"archivo": archivo_remoto("files/b"), "info": b, "tramo": None, "clave": "IMG_0001-abc123"}]
        gemini.enviar_lote(cliente, peticiones, MODELO, "lote", log=lambda _: None)
        src = cliente.llamadas[-1]["src"]
        assert [s.metadata["video"] for s in src] == ["IMG_0001", "IMG_0001-abc123"]
        assert [s.metadata["tramo"] for s in src] == ["0", "0"]

    def test_recoger_lote_respuesta_sin_json(self):
        respuestas = [types.InlinedResponse(response=respuesta("nada"), metadata={"video": "v", "tramo": "0"})]
        job = types.BatchJob(state=types.JobState.JOB_STATE_SUCCEEDED, dest=types.BatchJobDestination(inlined_responses=respuestas))
        resultados = gemini.recoger_lote(ClienteFalso(), job, {"v": {"info": info_video()}}, log=lambda _: None)
        assert isinstance(resultados["v"], Exception)


# ----------------------------------------------------------------------------
# Redactor
# ----------------------------------------------------------------------------

def resultado_base() -> ResultadoAnalisis:
    momentos = [Momento(10.0, "encender", "se enciende", 5, "ambos", "Inicio", {"x": 0.5, "y": 0.5}, ruta_captura="/c/1.jpg"),
                Momento(30.0, "colimador", "se ajusta a 10 cm", 4, "audio", "Inicio")]
    resultado = ResultadoAnalisis(momentos=momentos, modo="gemini", modelo=MODELO, resumen="borrador",
                                  uso=Uso(modelo=MODELO, tokens_entrada=1000, tokens_salida=100, tokens_total=1100,
                                          llamadas=1, costo_usd=0.001), avisos=["previo"])
    resultado.titulo = "Borrador"
    return resultado


class TestPulirRedaccion:
    def test_exito(self):
        pulido = json.dumps({"titulo_video": "Manual del arco en C", "resumen": "Resumen pulido.", "momentos": [
            {"tiempo": "00:10", "titulo": "Encender el equipo", "descripcion": "Presionar el botón de encendido.",
             "importancia": 1, "fuente": "visual", "seccion": "Encendido"},
            {"tiempo": "00:30", "titulo": "Ajustar el colimador a 10 cm", "descripcion": "Ajustar el colimador a 10 cm.",
             "importancia": 4, "fuente": "audio", "seccion": "Ajustes"}]})
        cliente = ClienteFalso([respuesta(pulido, entrada=500, salida=100, pensamiento=0)])
        original = resultado_base()
        nuevo = gemini.pulir_redaccion(cliente, original, "gemini-pro", log=lambda _: None)

        assert [m.titulo for m in nuevo.momentos] == ["Encender el equipo", "Ajustar el colimador a 10 cm"]
        assert [m.seccion for m in nuevo.momentos] == ["Encendido", "Ajustes"]
        assert [m.tiempo_seg for m in nuevo.momentos] == [10.0, 30.0]
        assert nuevo.momentos[0].importancia == 5 and nuevo.momentos[0].fuente == "ambos"      # no los cambia
        assert nuevo.momentos[0].zona == {"x": 0.5, "y": 0.5} and nuevo.momentos[0].ruta_captura == "/c/1.jpg"
        assert nuevo.resumen == "Resumen pulido." and nuevo.titulo == "Manual del arco en C"
        assert nuevo.modelo == f"{MODELO}+gemini-pro" and nuevo.uso.modelo == f"{MODELO}+gemini-pro"
        assert nuevo.uso.tokens_entrada == 1500 and nuevo.uso.llamadas == 2
        assert nuevo.uso.costo_usd == pytest.approx(0.001)      # gemini-pro sin precio conocido: no suma
        assert nuevo.avisos[0] == "previo" and any("pulida" in a for a in nuevo.avisos)
        # el original no se modifica
        assert original.momentos[0].titulo == "encender" and original.modelo == MODELO

        llamada = cliente.generaciones()[0]
        assert llamada["modelo"] == "gemini-pro"
        assert llamada["config"].system_instruction == gemini.PROMPT_REDACTOR
        assert llamada["config"].media_resolution is None
        assert llamada["config"].response_json_schema == gemini.ESQUEMA_RESPUESTA
        partes = llamada["contents"][0].parts
        assert len(partes) == 1 and partes[0].file_data is None and "00:30" in partes[0].text

    def test_fallo_devuelve_original_con_aviso(self):
        cliente = ClienteFalso([error_cliente(404, "not found")])
        original = resultado_base()
        nuevo = gemini.pulir_redaccion(cliente, original, "gemini-pro", log=lambda _: None)
        assert [m.titulo for m in nuevo.momentos] == ["encender", "colimador"]
        assert nuevo.modelo == MODELO and nuevo.uso.tokens_entrada == 1000
        assert any("No se pudo pulir" in a for a in nuevo.avisos)

    def test_estructura_alterada_devuelve_original(self):
        # quita un paso
        cliente = ClienteFalso([respuesta(json.dumps({"titulo_video": "T", "resumen": "R", "momentos": [
            {"tiempo": "00:10", "titulo": "X", "descripcion": "Y", "importancia": 5, "fuente": "ambos", "seccion": "S"}]}),
            entrada=7000, salida=100, pensamiento=0)])
        nuevo = gemini.pulir_redaccion(cliente, resultado_base(), "gemini-pro", log=lambda _: None)
        assert [m.titulo for m in nuevo.momentos] == ["encender", "colimador"] and any("No se pudo pulir" in a for a in nuevo.avisos)
        # G8: la llamada pagada se contabiliza aunque no se aplique
        assert nuevo.uso.llamadas == 2 and nuevo.uso.tokens_entrada == 8000 and nuevo.uso.modelo.endswith("(fallido)")
        assert nuevo.modelo == MODELO and any("7100 tokens" in a for a in nuevo.avisos)
        # cambia un tiempo
        cliente = ClienteFalso([respuesta(json.dumps({"titulo_video": "T", "resumen": "R", "momentos": [
            {"tiempo": "00:10", "titulo": "X", "descripcion": "Y", "importancia": 5, "fuente": "ambos", "seccion": "S"},
            {"tiempo": "00:45", "titulo": "X", "descripcion": "Y", "importancia": 5, "fuente": "ambos", "seccion": "S"}]}))])
        nuevo = gemini.pulir_redaccion(cliente, resultado_base(), "gemini-pro", log=lambda _: None)
        assert [m.titulo for m in nuevo.momentos] == ["encender", "colimador"]

    def test_sin_momentos(self):
        vacio = ResultadoAnalisis(momentos=[], modo="gemini", modelo=MODELO)
        nuevo = gemini.pulir_redaccion(ClienteFalso(), vacio, "gemini-pro", log=lambda _: None)
        assert nuevo.momentos == [] and any("omitido" in a for a in nuevo.avisos)

    def test_sin_uso_previo(self):
        pulido = json.dumps({"titulo_video": "T", "resumen": "R", "momentos": [
            {"tiempo": "00:10", "titulo": "X", "descripcion": "Y", "importancia": 5, "fuente": "ambos", "seccion": "S"}]})
        local = ResultadoAnalisis(momentos=[Momento(10.0, "a", "b")], modo="local", modelo="scdet", uso=None)
        nuevo = gemini.pulir_redaccion(ClienteFalso([respuesta(pulido)]), local, MODELO, log=lambda _: None)
        assert nuevo.uso.llamadas == 1 and nuevo.uso.modelo == f"scdet+{MODELO}" and nuevo.uso.costo_usd > 0

    def test_tras_el_refinado_el_uso_conserva_la_historia(self):
        """G5: el redactor va después del refinado; el Uso queda "a (+refinado)+b" y el documento "a+b"."""
        pulido = json.dumps({"titulo_video": "T", "resumen": "R", "momentos": [
            {"tiempo": "00:10", "titulo": "X", "descripcion": "Y", "importancia": 5, "fuente": "ambos", "seccion": "S"},
            {"tiempo": "00:30", "titulo": "Z", "descripcion": "W", "importancia": 4, "fuente": "audio", "seccion": "S"}]})
        refinado = resultado_base()
        refinado.uso = Uso(modelo=f"{MODELO} (+refinado)", tokens_entrada=1800, llamadas=2, costo_usd=0.002)
        nuevo = gemini.pulir_redaccion(ClienteFalso([respuesta(pulido, entrada=500, salida=50, pensamiento=0)]), refinado,
                                       "gemini-pro", log=lambda _: None)
        assert nuevo.modelo == f"{MODELO}+gemini-pro" and nuevo.uso.modelo == f"{MODELO} (+refinado)+gemini-pro"
        assert nuevo.uso.llamadas == 3 and nuevo.uso.tokens_entrada == 2300
        assert nuevo.momentos[0].zona == {"x": 0.5, "y": 0.5}      # el redactor no toca las zonas


# ----------------------------------------------------------------------------
# Refinado con capturas
# ----------------------------------------------------------------------------

def captura_jpeg(ruta: Path, ancho: int, alto: int) -> Path:
    """JPEG real (Pillow) con un rótulo, como una captura sacada del video original."""
    from PIL import Image, ImageDraw
    imagen = Image.new("RGB", (ancho, alto), (40, 44, 52))
    dibujo = ImageDraw.Draw(imagen)
    dibujo.rectangle([ancho * 0.6, alto * 0.4, ancho * 0.85, alto * 0.5], fill=(230, 200, 40))
    dibujo.text((ancho * 0.62, alto * 0.42), "kV 70  mA 2.5", fill=(0, 0, 0))
    imagen.save(ruta, format="JPEG", quality=90)
    return ruta


def momento_con_captura(carpeta: Path, numero: int, tiempo: float, titulo: str, ancho: int = 1920, alto: int = 1080,
                        zona: dict | None = None, ruta: str | None = None) -> Momento:
    if ruta is None:
        ruta = str(captura_jpeg(carpeta / f"{numero:02d}.jpg", ancho, alto))
    return Momento(tiempo, titulo, f"Descripción de {titulo}.", 3, "ambos", "Ajustes", zona, ruta_captura=ruta,
                   tiempo_real_seg=tiempo + 0.4)


def resultado_con_capturas(carpeta: Path) -> ResultadoAnalisis:
    """3 momentos: el 1 (horizontal) y el 3 (vertical) con captura real; el 2 sin captura."""
    momentos = [momento_con_captura(carpeta, 1, 10.0, "Encender", zona={"x": 0.5, "y": 0.5}),
                Momento(30.0, "Colimar", "Se ajusta el colimador.", 4, "audio", "Ajustes"),
                momento_con_captura(carpeta, 3, 65.0, "Guardar", ancho=900, alto=1400)]
    return ResultadoAnalisis(momentos=momentos, modo="gemini", modelo=MODELO, resumen="resumen", titulo="Manual",
                             uso=Uso(modelo=MODELO, tokens_entrada=1000, tokens_salida=100, tokens_total=1100,
                                     llamadas=1, costo_usd=0.001), avisos=["previo"])


def refinado_json(momentos: list[dict]) -> str:
    return json.dumps({"momentos": momentos}, ensure_ascii=False)


def imagenes_de(llamada: dict) -> list[tuple[str, "types.Blob"]]:
    """[(rótulo previo, inline_data)] de las imágenes enviadas en una generación."""
    partes = llamada["contents"][0].parts
    return [(partes[i - 1].text, p.inline_data) for i, p in enumerate(partes) if p.inline_data is not None]


def tamano_imagen(datos: bytes) -> tuple[int, int]:
    from PIL import Image
    import io
    with Image.open(io.BytesIO(datos)) as imagen:
        assert imagen.format == "JPEG"
        return imagen.size


class TestRefinarConCapturas:
    def test_exito_solo_cambia_lo_devuelto(self, tmp_path):
        original = resultado_con_capturas(tmp_path)
        cliente = ClienteFalso([respuesta(refinado_json([
            {"numero": 1, "titulo": "Ajustar kV a 70 y mA a 2,5", "descripcion": "Descripción de Encender.",
             "zona": {"x": 700, "y": 450}},
            {"numero": 3, "titulo": "Guardar", "descripcion": "Guardar la imagen con el botón SAVE (icono de disquete).",
             "zona": {"x": 2000, "y": 5}},                                          # zona inválida: se conserva la original
        ]), entrada=800, salida=120, pensamiento=0)])
        lineas, log = registro()
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, equipo="arco en C", max_lado_px=640, log=log)

        # ni los tiempos ni el número de momentos cambian; el 2 (sin captura) queda intacto
        assert len(nuevo.momentos) == 3
        assert [m.tiempo_seg for m in nuevo.momentos] == [10.0, 30.0, 65.0]
        assert [m.tiempo_real_seg for m in nuevo.momentos] == [10.4, None, 65.4]
        assert nuevo.momentos[1] == original.momentos[1]
        m1, m3 = nuevo.momentos[0], nuevo.momentos[2]
        assert m1.titulo == "Ajustar kV a 70 y mA a 2,5" and m1.descripcion == "Descripción de Encender."
        assert m1.zona == {"x": 0.7, "y": 0.45}
        assert (m1.importancia, m1.fuente, m1.seccion, m1.ruta_captura) == (3, "ambos", "Ajustes", original.momentos[0].ruta_captura)
        assert m3.titulo == "Guardar" and m3.descripcion.startswith("Guardar la imagen con el botón SAVE") and m3.zona is None
        # el original no se modifica
        assert original.momentos[0].titulo == "Encender" and original.momentos[0].zona == {"x": 0.5, "y": 0.5}
        assert original.uso.llamadas == 1 and original.avisos == ["previo"]
        # uso sumado y modelo del documento sin cambios
        assert nuevo.modelo == MODELO and nuevo.uso.modelo == f"{MODELO} (+refinado)"
        assert nuevo.uso.llamadas == 2 and nuevo.uso.tokens_entrada == 1800 and nuevo.uso.tokens_salida == 220
        assert nuevo.uso.costo_usd == pytest.approx(0.001 + (800 * 0.25 + 120 * 1.50) / 1e6)
        assert nuevo.avisos[0] == "previo" and any("Refinado con capturas" in a and "2 pasos" in a for a in nuevo.avisos)
        assert any("Refinando" in l for l in lineas)

        # una sola petición: rótulo + imagen por paso, y al final la instrucción con el JSON de esos pasos
        llamadas = cliente.generaciones()
        assert len(llamadas) == 1 and llamadas[0]["modelo"] == MODELO
        cfg = llamadas[0]["config"]
        assert cfg.response_json_schema == gemini.ESQUEMA_REFINADO and cfg.response_json_schema is not gemini.ESQUEMA_REFINADO
        assert cfg.media_resolution is None and cfg.thinking_config.thinking_level == types.ThinkingLevel.LOW
        assert "arco en C" in cfg.system_instruction and "no inventes" in cfg.system_instruction.lower()
        imagenes = imagenes_de(llamadas[0])
        assert [rotulo for rotulo, _ in imagenes] == ["Captura del paso 1 (00:10)", "Captura del paso 3 (01:05)"]
        assert all(blob.mime_type == "image/jpeg" for _, blob in imagenes)
        assert tamano_imagen(imagenes[0][1].data) == (640, 360)
        ancho, alto = tamano_imagen(imagenes[1][1].data)
        assert alto == 640 and ancho < 640
        partes = llamadas[0]["contents"][0].parts
        assert partes[0].text.startswith("Captura del paso 1") and partes[1].inline_data is not None
        texto = partes[-1].text
        assert texto.startswith(gemini.PROMPT_REFINADO_USUARIO)
        pasos = json.loads(texto[texto.index("{"):])["momentos"]
        assert [p["numero"] for p in pasos] == [1, 3]
        # G2: van también importancia y fuente, para que el modelo sepa qué viene del audio
        assert pasos[0] == {"numero": 1, "tiempo": "00:10", "titulo": "Encender", "descripcion": "Descripción de Encender.",
                            "importancia": 3, "fuente": "ambos", "seccion": "Ajustes", "zona": {"x": 500, "y": 500}}
        assert "zona" not in pasos[1] and "tiempo_seg" not in pasos[1]
        # G2: el texto previo de cada paso cambiado queda para auditar (solo el campo que cambió)
        assert m1.titulo_original == "Encender" and m1.descripcion_original is None
        assert m3.titulo_original is None and m3.descripcion_original == "Descripción de Guardar."
        assert nuevo.momentos[1].titulo_original is None
        assert "titulo_original" in m1.a_dict() and "descripcion_original" not in m1.a_dict()
        assert "titulo_original" not in nuevo.momentos[1].a_dict()

    def test_lotes_ceil_n_entre_lote(self, tmp_path):
        momentos = [momento_con_captura(tmp_path, n, 10.0 * n, f"Paso {n}", ancho=320, alto=200) for n in range(1, 6)]
        original = ResultadoAnalisis(momentos=momentos, modo="gemini", modelo=MODELO, uso=None)
        respuestas = [refinado_json([{"numero": n, "titulo": f"Título {n}", "descripcion": f"Descripción de Paso {n}."}
                                     for n in grupo]) for grupo in ([1, 2], [3, 4], [5])]
        cliente = ClienteFalso([respuesta(r) for r in respuestas])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, lote=2, log=lambda _: None)
        llamadas = cliente.generaciones()
        assert len(llamadas) == 3       # ceil(5 / 2)
        enviados = [[p["numero"] for p in json.loads(l["contents"][0].parts[-1].text.split("Pasos (JSON):\n")[1])["momentos"]]
                    for l in llamadas]
        assert enviados == [[1, 2], [3, 4], [5]]
        assert [len(imagenes_de(l)) for l in llamadas] == [2, 2, 1]
        assert [m.titulo for m in nuevo.momentos] == [f"Título {n}" for n in range(1, 6)]
        assert [m.tiempo_seg for m in nuevo.momentos] == [10.0, 20.0, 30.0, 40.0, 50.0]
        assert nuevo.uso.llamadas == 3 and nuevo.uso.modelo == f"{MODELO} (+refinado)"
        assert any("5 pasos revisados en 3 petición" in a for a in nuevo.avisos)
        # la imagen no se agranda si ya es pequeña
        assert tamano_imagen(imagenes_de(llamadas[0])[0][1].data) == (320, 200)

    def test_fusion_por_orden_si_faltan_numeros(self, tmp_path):
        original = resultado_con_capturas(tmp_path)
        cliente = ClienteFalso([respuesta(refinado_json([{"titulo": "Uno", "descripcion": "D1."},
                                                         {"titulo": "Tres", "descripcion": "D3."}]))])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        assert [m.titulo for m in nuevo.momentos] == ["Uno", "Colimar", "Tres"]
        assert nuevo.momentos[0].zona == {"x": 0.5, "y": 0.5}      # sin zona devuelta: se conserva

    def test_fuente_audio_solo_se_completa(self, tmp_path):
        """G2: lo dicho en el audio no se "corrige" con la captura; solo se admite añadir."""
        dicho = "Se indica kV 70 y mA 2,5 (valor no audible del tiempo)."
        momentos = [momento_con_captura(tmp_path, 1, 10.0, "Fijar kV en 70", ancho=320, alto=200),
                    momento_con_captura(tmp_path, 2, 20.0, "Seleccionar modo", ancho=320, alto=200)]
        for m in momentos:
            m.fuente = "audio"
            m.descripcion = dicho
        original = ResultadoAnalisis(momentos=momentos, modo="gemini", modelo=MODELO)
        cliente = ClienteFalso([respuesta(refinado_json([
            {"numero": 1, "titulo": "Fijar kV en 75", "descripcion": "En pantalla se lee kV 75."},   # sustituye: se rechaza
            {"numero": 2, "titulo": "Seleccionar modo FLUORO",                                       # completa: se admite
             "descripcion": dicho + " En pantalla se lee modo FLUORO."},
        ]))])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        assert nuevo.momentos[0].titulo == "Fijar kV en 70" and nuevo.momentos[0].descripcion == dicho
        assert nuevo.momentos[0].titulo_original is None
        assert nuevo.momentos[1].titulo == "Seleccionar modo FLUORO" and nuevo.momentos[1].titulo_original == "Seleccionar modo"
        assert nuevo.momentos[1].descripcion.endswith("modo FLUORO.") and nuevo.momentos[1].descripcion_original == dicho
        assert any("1 con cambios" in a for a in nuevo.avisos)
        # el JSON enviado lleva la fuente
        texto = cliente.generaciones()[0]["contents"][0].parts[-1].text
        assert all(p["fuente"] == "audio" for p in json.loads(texto[texto.index("{"):])["momentos"])

    def test_zona_null_quita_la_zona_y_ausente_la_conserva(self, tmp_path):
        """G3: null = el elemento no aparece en la captura (no señalar); sin campo = conservar."""
        momentos = [momento_con_captura(tmp_path, n, 10.0 * n, f"Paso {n}", ancho=320, alto=200, zona={"x": 0.5, "y": 0.5})
                    for n in (1, 2, 3)]
        original = ResultadoAnalisis(momentos=momentos, modo="gemini", modelo=MODELO)
        cliente = ClienteFalso([respuesta(refinado_json([
            {"numero": 1, "titulo": "Paso 1", "descripcion": "Descripción de Paso 1.", "zona": None},
            {"numero": 2, "titulo": "Paso 2", "descripcion": "Descripción de Paso 2."},
            {"numero": 3, "titulo": "Paso 3", "descripcion": "Descripción de Paso 3.", "zona": {"x": 100, "y": 200}},
        ]))])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        assert [m.zona for m in nuevo.momentos] == [None, {"x": 0.5, "y": 0.5}, {"x": 0.1, "y": 0.2}]
        assert all(m.titulo_original is None for m in nuevo.momentos)     # textos iguales: nada que auditar
        assert any("2 con cambios" in a for a in nuevo.avisos)

    def test_rotulo_con_el_tiempo_real_del_fotograma(self, tmp_path):
        """G3: el rótulo de cada captura lleva el instante real del fotograma, no el pedido."""
        momento = momento_con_captura(tmp_path, 1, 10.0, "Uno", ancho=320, alto=200)
        momento.tiempo_real_seg = 11.6
        sin_real = momento_con_captura(tmp_path, 2, 20.0, "Dos", ancho=320, alto=200)
        sin_real.tiempo_real_seg = None
        original = ResultadoAnalisis(momentos=[momento, sin_real], modo="gemini", modelo=MODELO)
        cliente = ClienteFalso([respuesta(refinado_json([{"numero": 1, "titulo": "Uno", "descripcion": "Descripción de Uno."}]))])
        gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        assert [r for r, _ in imagenes_de(cliente.generaciones()[0])] == ["Captura del paso 1 (00:12)", "Captura del paso 2 (00:20)"]

    def test_recorta_textos_largos_y_conserva_vacios(self, tmp_path):
        original = resultado_con_capturas(tmp_path)
        cliente = ClienteFalso([respuesta(refinado_json([{"numero": 1, "titulo": "x" * 300, "descripcion": ""},
                                                         {"numero": 3, "titulo": "", "descripcion": "y " * 400}]))])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        assert len(nuevo.momentos[0].titulo) == config.MAX_TITULO and nuevo.momentos[0].titulo.endswith("…")
        assert nuevo.momentos[0].descripcion == "Descripción de Encender."
        assert nuevo.momentos[2].titulo == "Guardar" and len(nuevo.momentos[2].descripcion) == config.MAX_DESCRIPCION

    @pytest.mark.parametrize("texto", [
        "No puedo leer las capturas.",                                                   # sin JSON
        json.dumps({"resultado": "ok"}),                                                 # sin lista de pasos
        json.dumps({"momentos": [{"numero": 9, "titulo": "a", "descripcion": "b"},
                                 {"titulo": "c", "descripcion": "d"},
                                 {"titulo": "e", "descripcion": "f"}]}),                 # números ajenos y cantidad distinta
    ])
    def test_respuesta_invalida_devuelve_original(self, tmp_path, texto):
        original = resultado_con_capturas(tmp_path)
        lineas, log = registro()
        nuevo = gemini.refinar_con_capturas(ClienteFalso([respuesta(texto, entrada=5000, salida=100, pensamiento=0)]),
                                            original, MODELO, log=log)
        assert nuevo.momentos == original.momentos and nuevo.modelo == MODELO
        assert any("No se pudo refinar" in a for a in nuevo.avisos) and nuevo.avisos[0] == "previo"
        assert any("Aviso" in l for l in lineas)
        # G8: la petición pagada se suma aunque no se aplique ningún cambio
        assert nuevo.uso.llamadas == 2 and nuevo.uso.tokens_entrada == 6000 and nuevo.uso.modelo == f"{MODELO} (+refinado fallido)"
        assert nuevo.uso.costo_usd == pytest.approx(0.001 + (5000 * 0.25 + 100 * 1.50) / 1e6)
        assert any("5100 tokens" in a and "sin aplicar cambios" in a for a in nuevo.avisos)
        assert original.uso.llamadas == 1

    def test_excepcion_del_cliente_devuelve_original(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gemini.time, "sleep", lambda _s: None)
        original = resultado_con_capturas(tmp_path)
        servidor = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        for respuestas, llamadas in (([error_cliente(400, "API key not valid. Please pass a valid API key.")], 1),
                                     ([servidor] * (gemini.REINTENTOS_RED + 1), gemini.REINTENTOS_RED + 1),
                                     ([error_cliente(404, "not found")], 1)):
            cliente = ClienteFalso(respuestas)
            nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
            assert nuevo.momentos == original.momentos and nuevo.uso == original.uso
            assert any("No se pudo refinar" in a for a in nuevo.avisos)
            assert len(cliente.generaciones()) == llamadas

    def test_respuesta_cortada_devuelve_original(self, tmp_path):
        original = resultado_con_capturas(tmp_path)
        completo = refinado_json([{"numero": 1, "titulo": "A", "descripcion": "a"}, {"numero": 3, "titulo": "B", "descripcion": "b"}])
        cortado = completo[: completo.index('"numero": 3') + 5]
        cliente = ClienteFalso([respuesta(cortado, finish=types.FinishReason.MAX_TOKENS),
                                respuesta(cortado, finish=types.FinishReason.MAX_TOKENS)])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        assert len(cliente.generaciones()) == 2
        assert nuevo.momentos == original.momentos and any("cortada" in a for a in nuevo.avisos)
        assert nuevo.uso.llamadas == 3 and nuevo.uso.modelo.endswith("(+refinado fallido)")     # G8: 2 llamadas pagadas

    def test_fallback_sin_esquema_usa_prompt_del_refinado(self, tmp_path):
        original = resultado_con_capturas(tmp_path)
        cliente = ClienteFalso([error_cliente(400, "responseJsonSchema is not supported"),
                                respuesta(refinado_json([{"numero": 1, "titulo": "Uno", "descripcion": "d"}]))])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        llamadas = cliente.generaciones()
        assert llamadas[1]["config"].response_json_schema is None
        assert llamadas[1]["contents"][0].parts[-1].text.endswith(gemini.PROMPT_REFINADO_SIN_ESQUEMA)
        assert gemini.PROMPT_SIN_ESQUEMA not in llamadas[1]["contents"][0].parts[-1].text
        assert nuevo.momentos[0].titulo == "Uno" and nuevo.momentos[2].titulo == "Guardar"

    def test_sin_capturas_no_se_envia_nada(self, tmp_path):
        sin = ResultadoAnalisis(momentos=[Momento(10.0, "a", "b"), Momento(20.0, "c", "d")], modo="gemini", modelo=MODELO)
        cliente = ClienteFalso()
        nuevo = gemini.refinar_con_capturas(cliente, sin, MODELO, log=lambda _: None)
        assert cliente.llamadas == [] and nuevo.momentos == sin.momentos and any("omitido" in a for a in nuevo.avisos)
        vacio = gemini.refinar_con_capturas(cliente, ResultadoAnalisis(momentos=[], modo="gemini"), MODELO, log=lambda _: None)
        assert vacio.momentos == [] and cliente.llamadas == []

    def test_captura_ilegible_se_omite(self, tmp_path):
        roto = tmp_path / "roto.jpg"
        roto.write_bytes(b"esto no es un jpeg")
        momentos = [momento_con_captura(tmp_path, 1, 10.0, "Uno", ancho=200, alto=100),
                    momento_con_captura(tmp_path, 2, 20.0, "Dos", ruta=str(tmp_path / "no_existe.jpg")),
                    momento_con_captura(tmp_path, 3, 30.0, "Tres", ruta=str(roto))]
        original = ResultadoAnalisis(momentos=momentos, modo="gemini", modelo=MODELO)
        cliente = ClienteFalso([respuesta(refinado_json([{"numero": 1, "titulo": "Uno bis", "descripcion": "d"}]))])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        llamadas = cliente.generaciones()
        assert len(llamadas) == 1 and [r for r, _ in imagenes_de(llamadas[0])] == ["Captura del paso 1 (00:10)"]
        assert [m.titulo for m in nuevo.momentos] == ["Uno bis", "Dos", "Tres"]
        assert sum("no se pudo leer la captura" in a for a in nuevo.avisos) == 2
        assert nuevo.uso is not None and nuevo.uso.llamadas == 1
        # si ninguna captura se puede leer no hay petición y se conserva el original con aviso
        cliente = ClienteFalso()
        nuevo = gemini.refinar_con_capturas(cliente, ResultadoAnalisis(momentos=momentos[1:], modo="gemini"), MODELO,
                                            log=lambda _: None)
        assert cliente.llamadas == [] and any("No se pudo refinar" in a for a in nuevo.avisos)

    def test_zona_caja_se_envia_en_formato_del_modelo(self, tmp_path):
        momento = momento_con_captura(tmp_path, 1, 10.0, "Uno", ancho=200, alto=100, zona={"caja": [0.2, 0.1, 0.4, 0.3]})
        original = ResultadoAnalisis(momentos=[momento], modo="gemini", modelo=MODELO)
        cliente = ClienteFalso([respuesta(refinado_json([{"numero": 1, "titulo": "Uno", "descripcion": "Descripción de Uno."}]))])
        nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
        texto = cliente.generaciones()[0]["contents"][0].parts[-1].text
        assert json.loads(texto[texto.index("{"):])["momentos"][0]["zona"] == {"caja": [100, 200, 300, 400]}
        assert nuevo.momentos[0].zona == {"caja": [0.2, 0.1, 0.4, 0.3]} and any("0 con cambios" in a for a in nuevo.avisos)


# ----------------------------------------------------------------------------
# Funciones puras
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("valor, esperado", [
    ("00:10", 10.0), ("0:10", 10.0), ("1:05", 65.0), ("01:05.5", 65.5), ("01:05,5", 65.5),
    ("00:01:05", 65.0), ("1:01:05", 3665.0), ("65", 65.0), ("65.5", 65.5), (65, 65.0), (65.5, 65.5),
    ("1m05s", 65.0), ("2 min", 120.0), ("1 min 5 seg", 65.0), ("5s", 5.0), ("1h 2m 5s", 3725.0),
    ("75:03", 4503.0), ("  01:05  ", 65.0), ("-3", 0.0), ("-00:10", 0.0), (-4, 0.0), ("a los 01:05 del video", 65.0),
    ("abc", None), ("", None), (None, None), (True, None), (float("nan"), None), ([1], None), ("::", None),
])
def test_parsear_tiempo(valor, esperado):
    assert gemini.parsear_tiempo(valor) == esperado


class TestExtraerJson:
    def test_objeto_limpio(self):
        assert gemini.extraer_json('{"a": 1}') == ({"a": 1}, False)

    def test_fences_y_prosa(self):
        texto = 'Claro:\n```json\n{"momentos": [{"tiempo": "00:10"}]}\n```\nListo.'
        assert gemini.extraer_json(texto) == ({"momentos": [{"tiempo": "00:10"}]}, False)
        assert gemini.extraer_json('```\n[1, 2]\n```') == ([1, 2], False)
        assert gemini.extraer_json('Resultado: {"a": [1]} fin') == ({"a": [1]}, False)

    def test_lista(self):
        assert gemini.extraer_json('[{"tiempo": "00:10"}, {"tiempo": "00:20"}]') == ([{"tiempo": "00:10"}, {"tiempo": "00:20"}], False)

    def test_comas_finales(self):
        assert gemini.extraer_json('{"a": [1, 2,], "b": {"c": 1,},}') == ({"a": [1, 2], "b": {"c": 1}}, False)

    def test_truncado_rescata_objetos_completos(self):
        completo = documento_json([momento_json("00:10", "A"), momento_json("00:20", "B"), momento_json("00:30", "C")])
        cortado = completo[: completo.index('"tiempo": "00:30"') + 14]
        datos, truncado = gemini.extraer_json(cortado)
        assert truncado is True
        assert [m["titulo"] for m in datos["momentos"]] == ["A", "B"]
        assert datos["titulo_video"] == "Manual del arco en C"

    def test_truncado_dentro_de_zona(self):
        texto = '{"momentos": [{"tiempo": "00:10", "zona": {"x": 1, "y": 2}}, {"tiempo": "00:20", "zona": {"x": 5'
        datos, truncado = gemini.extraer_json(texto)
        assert truncado and datos == {"momentos": [{"tiempo": "00:10", "zona": {"x": 1, "y": 2}}]}

    def test_truncado_con_fence_abierto_y_llaves_en_cadenas(self):
        texto = '```json\n{"momentos": [{"tiempo": "00:10", "titulo": "Pulsar {ok} y \\"cerrar\\""}, {"tiempo": "00:2'
        datos, truncado = gemini.extraer_json(texto)
        assert truncado and datos["momentos"][0]["titulo"] == 'Pulsar {ok} y "cerrar"'

    def test_lista_truncada(self):
        datos, truncado = gemini.extraer_json('[{"a": 1}, {"b": 2}, {"c"')
        assert truncado and datos == [{"a": 1}, {"b": 2}]

    def test_truncado_sin_nada_completo(self):
        assert gemini.extraer_json('{"titulo_video": "Manual del') == (None, True)

    def test_sin_json(self):
        assert gemini.extraer_json("No hay nada aquí.") == (None, False)
        assert gemini.extraer_json("") == (None, False)
        assert gemini.extraer_json("42") == (None, False)
        assert gemini.extraer_json(None) == (None, False)


class TestNormalizarMomentos:
    def test_duplicados_conserva_mayor_importancia(self):
        brutos = [momento_json("00:10", "Encender el equipo", 2), momento_json("00:11", "Encender el equipo.", 4),
                  momento_json("00:20", "Ajustar el colimador", 3), momento_json("00:21.5", "ajustar el  colimador", 1)]
        momentos, avisos = gemini.normalizar_momentos(brutos, 90.0)
        assert [m.titulo for m in momentos] == ["Encender el equipo.", "Ajustar el colimador"]
        assert [m.tiempo_seg for m in momentos] == [11.0, 20.0]
        assert len(avisos) == 2 and all("fusionado" in a for a in avisos)
        assert "'Encender el equipo' (00:10)" in avisos[0] and "'Encender el equipo.' (00:11)" in avisos[0]

    def test_cercanos_con_titulos_distintos_se_conservan(self):
        """G4: dos momentos a 1 s con contenido distinto no son duplicados (valor dicho en la misma frase)."""
        brutos = [momento_json("00:10", "Seleccionar modo FLUORO", 4),
                  momento_json("00:11", "Fijar kV en 70", 4, descripcion="Se indica kV 70.", fuente="audio"),
                  momento_json("00:11.5", "Fijar kV en 70", 3)]
        momentos, avisos = gemini.normalizar_momentos(brutos, 90.0)
        assert [m.titulo for m in momentos] == ["Seleccionar modo FLUORO", "Fijar kV en 70"]
        assert momentos[1].importancia == 4 and momentos[1].descripcion == "Se indica kV 70."
        assert len(avisos) == 1 and "'Fijar kV en 70' (00:12)" in avisos[0]

    def test_titulos_casi_iguales(self):
        assert gemini.titulos_casi_iguales("Encender el equipo", "encender el equipo.")
        assert gemini.titulos_casi_iguales("Ajustar el colimador a 10 cm", "Ajustar el colimador a 10 cm…")
        assert not gemini.titulos_casi_iguales("Seleccionar modo FLUORO", "Fijar kV en 70")
        assert not gemini.titulos_casi_iguales("Abrir el colimador", "Cerrar el colimador a 10 cm")
        assert gemini.titulos_casi_iguales("", "") and not gemini.titulos_casi_iguales("", "algo")

    def test_filtrar_momentos(self):
        momentos = [Momento(5.0, "A", "", 2, puntaje=1.0), Momento(15.0, "B", "", 5), Momento(25.0, "C", "", 3, puntaje=2.0),
                    Momento(35.0, "D", "", 5), Momento(45.0, "E", "", 3, puntaje=3.0)]
        assert gemini.filtrar_momentos(momentos) == (momentos, [])
        filtrados, avisos = gemini.filtrar_momentos(momentos, max_momentos=3)
        assert [m.titulo for m in filtrados] == ["B", "D", "E"]        # empate 3-3: mayor puntaje local
        assert len(avisos) == 1 and "--max-momentos" in avisos[0]
        filtrados, avisos = gemini.filtrar_momentos(momentos, importancia_minima=3, max_momentos=0)
        assert [m.titulo for m in filtrados] == ["B", "C", "D", "E"] and "importancia < 3" in avisos[0]
        assert gemini.filtrar_momentos([], max_momentos=2, importancia_minima=4) == ([], [])

    def test_acotado_y_orden(self):
        brutos = [momento_json("05:00", "tarde"), momento_json("-3", "negativo"), momento_json("00:30", "medio")]
        momentos, _ = gemini.normalizar_momentos(brutos, 90.0)
        assert [m.tiempo_seg for m in momentos] == [0.0, 30.0, 89.5]
        assert [m.titulo for m in momentos] == ["negativo", "medio", "tarde"]

    def test_desplazamiento(self):
        momentos, _ = gemini.normalizar_momentos([momento_json("00:10")], 6000.0, desplazamiento=2700.0)
        assert momentos[0].tiempo_seg == 2710.0

    def test_sin_tiempo_se_descarta(self):
        brutos = [{"titulo": "sin tiempo"}, momento_json("abc", "malo"), "texto suelto", momento_json("00:05", "bueno")]
        momentos, avisos = gemini.normalizar_momentos(brutos, 90.0)
        assert [m.titulo for m in momentos] == ["bueno"]
        assert any("sin tiempo" in a for a in avisos) and any("no eran objetos" in a for a in avisos)

    def test_recorte_y_valores_por_defecto(self):
        bruto = {"tiempo": 5, "titulo": "x" * 200, "descripcion": "y " * 300, "importancia": "9", "fuente": "video",
                 "seccion": "  ", "zona": "nada"}
        momentos, _ = gemini.normalizar_momentos([bruto], 90.0)
        m = momentos[0]
        assert len(m.titulo) == config.MAX_TITULO and m.titulo.endswith("…")
        assert len(m.descripcion) == config.MAX_DESCRIPCION and m.descripcion.endswith("…")
        assert m.importancia == 5 and m.fuente == "ambos" and m.seccion is None and m.zona is None
        vacio, _ = gemini.normalizar_momentos([{"tiempo": "00:07"}], 90.0)
        assert vacio[0].titulo == "Momento a los 00:07" and vacio[0].importancia == 3 and vacio[0].descripcion == ""
        assert gemini.normalizar_momentos([{"tiempo": "00:07", "importancia": "alta"}], 90.0)[0][0].importancia == 3

    @pytest.mark.parametrize("zona, esperado", [
        ({"x": 500, "y": 250}, {"x": 0.5, "y": 0.25}),
        ({"x": 0.5, "y": 0.25}, {"x": 0.5, "y": 0.25}),
        ({"x": "500", "y": "250"}, {"x": 0.5, "y": 0.25}),
        ({"x": 1000, "y": 0}, {"x": 1.0, "y": 0.0}),
        ({"caja": [100, 200, 300, 400]}, {"caja": [0.2, 0.1, 0.4, 0.3]}),
        ({"caja": [0.1, 0.2, 0.3, 0.4]}, {"caja": [0.2, 0.1, 0.4, 0.3]}),
        ({"x": 1500, "y": 20}, None),
        ({"x": -1, "y": 20}, None),
        ({"x": 10}, None),
        ({"caja": [1, 2, 3]}, None),
        ({"caja": [100, 200, 100, 400]}, None),
        ({"x": "a", "y": 1}, None),
        ("500,250", None),
        (None, None),
        ({}, None),
    ])
    def test_zona(self, zona, esperado):
        momentos, _ = gemini.normalizar_momentos([momento_json("00:05", zona=zona)], 90.0)
        assert momentos[0].zona == esperado

    def test_importancia_minima(self):
        brutos = [momento_json("00:05", "A", 1), momento_json("00:15", "B", 3), momento_json("00:25", "C", 5)]
        momentos, avisos = gemini.normalizar_momentos(brutos, 90.0, importancia_minima=3)
        assert [m.titulo for m in momentos] == ["B", "C"] and any("importancia" in a for a in avisos)

    def test_max_momentos(self):
        brutos = [momento_json("00:05", "A", 2), momento_json("00:15", "B", 5), momento_json("00:25", "C", 3),
                  momento_json("00:35", "D", 5), momento_json("00:45", "E", 3)]
        momentos, avisos = gemini.normalizar_momentos(brutos, 90.0, max_momentos=3)
        assert [m.titulo for m in momentos] == ["B", "C", "D"]      # empate 3-3: el más temprano; orden cronológico
        assert any("max-momentos" in a for a in avisos)
        todos, avisos = gemini.normalizar_momentos(brutos, 90.0, max_momentos=None)
        assert len(todos) == 5 and not avisos
        assert len(gemini.normalizar_momentos(brutos, 90.0, max_momentos=0)[0]) == 5     # 0 = sin límite, como None

    def test_vacio(self):
        assert gemini.normalizar_momentos([], 90.0) == ([], [])


class TestConstruirResultado:
    def test_dict(self):
        datos = {"titulo_video": " Manual ", "resumen": "Resumen.", "momentos": [momento_json("00:05")]}
        uso = Uso(modelo=MODELO, tokens_total=10)
        r = gemini.construir_resultado(datos, info_video(), MODELO, "crudo", uso, False)
        assert r.titulo == "Manual" and r.resumen == "Resumen." and len(r.momentos) == 1
        assert r.modo == "gemini" and r.modelo == MODELO and r.texto_bruto == "crudo" and r.uso is uso
        assert r.truncado is False and r.avisos == []

    def test_lista_y_none(self):
        r = gemini.construir_resultado([momento_json("00:05"), momento_json("00:15")], info_video(), MODELO, "", None, True,
                                       modo="gemini-batch", max_momentos=1)
        assert len(r.momentos) == 1 and r.titulo is None and r.resumen is None and r.truncado and r.modo == "gemini-batch"
        assert any("cortada" in a for a in r.avisos)
        vacio = gemini.construir_resultado(None, info_video(), MODELO, "", None, False)
        assert vacio.momentos == [] and any("JSON" in a for a in vacio.avisos) and any("ningún momento" in a for a in vacio.avisos)

    def test_lista_bajo_otra_clave(self):
        r = gemini.construir_resultado({"pasos": [momento_json("00:05")]}, info_video(), MODELO, "", None, False)
        assert len(r.momentos) == 1


class TestUsoYCosto:
    def test_uso_desde_respuesta(self):
        uso = gemini.uso_desde_respuesta(respuesta("{}", entrada=300, salida=20, pensamiento=5), MODELO)
        assert (uso.tokens_entrada, uso.tokens_salida, uso.tokens_pensamiento, uso.tokens_total) == (300, 20, 5, 325)
        assert uso.detalle_entrada == {"VIDEO": 200, "AUDIO": 60, "TEXT": 40}
        assert uso.llamadas == 1 and uso.batch is False and uso.costo_usd is None
        vacio = gemini.uso_desde_respuesta(respuesta("{}", con_uso=False), MODELO, batch=True)
        assert vacio.tokens_total == 0 and vacio.batch is True and vacio.detalle_entrada == {}
        parcial = types.GenerateContentResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=7))
        assert gemini.uso_desde_respuesta(parcial, MODELO).tokens_total == 7

    def test_estimar_costo(self):
        uso = Uso(modelo=MODELO, tokens_entrada=1_000_000, tokens_salida=100_000, tokens_pensamiento=100_000)
        precio = config.PRECIOS_USD_POR_MILLON[MODELO]
        esperado = precio["entrada"] + 0.2 * precio["salida"]
        assert gemini.estimar_costo(uso) == pytest.approx(esperado)
        assert gemini.estimar_costo(uso, batch=True) == pytest.approx(esperado * 0.5)
        assert gemini.estimar_costo(Uso(modelo="modelo-desconocido", tokens_entrada=10)) is None
        assert gemini.estimar_costo(Uso(modelo=f"{MODELO}+otro", tokens_entrada=10)) is None
        assert gemini.estimar_costo(None) is None
        assert gemini.estimar_costo(Uso(modelo="", tokens_entrada=10)) is None
        # prefijo (versión concreta) y prefijo models/
        assert gemini.estimar_costo(Uso(modelo=f"models/{MODELO}-001", tokens_entrada=1_000_000)) == pytest.approx(precio["entrada"])
        # precios fijados por el usuario (--precio-entrada/--precio-salida) valen para cualquier modelo
        assert gemini.estimar_costo(Uso(modelo="x", tokens_entrada=1_000_000, tokens_salida=1_000_000),
                                    {"entrada": 1.0, "salida": 2.0}) == pytest.approx(3.0)
        # tabla propia por modelo
        assert gemini.estimar_costo(Uso(modelo="x", tokens_entrada=1_000_000), {"x": {"entrada": 4.0, "salida": 1.0}}) == pytest.approx(4.0)
        # el Uso marcado como batch también aplica el descuento
        assert gemini.estimar_costo(Uso(modelo=MODELO, tokens_entrada=1_000_000, batch=True)) == pytest.approx(precio["entrada"] * 0.5)


# ----------------------------------------------------------------------------
# Transcripción literal del audio
# ----------------------------------------------------------------------------
class TestTranscripcion:
    def test_prompt_y_esquema(self):
        prompt = gemini.construir_prompt_transcripcion("arco en C")
        assert "arco en C" in prompt and "LITERALMENTE" in prompt and "mm:ss" in prompt
        assert "[inaudible]" in prompt
        props = gemini.ESQUEMA_TRANSCRIPCION["properties"]["segmentos"]["items"]["properties"]
        assert set(props) == {"inicio", "fin", "texto"}

    def test_transcribir_devuelve_segmentos_en_segundos_y_suma_uso(self):
        datos = json.dumps({"segmentos": [
            {"inicio": "00:24", "fin": "00:30", "texto": "Acá tienen el freno de rotación."},
            {"inicio": "00:01", "fin": "00:06", "texto": "  Esta es la palanca   de bloqueo. "},
            {"inicio": "xx", "fin": "00:10", "texto": "sin tiempo válido"},
            {"inicio": "00:40", "fin": "00:35", "texto": "fin antes del inicio"},
        ]})
        cliente = ClienteFalso([respuesta(datos, entrada=500, salida=80)])
        segmentos, uso = gemini.transcribir_video(cliente, archivo_remoto(), info_video(90.0), MODELO,
                                                  {"entrada": 1.0, "salida": 2.0}, log=lambda _: None)
        assert [s["inicio"] for s in segmentos] == [1.0, 24.0, 40.0]
        assert segmentos[0]["texto"] == "Esta es la palanca de bloqueo."
        assert segmentos[2]["fin"] == 40.0            # fin < inicio se corrige
        assert uso.llamadas == 1 and uso.tokens_entrada == 500 and uso.costo_usd is not None
        cfg = cliente.generaciones()[0]["config"]
        assert resolucion_en_wire(cfg) == gemini.RESOLUCIONES["baja"].value   # solo importa el audio
        assert cfg.response_json_schema["properties"]["segmentos"]

    def test_transcribir_por_tramos_desplaza_los_tiempos(self):
        r1 = respuesta(json.dumps({"segmentos": [{"inicio": "00:10", "fin": "00:12", "texto": "uno"}]}))
        r2 = respuesta(json.dumps({"segmentos": [{"inicio": "00:05", "fin": "00:07", "texto": "dos"}]}))
        cliente = ClienteFalso([r1, r2])
        segmentos, uso = gemini.transcribir_video(cliente, archivo_remoto(), info_video(1300.0), MODELO,
                                                  tramo_max_seg=650.0, log=lambda _: None)
        assert [(s["inicio"], s["texto"]) for s in segmentos] == [(10.0, "uno"), (655.0, "dos")]
        assert uso.llamadas == 2

    def test_transcribir_sin_json_propaga_con_uso(self):
        cliente = ClienteFalso([respuesta("nada de JSON"), respuesta("tampoco")])
        with pytest.raises(RuntimeError) as exc:
            gemini.transcribir_video(cliente, archivo_remoto(), info_video(), MODELO, log=lambda _: None)
        assert isinstance(exc.value.uso, Uso) and exc.value.uso.llamadas >= 1

    def test_segmentos_desde_bruto_tolera_formas_raras(self):
        assert gemini._segmentos_desde_bruto(None, 10.0, 0.0) == []
        assert gemini._segmentos_desde_bruto({"segmentos": "x"}, 10.0, 0.0) == []
        assert gemini._segmentos_desde_bruto([{"inicio": 3, "texto": "a"}, "b", {"texto": ""}], 10.0, 0.0) == [
            {"inicio": 3.0, "fin": 3.0, "texto": "a"}]


class TestReintentosDeRed:
    def test_conexion_cortada_se_reintenta_y_luego_responde(self, monkeypatch):
        import httpx
        monkeypatch.setattr(gemini.time, "sleep", lambda _s: None)
        exito = respuesta(documento_json([momento_json("00:10")]))
        cliente = ClienteFalso([httpx.RemoteProtocolError("Server disconnected without sending a response."),
                                errors.ServerError(503, {"error": {"message": "overloaded"}}), exito])
        avisos: list[str] = []
        resultado = gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=avisos.append)
        assert len(resultado.momentos) == 1 and len(cliente.generaciones()) == 3
        assert sum("se reintenta en" in a for a in resultado.avisos) == 2

    def test_conexion_cortada_persistente_propaga(self, monkeypatch):
        import httpx
        monkeypatch.setattr(gemini.time, "sleep", lambda _s: None)
        cliente = ClienteFalso([httpx.ReadTimeout("timed out")] * (gemini.REINTENTOS_RED + 1))
        with pytest.raises(httpx.ReadTimeout):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == gemini.REINTENTOS_RED + 1



def test_reparar_texto_vocales_mal_codificadas_y_controles():
    assert gemini.reparar_texto("teclado f\x00\x13sico y bot\x02n") == "teclado físico y botón"
    assert gemini.reparar_texto("sin\x07 control\x1f") == "sin control"
    assert gemini._recortar("f\x00\x13sico  x", 50) == "físico x"
    datos = json.dumps({"segmentos": [{"inicio": "00:01", "fin": "00:02", "texto": "bot\x02n f\x00\x13sico"}]})
    cliente = ClienteFalso([respuesta(datos)])
    segmentos, _uso = gemini.transcribir_video(cliente, archivo_remoto(), info_video(), MODELO, log=lambda _: None)
    assert segmentos[0]["texto"] == "botón físico"


def test_refinado_devuelve_rotacion(tmp_path):
    original = resultado_con_capturas(tmp_path)
    cliente = ClienteFalso([respuesta(refinado_json([
        {"numero": 1, "titulo": "Uno", "descripcion": "d1", "arriba_pantalla": "derecha"},
        {"numero": 3, "titulo": "Tres", "descripcion": "d3", "arriba_pantalla": "diagonal"}]))])   # el 2 no tiene captura
    nuevo = gemini.refinar_con_capturas(cliente, original, MODELO, log=lambda _: None)
    assert [m.rotacion for m in nuevo.momentos] == [270, 0, 0]     # parte superior a la derecha: 270° horario
    assert gemini.ROTACION_POR_LADO == {"arriba": 0, "derecha": 270, "abajo": 180, "izquierda": 90}
    assert "arriba_pantalla" in gemini.ESQUEMA_REFINADO["properties"]["momentos"]["items"]["properties"]
    assert "arriba_pantalla" in gemini.PROMPT_REFINADO and "arriba_pantalla" in gemini.PROMPT_REFINADO_USUARIO
