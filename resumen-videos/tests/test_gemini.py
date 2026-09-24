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
        assert cfg.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_LOW
        assert cfg.thinking_config.thinking_level == types.ThinkingLevel.LOW
        assert cfg.temperature == config.TEMPERATURA and cfg.max_output_tokens == 100


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

    def test_subida_timeout(self, tmp_path):
        ruta = tmp_path / "video.mp4"
        ruta.write_bytes(b"0" * 100)
        cliente = ClienteFalso(estados_archivo=[types.FileState.PROCESSING])
        with pytest.raises(RuntimeError, match="PROCESSING"):
            gemini.subir_video(cliente, ruta, "video", timeout_procesado=0, intervalo=0, log=lambda _: None)

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
        assert cfg.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_LOW
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

    def test_otros_errores_se_propagan(self):
        cliente = ClienteFalso([error_cliente(400, "API key not valid. Please pass a valid API key.")])
        with pytest.raises(errors.ClientError):
            gemini.analizar_video(cliente, archivo_remoto(), info_video(), log=lambda _: None)
        assert len(cliente.generaciones()) == 1
        servidor = errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        with pytest.raises(errors.ServerError):
            gemini.analizar_video(ClienteFalso([servidor]), archivo_remoto(), info_video(), log=lambda _: None)


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
            {"tiempo": "00:10", "titulo": "X", "descripcion": "Y", "importancia": 5, "fuente": "ambos", "seccion": "S"}]}))])
        nuevo = gemini.pulir_redaccion(cliente, resultado_base(), "gemini-pro", log=lambda _: None)
        assert [m.titulo for m in nuevo.momentos] == ["encender", "colimador"] and any("No se pudo pulir" in a for a in nuevo.avisos)
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
        brutos = [momento_json("00:10", "A", 2), momento_json("00:11", "B", 4), momento_json("00:20", "C", 3),
                  momento_json("00:21.5", "D", 1)]
        momentos, avisos = gemini.normalizar_momentos(brutos, 90.0)
        assert [m.titulo for m in momentos] == ["B", "C"]
        assert any("fusionaron" in a for a in avisos)

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
