"""Análisis de videos con la API de Gemini.

Cubre la subida a la Files API, el prompt y el esquema JSON de respuesta, el
análisis síncrono (por tramos en videos largos y con una escalera de fallbacks
para modelos que no admiten alguna opción), el redactor opcional, el modo
batch, el parseo robusto del JSON devuelto, la normalización de momentos y la
estimación de costo.  Las estructuras compartidas son solo las de ``modelos``.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from google import genai
from google.genai import errors, types

from . import config
from .modelos import InfoVideo, Momento, ResultadoAnalisis, Uso, formatear_tiempo

# ----------------------------------------------------------------------------
# 4.1 Prompt y esquema
# ----------------------------------------------------------------------------

#: Instrucción de sistema.  ``{equipo}`` se sustituye con ``construir_prompt_sistema``.
PROMPT_SISTEMA = """\
Eres un instructor clínico experto en la operación de {equipo}. Vas a ver un video completo, con imagen y audio, en el que un colega explica cómo se usa el equipo. Está grabado en un hospital con un teléfono: la cámara cambia de ángulo, hace zoom y enfoca botones, pantallas, el brazo, conexiones y accesorios mientras la persona lo va explicando.

OBJETIVO
Con tu respuesta se armará un manual o protocolo sencillo con capturas del video: muchas imágenes, poco texto, pasos numerados agrupados por sección. Tu tarea es extraer TODOS los momentos con valor didáctico: cada paso del procedimiento, cada parte de la máquina que se muestra o se señala, cada explicación, advertencia, valor o parámetro que se menciona, cada error a evitar y cada comprobación de seguridad.

RIGOR
Es documentación clínica: sé fiel a lo que se dice y a lo que se ve. Usa los valores, unidades, nombres de botones, menús y ajustes exactos que se mencionen o se lean en pantalla. No inventes ni completes con conocimiento general del equipo. Si algo no se ve o no se oye con claridad, dilo en la descripción (por ejemplo "valor no audible", "botón no visible con claridad"). No des por hecho nada que el video no muestre.

CUÁNTOS MOMENTOS
La cantidad la decide el contenido del video, no una cuota: si en 10 minutos se enseñan 30 cosas, devuelve 30; si solo se dicen 3 cosas importantes, devuelve 3. No rellenes con momentos triviales (encuadres de transición, pausas, repeticiones sin información nueva) ni omitas ninguno importante. Presta tanta atención a lo que se DICE como a lo que se VE: si la persona explica algo relevante sin que cambie la imagen, es un momento igual; si muestra o señala algo sin comentarlo, también lo es. Si una misma acción se repite, conserva la ocasión en que mejor se ve o mejor se explica.

CADA MOMENTO
- tiempo: "mm:ss" contado desde el inicio del video (nunca hh:mm:ss; si el video supera la hora, los minutos pasan de 59, por ejemplo "75:03"). Elige el instante en que MEJOR SE VE lo descrito (encuadre estable, botón o pantalla legibles, sin movimiento), no necesariamente el instante en que se empieza a hablar de ello.
- titulo: máximo 8 palabras, estilo manual, en imperativo: "Ajustar el colimador a 10 cm", "Conectar el pedal en el lateral".
- descripcion: 1 o 2 frases, máximo 260 caracteres: qué se hace y por qué, con los valores exactos que se digan o se lean. Sin relleno.
- importancia: 1 (detalle menor) a 5 (crítico para la seguridad del paciente, del operador o para el resultado).
- fuente: "visual" si la información está en lo que se ve, "audio" si está en lo que se dice, "ambos" si está en las dos.
- seccion: nombre corto de la fase del procedimiento a la que pertenece el paso (por ejemplo "Encendido", "Posicionamiento", "Adquisición de imagen", "Apagado y limpieza"). Usa exactamente el mismo nombre para los momentos consecutivos de la misma fase; un video suele tener pocas secciones (habitualmente entre tres y ocho).
- zona: opcional. Si la persona señala un punto concreto de la imagen (un botón, un conector, un dato en pantalla) o hay un elemento claramente protagonista, indica su posición en el fotograma del tiempo elegido como {"x": 0-1000, "y": 0-1000}, con x de izquierda a derecha e y de arriba abajo. Omite el campo si no aplica o no estás seguro.

NIVEL DOCUMENTO
- titulo_video: título corto del manual (por ejemplo "Operación básica del arco en C").
- resumen: 2 o 3 frases que digan qué enseña el video y en qué contexto.
- momentos: la lista completa, en orden cronológico.

ESTILO
Español neutro, claro y directo, sin emojis ni símbolos especiales, sin opiniones ni comentarios sobre el video. Responde únicamente con el JSON pedido.
"""

#: Texto que acompaña al video en cada petición.
PROMPT_USUARIO = (
    "Analiza este video completo, imagen y audio, y devuelve el JSON con titulo_video, resumen y la lista "
    "de momentos siguiendo las instrucciones. Recuerda: cada paso, cada parte señalada y cada valor o "
    "advertencia que se mencione es un momento; los tiempos van en formato mm:ss."
)

#: Aviso que se añade al prompt cuando se analiza solo un tramo del video.
PROMPT_TRAMO = (
    "Este fragmento corresponde al tramo {inicio}–{fin} del video completo; los tiempos van relativos "
    "al inicio del fragmento (el primer segundo del fragmento es 00:00)."
)

#: Sufijo cuando el modelo no admite esquema de respuesta.
PROMPT_SIN_ESQUEMA = (
    "Responde SOLO con el JSON: un único objeto con las claves titulo_video, resumen y momentos "
    '(lista de objetos con tiempo, titulo, descripcion, importancia, fuente, seccion y zona opcional {"x", "y"}), '
    "sin texto adicional ni marcas de código."
)

#: Instrucción de sistema del redactor opcional (segunda pasada solo de texto).
PROMPT_REDACTOR = """\
Eres el redactor de un manual clínico sobre la operación de un equipo médico. Recibirás en JSON el borrador de un protocolo extraído de un video: título, resumen y lista de pasos con tiempo, título, descripción, importancia, fuente y sección.
Mejora el título y la descripción de cada paso para que sean claros, precisos y homogéneos (imperativo, estilo protocolo), corrige la agrupación en secciones si es incoherente y redacta el resumen (2 o 3 frases) y el título del manual.
NO cambies los tiempos, NO añadas ni quites pasos, NO cambies su orden, NO inventes datos que no estén en el borrador (valores, unidades, nombres de botones, ajustes). Si el borrador dice que algo no se oyó o no se vio con claridad, consérvalo. Mantén importancia y fuente tal cual.
Devuelve el mismo JSON, con la misma estructura y el mismo número de pasos. Español neutro, sin emojis ni símbolos especiales.
"""

PROMPT_REDACTOR_USUARIO = "Borrador del protocolo (JSON):\n"

#: JSON Schema plano para ``response_json_schema`` (copiar con ``copy.deepcopy`` en cada petición).
ESQUEMA_RESPUESTA: dict = {
    "type": "object",
    "properties": {
        "titulo_video": {"type": "string", "description": "Título corto del manual"},
        "resumen": {"type": "string", "description": "Dos o tres frases sobre qué enseña el video"},
        "momentos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tiempo": {"type": "string", "description": "mm:ss desde el inicio del video"},
                    "titulo": {"type": "string"},
                    "descripcion": {"type": "string"},
                    "importancia": {"type": "integer", "minimum": 1, "maximum": 5},
                    "fuente": {"type": "string", "enum": ["visual", "audio", "ambos"]},
                    "seccion": {"type": "string"},
                    "zona": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer", "minimum": 0, "maximum": 1000},
                            "y": {"type": "integer", "minimum": 0, "maximum": 1000},
                        },
                        "required": ["x", "y"],
                    },
                },
                "required": ["tiempo", "titulo", "descripcion", "importancia", "fuente", "seccion"],
            },
        },
    },
    "required": ["titulo_video", "resumen", "momentos"],
}

FUENTES_VALIDAS = ("visual", "audio", "ambos")
ESTADOS_LOTE_TERMINADO = (
    types.JobState.JOB_STATE_SUCCEEDED,
    types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
    types.JobState.JOB_STATE_FAILED,
    types.JobState.JOB_STATE_CANCELLED,
    types.JobState.JOB_STATE_EXPIRED,
)
MARGEN_TIEMPOS_ABSOLUTOS_SEG = 5.0     # heurística de tramos (§4.4)
MINIMO_ULTIMO_TRAMO_SEG = 300.0        # un último tramo más corto se fusiona con el anterior
TOLERANCIA_TIEMPO_REDACTOR_SEG = 1.5   # el redactor no puede mover los tiempos más que esto


def construir_prompt_sistema(equipo: str = config.EQUIPO_POR_DEFECTO) -> str:
    """Instrucción de sistema con el equipo insertado (sin ``str.format`` por las llaves del JSON)."""
    texto = (equipo or "").strip() or config.EQUIPO_POR_DEFECTO
    return PROMPT_SISTEMA.replace("{equipo}", texto)


def construir_prompt_usuario(tramo: tuple[float, float] | None = None) -> str:
    """Texto del usuario; con el aviso de tramo cuando se analiza un fragmento."""
    if tramo is None:
        return PROMPT_USUARIO
    inicio, fin = tramo
    return PROMPT_USUARIO + "\n\n" + PROMPT_TRAMO.format(inicio=formatear_tiempo(inicio), fin=formatear_tiempo(fin))


# ----------------------------------------------------------------------------
# 4.2 Cliente y subida
# ----------------------------------------------------------------------------

def crear_cliente(api_key: str, timeout_ms: int = config.TIMEOUT_HTTP_MS) -> "genai.Client":
    """Cliente de Gemini con timeout y reintentos HTTP (sin ``retry_options`` el SDK no reintenta)."""
    if not api_key:
        raise ValueError("Falta la clave de API de Gemini (GEMINI_API_KEY en .env).")
    opciones = types.HttpOptions(timeout=int(timeout_ms), retry_options=types.HttpRetryOptions())
    return genai.Client(api_key=api_key, http_options=opciones)


def obtener_api_key() -> str | None:
    """``GEMINI_API_KEY`` y, si no, ``GOOGLE_API_KEY``; una cadena vacía cuenta como ausente."""
    for variable in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        valor = os.environ.get(variable, "").strip()
        if valor:
            return valor
    return None


def subir_video(cliente, ruta: Path, nombre: str, timeout_procesado: int = config.TIMEOUT_PROCESADO_SEG,
                intervalo: int = config.INTERVALO_SONDEO_ARCHIVO_SEG, *,
                log: Callable[[str], None] = print) -> "types.File":
    """Sube el archivo a la Files API y espera a que Gemini termine de procesarlo."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el archivo a subir: {ruta}")
    mime = config.MIME_SUBIDA.get(ruta.suffix.lower())
    if not mime:
        raise ValueError(
            f"Extensión {ruta.suffix!r} sin tipo MIME conocido para la subida "
            f"(admitidas: {', '.join(sorted(config.MIME_SUBIDA))}); transcodifica a .mp4 antes de subir."
        )
    tamano_mb = ruta.stat().st_size / 1_000_000
    log(f"Subiendo {ruta.name} ({tamano_mb:.1f} MB, {mime}) a la Files API…")
    archivo = cliente.files.upload(file=str(ruta), config=types.UploadFileConfig(mime_type=mime, display_name=nombre))
    log(f"Subido como {archivo.name}; esperando a que Gemini lo procese…")

    inicio = time.monotonic()
    sondeos = 0
    while archivo.state == types.FileState.PROCESSING:
        transcurrido = time.monotonic() - inicio
        if transcurrido >= timeout_procesado:
            raise RuntimeError(
                f"El archivo {archivo.name} sigue en PROCESSING tras {int(transcurrido)} s "
                f"(límite {timeout_procesado} s); prueba con --timeout-procesado mayor o una copia más ligera."
            )
        if intervalo > 0:
            time.sleep(intervalo)
        archivo = cliente.files.get(name=archivo.name)
        sondeos += 1
        if sondeos % 6 == 0:
            log(f"  …procesando ({int(time.monotonic() - inicio)} s)")
    if archivo.state == types.FileState.FAILED:
        motivo = archivo.error.message if archivo.error is not None and archivo.error.message else "motivo desconocido"
        raise RuntimeError(f"Gemini no pudo procesar el archivo subido {archivo.name}: {motivo}")
    if archivo.state != types.FileState.ACTIVE:
        log(f"Aviso: el archivo {archivo.name} quedó en estado {archivo.state}, se intenta usar igual.")
    else:
        log(f"Archivo {archivo.name} listo (ACTIVE).")
    return archivo


def eliminar_archivo(cliente, archivo, *, log: Callable[[str], None] = print) -> None:
    """Borra un archivo remoto; nunca lanza (solo informa por ``log``)."""
    nombre = archivo if isinstance(archivo, str) else getattr(archivo, "name", None)
    if not nombre:
        return
    try:
        cliente.files.delete(name=nombre)
        log(f"Archivo remoto {nombre} eliminado.")
    except Exception as exc:  # noqa: BLE001 - por contrato no debe lanzar
        log(f"Aviso: no se pudo eliminar el archivo remoto {nombre}: {exc}")


# ----------------------------------------------------------------------------
# Construcción de peticiones y escalera de fallbacks
# ----------------------------------------------------------------------------

@dataclass
class _Variante:
    """Combinación modelo + opciones con la que se intenta una generación."""

    modelo: str
    thinking: bool = True
    resolucion: bool = True
    esquema: bool = True


def _segundos_texto(segundos: float) -> str:
    return f"{int(round(segundos))}s"


def _construir_contenido(archivo, prompt_usuario: str, fps: float | None = None,
                         tramo: tuple[float, float] | None = None) -> list:
    """``contents`` con la parte de video (y ``VideoMetadata`` solo si hay fps o tramo) y el texto."""
    metadatos = None
    if fps or tramo is not None:
        metadatos = types.VideoMetadata(
            fps=float(fps) if fps else None,
            start_offset=_segundos_texto(tramo[0]) if tramo is not None else None,
            end_offset=_segundos_texto(tramo[1]) if tramo is not None else None,
        )
    parte_video = types.Part(
        file_data=types.FileData(file_uri=archivo.uri, mime_type=archivo.mime_type or "video/mp4"),
        video_metadata=metadatos,
    )
    return [types.Content(role="user", parts=[parte_video, types.Part.from_text(text=prompt_usuario)])]


def _construir_config(prompt_sistema: str, max_tokens: int, *, esquema: bool = True, thinking: bool = True,
                      resolucion: bool = True) -> "types.GenerateContentConfig":
    """``GenerateContentConfig`` con una copia nueva del esquema."""
    return types.GenerateContentConfig(
        system_instruction=prompt_sistema,
        temperature=config.TEMPERATURA,
        max_output_tokens=int(max_tokens),
        response_mime_type="application/json",
        response_json_schema=copy.deepcopy(ESQUEMA_RESPUESTA) if esquema else None,
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW if resolucion else None,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW) if thinking else None,
    )


def _generar(cliente, variante: _Variante, construir_contents: Callable[[str], list], prompt_sistema: str,
             max_tokens: int) -> "types.GenerateContentResponse":
    """Una llamada a ``generate_content`` con la variante indicada."""
    sufijo = "" if variante.esquema else "\n\n" + PROMPT_SIN_ESQUEMA
    cfg = _construir_config(prompt_sistema, max_tokens, esquema=variante.esquema,
                            thinking=variante.thinking, resolucion=variante.resolucion)
    return cliente.models.generate_content(model=variante.modelo, contents=construir_contents(sufijo), config=cfg)


def _accion_fallback(exc: errors.ClientError, variante: _Variante, hay_otro_modelo: bool) -> str | None:
    """Qué peldaño de la escalera aplica a este error (None = propagar)."""
    mensaje = f"{exc.message or ''} {exc.details or ''}".lower()
    estado = (exc.status or "").upper()
    if exc.code == 400:
        if variante.thinking and "thinking" in mensaje:
            return "sin_thinking"
        if variante.resolucion and re.search(r"media[_ ]?resolution", mensaje):
            return "sin_resolucion"
        if variante.esquema and "schema" in mensaje:
            return "sin_esquema"
    no_encontrado = exc.code == 404 or estado == "NOT_FOUND" or "not found" in mensaje or "not supported" in mensaje
    if hay_otro_modelo and no_encontrado:
        return "siguiente_modelo"
    return None


def _generar_con_fallbacks(cliente, modelos: list[str], construir_contents: Callable[[str], list],
                           prompt_sistema: str, max_tokens: int, avisos: list[str], *,
                           variante_inicial: _Variante | None = None,
                           log: Callable[[str], None] = print) -> tuple["types.GenerateContentResponse", _Variante]:
    """Prueba en orden: tal cual → sin thinking → sin media_resolution → sin esquema → siguiente modelo."""
    if not modelos:
        raise ValueError("No hay ningún modelo con el que analizar.")
    indice = 0
    if variante_inicial is not None and variante_inicial.modelo in modelos:
        indice = modelos.index(variante_inicial.modelo)
    for posicion in range(indice, len(modelos)):
        if variante_inicial is not None and posicion == indice:
            variante = replace(variante_inicial)
        else:
            variante = _Variante(modelo=modelos[posicion])
        hay_otro = posicion + 1 < len(modelos)
        while True:
            try:
                return _generar(cliente, variante, construir_contents, prompt_sistema, max_tokens), variante
            except errors.ClientError as exc:
                accion = _accion_fallback(exc, variante, hay_otro)
                detalle = f"{exc.code} {exc.status or ''}: {exc.message or exc.details}"
                if accion == "sin_thinking":
                    variante.thinking = False
                    aviso = f"El modelo {variante.modelo} rechazó thinking_config ({detalle}); se reintenta sin él."
                elif accion == "sin_resolucion":
                    variante.resolucion = False
                    aviso = f"El modelo {variante.modelo} rechazó media_resolution ({detalle}); se reintenta sin él."
                elif accion == "sin_esquema":
                    variante.esquema = False
                    aviso = (f"El modelo {variante.modelo} rechazó el esquema JSON ({detalle}); "
                             "se reintenta pidiendo el JSON en el prompt.")
                elif accion == "siguiente_modelo":
                    aviso = (f"El modelo {variante.modelo} no está disponible ({detalle}); "
                             f"se prueba con {modelos[posicion + 1]}.")
                    avisos.append(aviso)
                    log("Aviso: " + aviso)
                    break
                else:
                    raise
                avisos.append(aviso)
                log("Aviso: " + aviso)
    raise RuntimeError("La escalera de fallbacks terminó sin respuesta.")  # inalcanzable: el último modelo propaga


# ----------------------------------------------------------------------------
# Respuesta, uso y costo
# ----------------------------------------------------------------------------

def _razon_fin(resp) -> "types.FinishReason | None":
    candidatos = getattr(resp, "candidates", None) or []
    return candidatos[0].finish_reason if candidatos else None


def _texto_respuesta(resp) -> str:
    """``resp.text`` o ``RuntimeError`` explicando por qué no hay texto."""
    texto = resp.text
    if texto is not None:
        return texto
    retro = getattr(resp, "prompt_feedback", None)
    if retro is not None and retro.block_reason is not None:
        razon = getattr(retro.block_reason, "value", retro.block_reason)
        raise RuntimeError(f"Gemini bloqueó la petición (block_reason={razon}).")
    fin = _razon_fin(resp)
    if fin is not None:
        raise RuntimeError(f"Gemini no devolvió texto (finish_reason={getattr(fin, 'value', fin)}).")
    raise RuntimeError("Gemini no devolvió ningún candidato.")


def _parsear_respuesta(resp) -> tuple[dict | list | None, bool, str]:
    """(datos, truncado, texto): ``resp.parsed`` si es dict; si no, ``extraer_json``."""
    texto = _texto_respuesta(resp)
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, dict):
        return parsed, False, texto
    datos, truncado = extraer_json(texto)
    return datos, truncado, texto


def uso_desde_respuesta(resp, modelo: str, batch: bool = False) -> Uso:
    """``Uso`` de una respuesta (``usage_metadata`` puede ser None → ceros); una llamada."""
    uso = Uso(modelo=modelo, llamadas=1, batch=batch)
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return uso
    uso.tokens_entrada = int(um.prompt_token_count or 0)
    uso.tokens_salida = int(um.candidates_token_count or 0)
    uso.tokens_pensamiento = int(um.thoughts_token_count or 0)
    uso.tokens_total = int(um.total_token_count or 0) or (uso.tokens_entrada + uso.tokens_salida + uso.tokens_pensamiento)
    for detalle in um.prompt_tokens_details or []:
        if detalle.modality is None:
            continue
        clave = getattr(detalle.modality, "value", str(detalle.modality))
        uso.detalle_entrada[clave] = uso.detalle_entrada.get(clave, 0) + int(detalle.token_count or 0)
    return uso


def _nombre_modelo(modelo: str | None) -> str:
    """Nombre del modelo sin el prefijo ``models/`` que devuelven algunos objetos del SDK."""
    modelo = modelo or ""
    return modelo[len("models/"):] if modelo.startswith("models/") else modelo


def _tarifa_modelo(modelo: str, precios: dict | None) -> tuple[float, float] | None:
    """(USD/M entrada, USD/M salida) para el modelo, o None si no se conoce."""
    tabla = precios if precios else config.PRECIOS_USD_POR_MILLON
    if isinstance(tabla.get("entrada"), (int, float)) and isinstance(tabla.get("salida"), (int, float)):
        return float(tabla["entrada"]), float(tabla["salida"])   # precios fijados por el usuario
    nombre = _nombre_modelo((modelo or "").strip())
    if not nombre or "+" in nombre:          # "a+b" (análisis + redactor) no tiene tarifa única
        return None
    claves = [k for k in tabla if isinstance(k, str) and (nombre == k or nombre.startswith(k + "-"))]
    if not claves:
        return None
    tarifa = tabla[max(claves, key=len)]
    if not isinstance(tarifa, dict):
        return None
    try:
        return float(tarifa["entrada"]), float(tarifa["salida"])
    except (KeyError, TypeError, ValueError):
        return None


def estimar_costo(uso: Uso | None, precios: dict | None = None, batch: bool = False) -> float | None:
    """Costo estimado en USD (pensamiento se cobra como salida); None si el modelo no tiene precio."""
    if uso is None:
        return None
    tarifa = _tarifa_modelo(uso.modelo, precios)
    if tarifa is None:
        return None
    entrada, salida = tarifa
    costo = (uso.tokens_entrada * entrada + (uso.tokens_salida + uso.tokens_pensamiento) * salida) / 1_000_000
    if batch or uso.batch:
        costo *= config.DESCUENTO_BATCH
    return costo


# ----------------------------------------------------------------------------
# 4.5 Normalización (pura)
# ----------------------------------------------------------------------------

_RE_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)(?:```|$)", re.S)
_RE_COMAS_FINALES = re.compile(r",(\s*[}\]])")


def _quitar_fences(texto: str) -> str:
    coincidencia = _RE_FENCE.search(texto)
    if coincidencia:
        return coincidencia.group(1).strip()
    return texto.strip()


def _cargar_json(fragmento: str) -> dict | list | None:
    """``json.loads`` tolerante con comas finales; solo devuelve objetos o listas."""
    for candidato in (fragmento, _RE_COMAS_FINALES.sub(r"\1", fragmento)):
        try:
            objeto = json.loads(candidato)
        except ValueError:
            continue
        return objeto if isinstance(objeto, (dict, list)) else None
    return None


def _inicio_json(texto: str) -> int | None:
    posiciones = [p for p in (texto.find("{"), texto.find("[")) if p >= 0]
    return min(posiciones) if posiciones else None


def _rescatar_truncado(texto: str) -> dict | list | None:
    """Corta un JSON incompleto tras el último elemento completo y cierra los contenedores abiertos."""
    inicio = _inicio_json(texto)
    if inicio is None:
        return None
    pila: list[str] = []
    en_cadena = escape = False
    corte_elemento = corte_cualquiera = None      # posición y pila tras cerrar un elemento de lista / cualquier contenedor
    for i in range(inicio, len(texto)):
        c = texto[i]
        if en_cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_cadena = False
            continue
        if c == '"':
            en_cadena = True
        elif c in "{[":
            pila.append(c)
        elif c in "}]":
            if not pila:
                break
            pila.pop()
            if not pila:
                return None          # la raíz se cerró: no está truncado, es otro problema
            corte_cualquiera = (i + 1, list(pila))
            if pila[-1] == "[":
                corte_elemento = (i + 1, list(pila))
    corte = corte_elemento or corte_cualquiera
    if corte is None:
        return None
    posicion, pila_corte = corte
    cierre = "".join("}" if c == "{" else "]" for c in reversed(pila_corte))
    return _cargar_json(texto[inicio:posicion] + cierre)


def extraer_json(texto: str) -> tuple[dict | list | None, bool]:
    """Extrae el JSON de una respuesta: quita fences y prosa, tolera comas finales y rescata arrays truncados.

    Devuelve ``(objeto, truncado)``; ``(None, False)`` si no hay JSON y ``(None, True)`` si lo había pero
    estaba cortado sin ningún elemento completo.
    """
    if not isinstance(texto, str) or not texto.strip():
        return None, False
    limpio = _quitar_fences(texto)
    objeto = _cargar_json(limpio)
    if objeto is not None:
        return objeto, False
    inicio = _inicio_json(limpio)
    if inicio is None:
        return None, False
    fin = max(limpio.rfind("}"), limpio.rfind("]"))
    if fin > inicio:
        objeto = _cargar_json(limpio[inicio:fin + 1])
        if objeto is not None:
            return objeto, False
    return _rescatar_truncado(limpio[inicio:]), True


_RE_HMS = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})(?:[.,](\d+))?")
_RE_MS = re.compile(r"(\d+):(\d{1,2})(?:[.,](\d+))?")
_RE_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")
_RE_UNIDADES = re.compile(
    r"(?:(?P<h>\d+(?:[.,]\d+)?)\s*(?:horas?|hrs?|h)\.?\s*)?"
    r"(?:(?P<m>\d+(?:[.,]\d+)?)\s*(?:minutos?|mins?|m)\.?\s*)?"
    r"(?:(?P<s>\d+(?:[.,]\d+)?)\s*(?:segundos?|segs?|sec|s)\.?)?"
)


def _numero(texto: str | None) -> float:
    return float(texto.replace(",", ".")) if texto else 0.0


def parsear_tiempo(valor) -> float | None:
    """Segundos a partir de ``mm:ss``, ``hh:mm:ss``, números, ``1m05s``, ``2 min``…; negativos → 0; si no, None."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        if math.isnan(valor) or math.isinf(valor):
            return None
        return max(0.0, float(valor))
    if not isinstance(valor, str):
        return None
    texto = valor.strip().lower()
    negativo = texto.startswith("-")
    texto = texto.lstrip("+-").strip()
    if not texto:
        return None
    segundos: float | None = None
    if (m := _RE_HMS.fullmatch(texto)):
        segundos = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + _numero("0." + m[4] if m[4] else None)
    elif (m := _RE_MS.fullmatch(texto)):
        segundos = int(m[1]) * 60 + int(m[2]) + _numero("0." + m[3] if m[3] else None)
    elif _RE_NUMERO.fullmatch(texto):
        segundos = _numero(texto)
    elif (m := _RE_UNIDADES.fullmatch(texto)) and any(m.group(g) for g in ("h", "m", "s")):
        segundos = _numero(m["h"]) * 3600 + _numero(m["m"]) * 60 + _numero(m["s"])
    elif (m := _RE_HMS.search(texto)):
        segundos = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
    elif (m := _RE_MS.search(texto)):
        segundos = int(m[1]) * 60 + int(m[2])
    if segundos is None:
        return None
    return 0.0 if negativo else segundos


def _recortar(texto: str, maximo: int) -> str:
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) <= maximo:
        return texto
    return texto[: max(1, maximo - 1)].rstrip() + "…"


def _a_numero(valor) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return None
    try:
        numero = float(valor)
    except ValueError:
        return None
    return None if math.isnan(numero) or math.isinf(numero) else numero


def _normalizar_zona(zona) -> dict | None:
    """Zona del modelo → coords 0-1: punto ``{"x","y"}`` (0-1000 o 0-1) o ``{"caja":[ymin,xmin,ymax,xmax]}``."""
    if not isinstance(zona, dict):
        return None
    if "caja" in zona:
        caja = zona["caja"]
        if not isinstance(caja, (list, tuple)) or len(caja) != 4:
            return None
        valores = [_a_numero(v) for v in caja]
        if any(v is None for v in valores):
            return None
        if any(v > 1 for v in valores):
            valores = [v / 1000 for v in valores]
        if any(v < 0 or v > 1 for v in valores):
            return None
        ymin, xmin, ymax, xmax = valores
        x1, x2 = sorted((xmin, xmax))
        y1, y2 = sorted((ymin, ymax))
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            return None
        return {"caja": [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]}
    x, y = _a_numero(zona.get("x")), _a_numero(zona.get("y"))
    if x is None or y is None:
        return None
    if x > 1 or y > 1:
        x, y = x / 1000, y / 1000
    if not (0 <= x <= 1 and 0 <= y <= 1):
        return None
    return {"x": round(x, 4), "y": round(y, 4)}


def _normalizar_importancia(valor) -> int:
    numero = _a_numero(valor)
    if numero is None:
        return 3
    return int(min(5, max(1, round(numero))))


def _momento_desde_bruto(bruto: dict, duracion: float, desplazamiento: float) -> Momento | None:
    """Convierte un dict del modelo en ``Momento``; None si no tiene tiempo parseable."""
    tiempo = None
    for clave in ("tiempo", "tiempo_seg", "timestamp", "time"):
        if clave in bruto:
            tiempo = parsear_tiempo(bruto[clave])
            if tiempo is not None:
                break
    if tiempo is None:
        return None
    tiempo = min(max(0.0, tiempo + desplazamiento), max(0.0, duracion - 0.5))
    titulo = _recortar(str(bruto.get("titulo") or ""), config.MAX_TITULO) or f"Momento a los {formatear_tiempo(tiempo)}"
    descripcion = _recortar(str(bruto.get("descripcion") or ""), config.MAX_DESCRIPCION)
    fuente = str(bruto.get("fuente") or "").strip().lower()
    seccion = str(bruto.get("seccion") or "").strip() or None
    return Momento(
        tiempo_seg=round(tiempo, 3),
        titulo=titulo,
        descripcion=descripcion,
        importancia=_normalizar_importancia(bruto.get("importancia")),
        fuente=fuente if fuente in FUENTES_VALIDAS else "ambos",
        seccion=seccion,
        zona=_normalizar_zona(bruto.get("zona")),
    )


def normalizar_momentos(brutos: list[dict], duracion: float, max_momentos: int | None = None,
                        importancia_minima: int = 1, separacion_min: float = config.SEPARACION_MINIMA_SEG,
                        desplazamiento: float = 0.0) -> tuple[list[Momento], list[str]]:
    """Valida, acota, ordena y filtra los momentos devueltos por el modelo; devuelve (momentos, avisos)."""
    avisos: list[str] = []
    momentos: list[Momento] = []
    sin_tiempo = no_objeto = 0
    for bruto in brutos or []:
        if not isinstance(bruto, dict):
            no_objeto += 1
            continue
        momento = _momento_desde_bruto(bruto, duracion, desplazamiento)
        if momento is None:
            sin_tiempo += 1
        else:
            momentos.append(momento)
    if no_objeto:
        avisos.append(f"{no_objeto} elemento(s) de la lista de momentos no eran objetos y se descartaron.")
    if sin_tiempo:
        avisos.append(f"{sin_tiempo} momento(s) sin tiempo reconocible se descartaron.")

    momentos.sort(key=lambda m: m.tiempo_seg)   # estable: empates en el orden original
    unicos: list[Momento] = []
    fusionados = 0
    for momento in momentos:
        if unicos and momento.tiempo_seg - unicos[-1].tiempo_seg < separacion_min:
            fusionados += 1
            if momento.importancia > unicos[-1].importancia:
                unicos[-1] = momento
            continue
        unicos.append(momento)
    if fusionados:
        avisos.append(f"{fusionados} momento(s) duplicados (a menos de {separacion_min:g} s) se fusionaron.")

    if importancia_minima > 1:
        filtrados = [m for m in unicos if m.importancia >= importancia_minima]
        if len(filtrados) != len(unicos):
            avisos.append(f"{len(unicos) - len(filtrados)} momento(s) con importancia < {importancia_minima} se descartaron.")
        unicos = filtrados

    if max_momentos and len(unicos) > max_momentos:
        mejores = sorted(unicos, key=lambda m: (-m.importancia, m.tiempo_seg))[:max_momentos]
        avisos.append(f"Se conservan los {max_momentos} momentos más importantes de {len(unicos)} (--max-momentos).")
        unicos = sorted(mejores, key=lambda m: m.tiempo_seg)
    return unicos, avisos


def _texto_o_none(valor) -> str | None:
    return valor.strip() or None if isinstance(valor, str) else None


def _desglosar_datos(datos) -> tuple[list, str | None, str | None]:
    """(momentos brutos, titulo_video, resumen) a partir de un objeto, una lista o None."""
    if isinstance(datos, list):
        return datos, None, None
    if not isinstance(datos, dict):
        return [], None, None
    brutos = datos.get("momentos")
    if not isinstance(brutos, list):
        brutos = next((v for v in datos.values() if isinstance(v, list)), [])
    titulo = _texto_o_none(datos.get("titulo_video")) or _texto_o_none(datos.get("titulo"))
    return brutos, titulo, _texto_o_none(datos.get("resumen"))


def construir_resultado(datos: dict | list, info: InfoVideo, modelo: str, texto_bruto: str, uso: Uso | None,
                        truncado: bool, modo: str = "gemini", desplazamiento: float = 0.0,
                        **filtros) -> ResultadoAnalisis:
    """``ResultadoAnalisis`` a partir del JSON del modelo (``filtros``: max_momentos, importancia_minima, separacion_min)."""
    brutos, titulo, resumen = _desglosar_datos(datos)
    avisos: list[str] = []
    if datos is None:
        avisos.append("La respuesta del modelo no contenía un JSON reconocible.")
    momentos, avisos_norm = normalizar_momentos(brutos, info.duracion, desplazamiento=desplazamiento, **filtros)
    avisos.extend(avisos_norm)
    if truncado:
        avisos.append(f"La respuesta llegó cortada; se conservan los {len(momentos)} momentos rescatados.")
    if not momentos:
        avisos.append("El modelo no devolvió ningún momento utilizable.")
    return ResultadoAnalisis(momentos=momentos, modo=modo, modelo=modelo, resumen=resumen, uso=uso,
                             texto_bruto=texto_bruto, truncado=truncado, avisos=avisos, titulo=titulo)


# ----------------------------------------------------------------------------
# 4.3 / 4.4 Análisis síncrono con tramos
# ----------------------------------------------------------------------------

def calcular_tramos(duracion: float, tramo_max_seg: float | None,
                    minimo_ultimo: float = MINIMO_ULTIMO_TRAMO_SEG) -> list[tuple[float, float]]:
    """Tramos consecutivos ``[a, b)``; uno solo ``[(0, duracion)]`` si el video no supera ``tramo_max_seg``."""
    duracion = float(duracion)
    if not tramo_max_seg or tramo_max_seg <= 0 or duracion <= tramo_max_seg:
        return [(0.0, duracion)]
    tramos: list[tuple[float, float]] = []
    inicio = 0.0
    while inicio < duracion:
        fin = min(inicio + float(tramo_max_seg), duracion)
        tramos.append((inicio, fin))
        inicio = fin
    if len(tramos) > 1 and tramos[-1][1] - tramos[-1][0] < minimo_ultimo:
        tramos[-2:] = [(tramos[-2][0], duracion)]
    return tramos


@dataclass
class _Parte:
    """Respuesta ya parseada de un tramo (o del video entero)."""

    tramo: tuple[float, float] | None
    datos: dict | list | None
    truncado: bool
    texto: str
    uso: Uso


def _contar_momentos(datos) -> int:
    return len(_desglosar_datos(datos)[0])


def _elegir_mejor(a: tuple, b: tuple) -> tuple:
    """Entre dos (datos, truncado, texto) prefiere el no truncado y, después, el de más momentos (empate: b)."""
    if a[0] is None:
        return b
    if b[0] is None:
        return a
    if a[1] != b[1]:
        return b if a[1] else a
    return a if _contar_momentos(a[0]) > _contar_momentos(b[0]) else b


def _generar_y_parsear(cliente, modelos: list[str], prompt_sistema: str, construir_contents: Callable[[str], list],
                       avisos: list[str], *, variante_inicial: _Variante | None = None,
                       max_tokens: int = config.MAX_TOKENS_SALIDA,
                       log: Callable[[str], None] = print) -> tuple[dict | list | None, bool, str, Uso, _Variante]:
    """Escalera de fallbacks + parseo + un reintento con el doble de tokens si la respuesta llegó cortada."""
    resp, variante = _generar_con_fallbacks(cliente, modelos, construir_contents, prompt_sistema, max_tokens, avisos,
                                            variante_inicial=variante_inicial, log=log)
    uso = uso_desde_respuesta(resp, variante.modelo)
    datos, truncado, texto = _parsear_respuesta(resp)
    cortada_por_tokens = _razon_fin(resp) == types.FinishReason.MAX_TOKENS
    if truncado or cortada_por_tokens:
        motivo = "finish_reason=MAX_TOKENS" if cortada_por_tokens else "JSON incompleto"
        log(f"Respuesta cortada ({motivo}); se reintenta con max_output_tokens={max_tokens * 2}.")
        try:
            resp2 = _generar(cliente, variante, construir_contents, prompt_sistema, max_tokens * 2)
            uso.sumar(uso_desde_respuesta(resp2, variante.modelo))
            datos2, truncado2, texto2 = _parsear_respuesta(resp2)
        except (errors.APIError, RuntimeError) as exc:
            avisos.append(f"El reintento con más tokens falló ({exc}); se conserva lo rescatado.")
        else:
            datos, truncado, texto = _elegir_mejor((datos, truncado, texto), (datos2, truncado2, texto2))
    if datos is None:
        raise RuntimeError(f"La respuesta del modelo no contiene JSON utilizable: {texto[:200]!r}")
    return datos, truncado, texto, uso, variante


def _desplazamiento_tramo(brutos: list, tramo: tuple[float, float], avisos: list[str]) -> float:
    """Heurística §4.4: tiempos relativos → sumar ``a``; si el máximo supera ``(b-a)+5`` eran absolutos."""
    inicio, fin = tramo
    tiempos = [parsear_tiempo(b.get("tiempo")) for b in brutos if isinstance(b, dict)]
    tiempos = [t for t in tiempos if t is not None]
    if tiempos and max(tiempos) > (fin - inicio) + MARGEN_TIEMPOS_ABSOLUTOS_SEG:
        avisos.append(f"Tramo {formatear_tiempo(inicio)}–{formatear_tiempo(fin)}: el modelo devolvió tiempos "
                      "absolutos; no se suma el desplazamiento.")
        return 0.0
    return inicio


def _resultado_desde_partes(partes: list[_Parte], info: InfoVideo, modelo: str, modo: str, avisos: list[str],
                            precios: dict | None, batch: bool = False, **filtros) -> ResultadoAnalisis:
    """Fusiona las partes (tramos) en un solo ``ResultadoAnalisis`` con tiempos absolutos."""
    brutos_totales: list = []
    titulo = resumen = None
    resumen_mas_largo = None
    uso_total = Uso(modelo=modelo, batch=batch)
    textos: list[str] = []
    varios = len(partes) > 1
    for parte in partes:
        brutos, titulo_parte, resumen_parte = _desglosar_datos(parte.datos)
        tramo = parte.tramo or (0.0, info.duracion)
        desplazamiento = _desplazamiento_tramo(brutos, tramo, avisos) if (varios and parte.tramo) else 0.0
        for bruto in brutos:
            if isinstance(bruto, dict):
                tiempo = parsear_tiempo(bruto.get("tiempo"))
                brutos_totales.append(bruto if tiempo is None else {**bruto, "tiempo": tiempo + desplazamiento})
            else:
                brutos_totales.append(bruto)
        titulo = titulo or titulo_parte
        if resumen_parte and resumen is None and parte is partes[0]:
            resumen = resumen_parte
        if resumen_parte and (resumen_mas_largo is None or len(resumen_parte) > len(resumen_mas_largo)):
            resumen_mas_largo = resumen_parte
        parte.uso.costo_usd = estimar_costo(parte.uso, precios, batch=batch)
        uso_total.sumar(parte.uso)
        if varios:
            textos.append(f"--- tramo {formatear_tiempo(tramo[0])}–{formatear_tiempo(tramo[1])} ---\n{parte.texto}")
        else:
            textos.append(parte.texto)
    datos = {"titulo_video": titulo, "resumen": resumen or resumen_mas_largo, "momentos": brutos_totales}
    truncado = any(p.truncado for p in partes)
    resultado = construir_resultado(datos, info, modelo, "\n\n".join(textos), uso_total, truncado, modo=modo, **filtros)
    resultado.tramos = len(partes)
    resultado.avisos = avisos + resultado.avisos
    return resultado


def analizar_video(cliente, archivo, info: InfoVideo, modelo: str = config.MODELO_POR_DEFECTO,
                   fps: float | None = None, tramo_max_seg: float = config.TRAMO_MAX_MIN * 60,
                   precios: dict | None = None, equipo: str = config.EQUIPO_POR_DEFECTO, *,
                   max_momentos: int | None = None, importancia_minima: int = 1,
                   log: Callable[[str], None] = print) -> ResultadoAnalisis:
    """Analiza un video ya subido (por tramos si es muy largo) y devuelve el ``ResultadoAnalisis``."""
    modelos = [modelo] + [m for m in config.MODELOS_ALTERNATIVOS if m != modelo]
    prompt_sistema = construir_prompt_sistema(equipo)
    tramos = calcular_tramos(info.duracion, tramo_max_seg)
    avisos: list[str] = []
    partes: list[_Parte] = []
    variante: _Variante | None = None
    for k, (inicio, fin) in enumerate(tramos, start=1):
        tramo = (inicio, fin) if len(tramos) > 1 else None
        etiqueta = f" (tramo {k}/{len(tramos)}: {formatear_tiempo(inicio)}–{formatear_tiempo(fin)})" if tramo else ""
        log(f"Analizando {info.nombre} con {variante.modelo if variante else modelo}{etiqueta}…")
        prompt_usuario = construir_prompt_usuario(tramo)
        datos, truncado, texto, uso, variante = _generar_y_parsear(
            cliente, modelos, prompt_sistema,
            lambda sufijo, pu=prompt_usuario, tr=tramo: _construir_contenido(archivo, pu + sufijo, fps, tr),
            avisos, variante_inicial=variante, log=log)
        partes.append(_Parte(tramo=tramo, datos=datos, truncado=truncado, texto=texto, uso=uso))
        log(f"  {_contar_momentos(datos)} momentos brutos, {uso.tokens_total} tokens{' (cortada)' if truncado else ''}.")
    resultado = _resultado_desde_partes(partes, info, variante.modelo, "gemini", avisos, precios,
                                        max_momentos=max_momentos, importancia_minima=importancia_minima)
    log(f"Análisis terminado: {len(resultado.momentos)} momentos.")
    return resultado


# ----------------------------------------------------------------------------
# 4.3b Redactor opcional
# ----------------------------------------------------------------------------

def _borrador_para_redactor(resultado: ResultadoAnalisis) -> dict:
    return {
        "titulo_video": resultado.titulo or "",
        "resumen": resultado.resumen or "",
        "momentos": [
            {"tiempo": m.tiempo, "titulo": m.titulo, "descripcion": m.descripcion, "importancia": m.importancia,
             "fuente": m.fuente, "seccion": m.seccion or ""}
            for m in resultado.momentos
        ],
    }


def _aplicar_redaccion(resultado: ResultadoAnalisis, datos) -> tuple[list[Momento], str | None, str | None]:
    """Momentos con la redacción nueva (solo textos y sección); ``ValueError`` si el redactor alteró la estructura."""
    brutos, titulo, resumen = _desglosar_datos(datos)
    if len(brutos) != len(resultado.momentos):
        raise ValueError(f"el redactor devolvió {len(brutos)} pasos en vez de {len(resultado.momentos)}")
    nuevos: list[Momento] = []
    for original, bruto in zip(resultado.momentos, brutos):
        if not isinstance(bruto, dict):
            raise ValueError("el redactor devolvió un paso que no es un objeto")
        tiempo = parsear_tiempo(bruto.get("tiempo"))
        if tiempo is None or abs(tiempo - original.tiempo_seg) > TOLERANCIA_TIEMPO_REDACTOR_SEG:
            raise ValueError(f"el redactor cambió el tiempo del paso {original.tiempo}")
        titulo_paso = _recortar(str(bruto.get("titulo") or ""), config.MAX_TITULO)
        descripcion = _recortar(str(bruto.get("descripcion") or ""), config.MAX_DESCRIPCION)
        seccion = str(bruto.get("seccion") or "").strip()
        nuevos.append(replace(original, titulo=titulo_paso or original.titulo,
                              descripcion=descripcion or original.descripcion,
                              seccion=seccion or original.seccion))
    return nuevos, titulo, resumen


def pulir_redaccion(cliente, resultado: ResultadoAnalisis, modelo_redactor: str, precios: dict | None = None, *,
                    log: Callable[[str], None] = print) -> ResultadoAnalisis:
    """Segunda pasada solo de texto: mejora títulos, descripciones, secciones y resumen sin tocar tiempos.

    Si algo falla devuelve el resultado original con un aviso.
    """
    if not resultado.momentos:
        return replace(resultado, avisos=resultado.avisos + ["Redactor omitido: no hay momentos que pulir."])
    borrador = json.dumps(_borrador_para_redactor(resultado), ensure_ascii=False, indent=1)
    texto_usuario = PROMPT_REDACTOR_USUARIO + borrador
    avisos: list[str] = []
    log(f"Puliendo la redacción con {modelo_redactor} ({len(resultado.momentos)} pasos, solo texto)…")
    try:
        datos, truncado, _texto, uso, variante = _generar_y_parsear(
            cliente, [modelo_redactor], PROMPT_REDACTOR,
            lambda sufijo: [types.Content(role="user", parts=[types.Part.from_text(text=texto_usuario + sufijo)])],
            avisos, variante_inicial=_Variante(modelo=modelo_redactor, resolucion=False), log=log)
        if truncado:
            raise ValueError("la respuesta del redactor llegó cortada")
        momentos, titulo, resumen = _aplicar_redaccion(resultado, datos)
    except Exception as exc:  # noqa: BLE001 - por contrato: cualquier fallo devuelve el original
        aviso = f"No se pudo pulir la redacción con {modelo_redactor} ({exc}); se conserva el borrador original."
        log("Aviso: " + aviso)
        return replace(resultado, avisos=resultado.avisos + avisos + [aviso])

    uso.costo_usd = estimar_costo(uso, precios)
    modelo_combinado = f"{resultado.modelo}+{variante.modelo}" if resultado.modelo else variante.modelo
    uso_total = Uso(modelo=modelo_combinado)
    if resultado.uso is not None:
        uso_total.sumar(resultado.uso)
        uso_total.batch = resultado.uso.batch
    uso_total.sumar(uso)
    log(f"Redacción pulida ({uso.tokens_total} tokens).")
    return replace(resultado, momentos=momentos, modelo=modelo_combinado, uso=uso_total,
                   resumen=resumen or resultado.resumen, titulo=titulo or resultado.titulo,
                   avisos=resultado.avisos + avisos + [f"Redacción pulida con {variante.modelo}."])


# ----------------------------------------------------------------------------
# 4.6 Batch
# ----------------------------------------------------------------------------

def _tramo_desde(valor) -> tuple[float, float] | None:
    if valor is None:
        return None
    inicio, fin = valor
    return float(inicio), float(fin)


def enviar_lote(cliente, peticiones: list[dict], modelo: str, nombre_lote: str, *,
                equipo: str = config.EQUIPO_POR_DEFECTO, fps: float | None = None,
                log: Callable[[str], None] = print) -> "types.BatchJob":
    """Crea un trabajo batch con una petición por video (o por tramo).

    ``peticiones``: ``[{"archivo": types.File, "info": InfoVideo, "tramo": (a, b) | None}]``; cada dict puede
    traer además ``"equipo"`` y ``"fps"`` propios.  Los metadatos (video, tramo, inicio, fin) permiten mapear las
    respuestas al recoger el lote.
    """
    if not peticiones:
        raise ValueError("No hay peticiones que enviar al lote.")
    contador: dict[str, int] = {}
    solicitudes = []
    for peticion in peticiones:
        info: InfoVideo = peticion["info"]
        tramo = _tramo_desde(peticion.get("tramo"))
        k = contador.get(info.nombre, 0)
        contador[info.nombre] = k + 1
        inicio, fin = tramo if tramo else (0.0, float(info.duracion))
        contenido = _construir_contenido(peticion["archivo"], construir_prompt_usuario(tramo),
                                         peticion.get("fps", fps), tramo)
        cfg = _construir_config(construir_prompt_sistema(peticion.get("equipo", equipo)), config.MAX_TOKENS_SALIDA)
        solicitudes.append(types.InlinedRequest(
            contents=contenido, config=cfg,
            metadata={"video": info.nombre, "tramo": str(k), "inicio": f"{inicio:g}", "fin": f"{fin:g}"}))
    job = cliente.batches.create(model=modelo, src=solicitudes, config=types.CreateBatchJobConfig(display_name=nombre_lote))
    log(f"Lote enviado: {job.name} ({len(solicitudes)} peticiones, modelo {modelo}, estado {job.state}).")
    return job


def estado_lote(cliente, nombre_job: str) -> "types.BatchJob":
    """Estado actual del trabajo batch."""
    return cliente.batches.get(name=nombre_job)


def lote_terminado(job) -> bool:
    """True si el trabajo ya no va a cambiar (éxito, éxito parcial, fallo, cancelado o expirado)."""
    estado = getattr(job, "state", None)
    return estado is not None and estado in ESTADOS_LOTE_TERMINADO


def recoger_lote(cliente, job, mapa_videos: dict, precios: dict | None = None, *,
                 log: Callable[[str], None] = print) -> dict[str, ResultadoAnalisis | Exception]:
    """Convierte las respuestas de un lote terminado en un ``ResultadoAnalisis`` (o ``Exception``) por video.

    ``mapa_videos``: ``{nombre: {"info": InfoVideo, "tramos": [(a, b), ...] (opcional), "modelo": str (opcional),
    "max_momentos": int | None (opcional), "importancia_minima": int (opcional)}}``.
    Las respuestas se mapean por ``metadata["video"]``/``["tramo"]`` y, si faltan los metadatos, por índice.
    """
    resultados: dict[str, ResultadoAnalisis | Exception] = {}
    destino = getattr(job, "dest", None)
    respuestas = list(getattr(destino, "inlined_responses", None) or [])
    if not respuestas:
        error = getattr(job, "error", None)
        motivo = error.message if error is not None and error.message else f"estado {getattr(job, 'state', None)}"
        for nombre in mapa_videos:
            resultados[nombre] = RuntimeError(f"El lote no devolvió respuestas ({motivo}).")
        return resultados

    esperados: list[tuple[str, int, tuple[float, float] | None]] = []
    for nombre, entrada in mapa_videos.items():
        tramos = [_tramo_desde(t) for t in (entrada.get("tramos") or [None])]
        esperados.extend((nombre, k, tramo) for k, tramo in enumerate(tramos))

    partes: dict[str, dict[int, _Parte | Exception]] = {}
    for i, inlined in enumerate(respuestas):
        meta = inlined.metadata or {}
        nombre = meta.get("video")
        k = int(meta["tramo"]) if str(meta.get("tramo", "")).isdigit() else None
        if nombre not in mapa_videos:
            if i >= len(esperados):
                log(f"Aviso: respuesta {i} del lote sin video conocido ({meta}); se ignora.")
                continue
            nombre, k, _ = esperados[i]
        if k is None:
            k = len(partes.get(nombre, {}))
        tramo = None
        if "inicio" in meta and "fin" in meta:
            try:
                tramo = (float(meta["inicio"]), float(meta["fin"]))
            except ValueError:
                tramo = None
        if tramo is None:
            tramos_mapa = mapa_videos[nombre].get("tramos") or []
            tramo = _tramo_desde(tramos_mapa[k]) if k < len(tramos_mapa) else None
        modelo =_nombre_modelo(mapa_videos[nombre].get("modelo") or getattr(job, "model", None))
        if inlined.error is not None:
            partes.setdefault(nombre, {})[k] = RuntimeError(
                f"Gemini devolvió un error para {nombre} (tramo {k}): {inlined.error.code} {inlined.error.message}")
            continue
        try:
            datos, truncado, texto = _parsear_respuesta(inlined.response)
            if datos is None:
                raise RuntimeError(f"la respuesta no contiene JSON utilizable: {texto[:200]!r}")
        except Exception as exc:  # noqa: BLE001 - se registra por video y se sigue con el resto
            partes.setdefault(nombre, {})[k] = RuntimeError(f"Respuesta inválida para {nombre} (tramo {k}): {exc}")
            continue
        uso = uso_desde_respuesta(inlined.response, modelo, batch=True)
        partes.setdefault(nombre, {})[k] = _Parte(tramo=tramo, datos=datos, truncado=truncado, texto=texto, uso=uso)

    for nombre, entrada in mapa_videos.items():
        piezas = partes.get(nombre)
        if not piezas:
            resultados[nombre] = RuntimeError(f"El lote no contiene ninguna respuesta para {nombre}.")
            continue
        errores = [p for p in piezas.values() if isinstance(p, Exception)]
        if errores:
            resultados[nombre] = errores[0]
            continue
        ordenadas = [piezas[k] for k in sorted(piezas)]
        modelo = ordenadas[0].uso.modelo
        try:
            resultado = _resultado_desde_partes(ordenadas, entrada["info"], modelo, "gemini-batch", [], precios,
                                                batch=True, max_momentos=entrada.get("max_momentos"),
                                                importancia_minima=entrada.get("importancia_minima", 1))
        except Exception as exc:  # noqa: BLE001
            resultados[nombre] = RuntimeError(f"No se pudo construir el resultado de {nombre}: {exc}")
            continue
        resultados[nombre] = resultado
        log(f"{nombre}: {len(resultado.momentos)} momentos ({len(ordenadas)} respuesta(s) del lote).")
    return resultados
