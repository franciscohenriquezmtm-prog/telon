"""Constantes y valores por defecto.

Todo lo que aparece aquí se puede sobreescribir desde la línea de comandos
(ver ``resumir_videos.py --help``) o desde el archivo ``.env``.
"""
from __future__ import annotations

from pathlib import Path

RAIZ_PAQUETE = Path(__file__).resolve().parent
CARPETA_FUENTES = RAIZ_PAQUETE / "fuentes"          # DejaVuSans.ttf y DejaVuSans-Bold.ttf (incluidas)

# ----------------------------------------------------------------------------
# Gemini
# ----------------------------------------------------------------------------
MODELO_POR_DEFECTO = "gemini-3.1-flash-lite"
# Si el modelo por defecto no existe para la clave del usuario (error 404), se prueban estos en orden.
MODELOS_ALTERNATIVOS = ["gemini-flash-lite-latest", "gemini-2.5-flash-lite"]

# Precios en USD por millón de tokens.  *** VERIFICAR *** en
# https://ai.google.dev/gemini-api/docs/pricing  (cambian con el tiempo).
# El de entrada de gemini-3.1-flash-lite (0,25) viene de la conversación de diseño;
# el de salida es una estimación conservadora.  Se pueden fijar con --precio-entrada/--precio-salida.
PRECIOS_USD_POR_MILLON = {
    "gemini-3.1-flash-lite": {"entrada": 0.25, "salida": 1.50},
    "gemini-flash-lite-latest": {"entrada": 0.25, "salida": 1.50},
    "gemini-2.5-flash-lite": {"entrada": 0.10, "salida": 0.40},
}
DESCUENTO_BATCH = 0.5            # el modo batch cuesta la mitad

TIMEOUT_HTTP_MS = 600_000        # por petición HTTP (subida por trozos de 8 MB y generación)
TIMEOUT_PROCESADO_SEG = 1800     # espera máxima a que el archivo subido pase de PROCESSING a ACTIVE
INTERVALO_SONDEO_ARCHIVO_SEG = 10
INTERVALO_SONDEO_LOTE_SEG = 60
MAX_TOKENS_SALIDA = 16384        # si la respuesta llega cortada (MAX_TOKENS) se reintenta con el doble
TEMPERATURA = 0.2
FPS_POR_DEFECTO = None           # None = el muestreo por defecto de Gemini (1 fotograma/s)
RESOLUCION_VIDEO = "media"       # resolución con la que Gemini mira el video: baja | media | alta
                                 # (baja ≈ 100 tokens/s, media ≈ 300 tokens/s).  Con textos e iconos en pantalla
                                 # conviene "media"; "baja" solo para ahorrar cuando basta reconocer la máquina.
REFINAR_CON_CAPTURAS = True      # segunda pasada: se envían las capturas en alta resolución (imágenes fijas,
                                 # muy baratas) para corregir textos con lo que se lee en pantalla. --sin-refinado
REFINADO_MAX_LADO_PX = 1280      # las capturas se reducen a este lado mayor antes de enviarlas
REFINADO_LOTE = 15               # capturas por petición en el refinado
TRAMO_MAX_MIN = 45               # videos más largos se analizan por tramos (start_offset/end_offset)
EQUIPO_POR_DEFECTO = "un equipo médico (por ejemplo un arco en C de fluoroscopía o una máquina de radioterapia)"
REDACTOR_MODELO = None           # opcional (--redactor M): segunda pasada SOLO de texto con un modelo más potente
                                 # que pule la redacción del protocolo sin cambiar tiempos ni inventar datos

# ----------------------------------------------------------------------------
# Momentos: SIN tope.  La cantidad la decide el contenido del video.
# ----------------------------------------------------------------------------
MAX_MOMENTOS = None              # None = sin límite.  --max-momentos N para limitar (se quedan los más importantes)
IMPORTANCIA_MINIMA = 1           # 1 = conservar todos los momentos que devuelva el modelo
SEPARACION_MINIMA_SEG = 2.0      # dos momentos más cercanos que esto son duplicados: se conserva el más importante
MAX_TITULO = 80                  # caracteres
MAX_DESCRIPCION = 260            # caracteres (~2 frases)

# ----------------------------------------------------------------------------
# Video / ffmpeg
# ----------------------------------------------------------------------------
EXTENSIONES_VIDEO = {".mp4", ".m4v", ".mov", ".webm", ".mpeg", ".mpg", ".avi", ".wmv",
                     ".3gp", ".mkv", ".mts", ".m2ts", ".ts", ".flv"}
# Tipos MIME explícitos para la subida (mimetypes.guess_type no es fiable).
MIME_SUBIDA = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mpeg": "video/mpeg", ".mpg": "video/mpeg", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv", ".3gp": "video/3gpp",
}
# Solo estos contenedores se suben tal cual; el resto (p. ej. .MOV HEVC de iPhone) se transcodifica a
# una copia ligera MP4 H.264 antes de subir.  Las capturas SIEMPRE salen del archivo original.
EXTENSIONES_SUBIDA_DIRECTA = {".mp4", ".m4v", ".webm"}
UMBRAL_TRANSCODIFICAR_MB = 300   # aunque sea .mp4, por encima de este tamaño se sube la copia ligera
MAX_SUBIDA_MB = 1900             # límite de la Files API: 2 GB por archivo
TRANSCODIFICAR_ALTO = 720        # resolución de la copia ligera (--copia-alto 480|720|1080); hay texto e iconos
                                 # en pantalla, así que 720p es el mínimo razonable. --subir-original la evita.
TRANSCODIFICAR_FPS = 2           # fotogramas/s de la copia ligera (Gemini muestrea 1/s por defecto)
TRANSCODIFICAR_AUDIO_KBPS = 64   # el audio importa (lo que se dice): mono AAC 64 kbps

ANCHO_MAX_CAPTURA = 1280         # las capturas se reducen a este ancho como máximo
VENTANA_NITIDEZ_SEG = 1.0        # se busca el fotograma más nítido en ±1 s
FPS_RAFAGA = 2.5                 # fotogramas por segundo que se evalúan dentro de la ventana
UMBRAL_ESCENA = 3.0              # puntuación mínima de scdet para considerar un cambio de escena (modo local)

# ----------------------------------------------------------------------------
# Documentos
# ----------------------------------------------------------------------------
POR_PAGINA = "auto"              # "auto" | 1 | 2 | 3 | 4 capturas por página
FUENTE_DOCX = "Calibri"
INCLUIR_INDICE = True            # índice de pasos por sección tras la portada
ANOTAR_CAPTURAS = True           # dibujar círculo/flecha cuando el modelo indica una zona

# ----------------------------------------------------------------------------
# Modo local (sin API)
# ----------------------------------------------------------------------------
WHISPER_MODELO = "base"          # tiny | base | small | medium | large-v3 | ruta a un modelo descargado
WHISPER_IDIOMA = "es"
SEPARACION_MINIMA_LOCAL_SEG = 8.0

# ----------------------------------------------------------------------------
# Carpetas y nombres de archivo
# ----------------------------------------------------------------------------
CARPETA_VIDEOS = "videos"
CARPETA_SALIDA = "salida"
CARPETA_CAPTURAS = "capturas"
CARPETA_LOTES = "_lotes"         # salida/_lotes/<id>.json con los trabajos batch pendientes
NOMBRE_JSON = "momentos.json"
NOMBRE_LOG = "log.txt"
NOMBRE_ERROR = "error.txt"
