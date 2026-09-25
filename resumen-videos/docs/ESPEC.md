# Especificación de implementación — `resumen_videos`

Documento de contrato entre módulos. Los módulos se escriben en paralelo por
personas/agentes distintos: **respeta las firmas y estructuras tal cual**; si
necesitas algo que no está aquí, añádelo como función nueva sin cambiar las
existentes. Idioma del código, comentarios, mensajes y documentación: **español**
(identificadores en español sin tildes, p. ej. `extraer_mejor_fotograma`).

## 0. Objetivo y principio rector

Caso real: videos grabados con iPhone en un hospital donde un colega explica cómo
se usa un equipo médico (el primer caso es un **arco en C** de fluoroscopía; también
máquinas de radioterapia u otros). La cámara cambia de ángulo, hace zoom y enfoca
botones, el brazo, conexiones, la pantalla de ingreso de datos del paciente, la toma
del rayo, el cierre… mientras la persona lo va explicando. Duración típica 15-25
min (rango 2-40 min); los archivos pueden ser **pesados** (.MOV HEVC 4K/60 fps de
varios GB). Por cada video se genera un **documento didáctico con capturas** (.docx
editable + .pdf) que funcione como **manual o protocolo sencillo**: muchas
imágenes, poco texto, pasos numerados agrupados por sección. Es trabajo clínico:
**rigor** (fiel a lo que se dice y se ve, valores exactos, sin inventar; si algo no
se ve u oye con claridad, se dice).

**El ancla es el contenido, no un rango.** La cantidad de momentos (y por tanto de
capturas y de páginas) la decide lo que el video enseña, sumando lo que se **ve**
y lo que se **dice**. Si en 10 minutos la persona enseña 30 cosas y las va
señalando, salen 30 capturas con su texto; si en 10 minutos solo dice 3 cosas
importantes, salen 3. No hay mínimo ni máximo por defecto (`config.MAX_MOMENTOS =
None`); `--max-momentos` e `--importancia-minima` existen solo como filtros
opcionales del usuario. **Nunca** escribas un prompt que pida "entre X e Y
momentos" ni recortes por número salvo que el usuario lo pida.

Cada momento lleva: tiempo, título estilo manual, descripción breve (qué se hace y
por qué, con los valores exactos que se mencionen), importancia 1-5, fuente
(visual/audio/ambos), sección (fase del procedimiento) y, opcionalmente, la zona
de la imagen que la persona señala, para dibujar un círculo/flecha en la captura.

## 1. Estructura de archivos

```
resumen-videos/
├── resumir_videos.py            CLI (único punto de entrada)
├── resumen_videos/
│   ├── __init__.py              (hecho)
│   ├── config.py                (hecho) constantes
│   ├── modelos.py               (hecho) InfoVideo, Momento, Uso, ResultadoAnalisis, ResultadoVideo, formatear_tiempo
│   ├── video.py                 ffmpeg: binarios, información, fotogramas nítidos, escenas, audio, transcodificar
│   ├── gemini.py                Files API, prompt+esquema, análisis (con tramos y fallbacks), batch, JSON robusto, costo
│   ├── documentos.py            .docx y .pdf (portada, índice, pasos en rejilla)
│   ├── anotar.py                círculo/flecha sobre la captura (Pillow)
│   ├── local.py                 modo local (scdet + faster-whisper) y modo simulado
│   ├── pipeline.py              orquestación: carpeta → videos → salida/<nombre>/
│   └── fuentes/                 DejaVuSans.ttf, DejaVuSans-Bold.ttf, LICENCIA-DejaVu.txt (hecho)
├── tools/crear_video_prueba.py  genera un video sintético con escenas, rótulos y tonos (para probar)
├── tests/                       pytest; conftest.py con fixture `video_prueba` (ruta a un mp4 sintético)
├── videos/                      carpeta de entrada por defecto (vacía, .gitkeep)
├── salida/                      se crea al ejecutar (ignorada por git)
├── docs/ESPEC.md                este documento
├── README.md, requirements.txt, .env.ejemplo, .gitignore
```

## 2. Convenciones comunes

- Python **≥ 3.10** (`from __future__ import annotations`, nada exclusivo de 3.11+). Debe correr en
  **Windows** (el usuario usa PowerShell), macOS y Linux: `pathlib`, `subprocess.run([...])` con lista
  de argumentos (nunca `shell=True`), `encoding="utf-8", errors="replace"` al leer salida de procesos,
  `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` en la CLI. Nombres de archivo seguros
  para Windows (sin `: * ? " < > |`, sin punto/espacio final).
- Cada función que informa progreso recibe `log: Callable[[str], None] = print` como último parámetro
  con nombre. Los módulos **no** imprimen directamente salvo a través de `log`.
- Errores: las funciones lanzan excepciones normales (`RuntimeError`, `ValueError`, `FileNotFoundError`,
  `google.genai.errors.APIError`); es `pipeline.py` quien las captura por video y sigue con el siguiente.
- Nunca se imprime ni se guarda la clave de API. `.env` se carga con `python-dotenv`: primero el `.env` junto a
  `resumir_videos.py` (manda) y después, si es otro archivo, el del directorio actual; **no** se recorren las
  carpetas padre (nada de `find_dotenv(usecwd=True)`: un `.env` ajeno no debe imponer su clave o su modelo). La
  CLI imprime qué archivo leyó y avisa si existe `.env.txt` (Bloc de notas) sin `.env`.
  Variables: `GEMINI_API_KEY` (preferida) o `GOOGLE_API_KEY`; opcionales `FFMPEG_BIN`, `FFPROBE_BIN`,
  `WHISPER_CACHE`, `GEMINI_MODELO`.
- Tipos compartidos: **solo** los de `modelos.py`. Un momento es `Momento`; un análisis es
  `ResultadoAnalisis`; el uso de tokens es `Uso`.
- Importa `config` como `from . import config` y usa sus constantes como valores por defecto.
- Tests con `pytest`; los que necesitan ffmpeg usan la fixture `video_prueba` y hacen `pytest.skip` si no
  hay ffmpeg. Los tests de Gemini **no** llaman a la red: usan un cliente falso (ver §4.7).

Recursos ya verificados que debes reutilizar (léelos antes de escribir tu módulo):
- `/tmp/claude-0/-home-user-telon/17b2412d-fad4-5bdf-b01b-ba561aa318b4/scratchpad/genai_api_notes.md`
- `/tmp/claude-0/-home-user-telon/17b2412d-fad4-5bdf-b01b-ba561aa318b4/scratchpad/ffmpeg_notes.md`
- `/tmp/claude-0/-home-user-telon/17b2412d-fad4-5bdf-b01b-ba561aa318b4/scratchpad/whisper_notes.md`
- Prototipos probados en `/tmp/claude-0/-home-user-telon/17b2412d-fad4-5bdf-b01b-ba561aa318b4/scratchpad/prototipos/`:
  `proto_docs.py` (docx/pdf, 563 líneas, verificado), `frame_tools.py`, `probe_helpers.py`, `scene_detect.py`,
  `sharpness.py`, `transcribe_local.py`, `make_test_video.py`, `verificar_proto.py`.
- Binarios en este contenedor: ffmpeg vía `imageio_ffmpeg.get_ffmpeg_exe()` (sin `drawtext`); ffprobe en
  `/tmp/claude-0/-home-user-telon/17b2412d-fad4-5bdf-b01b-ba561aa318b4/scratchpad/bin/ffprobe` (expórtalo como
  `FFPROBE_BIN` o añade `.../scratchpad/bin` al PATH para probar la ruta con ffprobe; el código debe funcionar
  también SIN ffprobe). Video sintético de 90 s con 8 escenas:
  `/tmp/claude-0/-home-user-telon/17b2412d-fad4-5bdf-b01b-ba561aa318b4/scratchpad/videos_prueba/prueba_maquina.mp4`.
- No hay clave de Gemini en este entorno ni acceso a huggingface.co: el camino real de Gemini y la descarga de
  modelos Whisper NO se pueden ejecutar aquí; se prueban con dobles.

## 3. `video.py`

```python
def localizar_ffmpeg(ruta: str | None = None) -> str
    # orden: argumento (--ffmpeg) → env FFMPEG_BIN → imageio_ffmpeg.get_ffmpeg_exe() (incluido; import perezoso,
    # cacheado) → shutil.which("ffmpeg") → RuntimeError con instrucciones claras.  El incluido va ANTES que el del
    # PATH: un ffmpeg viejo de otro programa (Anaconda) puede no tener libx264/scdet/zscale.  Recuerda el último
    # ffmpeg localizado (para que localizar_ffprobe busque a su lado aunque no se le pase).
def localizar_ffprobe(ruta: str | None = None, ffmpeg: str | None = None) -> str | None
    # argumento → env FFPROBE_BIN → junto a `ffmpeg` (el ya elegido; si no se pasa, el último localizado o
    # localizar_ffmpeg()) → shutil.which → None (no es obligatorio).  Quien ya tiene el ffmpeg debe pasarlo.
def version_ffmpeg(ffmpeg: str) -> str   # "7.0.2-static" según `ffmpeg -version` (cacheado); "?" si no responde
TONEMAP_HDR: bool = True   # constante de módulo; la CLI la pone en False con --sin-tonemap (ver es_hdr)
def sanear_nombre(nombre: str) -> str
    # letras/dígitos/áéíóúüñ/espacio/-/_ ; colapsa espacios; quita puntos/espacios finales; máx 80 chars;
    # "" -> "video"
def obtener_info(ruta: Path, ffmpeg: str, ffprobe: str | None = None) -> InfoVideo
    # ffprobe → regex sobre stderr de `ffmpeg -hide_banner -i` (exit 1 es normal) → PyAV (import perezoso).
    # nombre = sanear_nombre(ruta.stem).  RuntimeError si no se obtiene la duración.  Si la rotación es ±90/270
    # (iPhone en vertical) ancho y alto se devuelven ya intercambiados (1080x1920), como se ve el video.
def extraer_fotograma(ruta_video: Path, t: float, destino: Path, ffmpeg: str,
                      ancho_max: int = config.ANCHO_MAX_CAPTURA) -> Path | None
    # `-ss t -i V -frames:v 1 -vf "scale='min(ANCHO,iw)':-2" -pix_fmt yuvj420p -q:v 2 destino`; None si no
    # existe el archivo al terminar (ffmpeg sale 0 aunque t esté fuera del video).
def nitidez(ruta_imagen: Path) -> float
    # varianza del laplaciano 3x3 en escala de grises sobre miniatura ≤ 640 px (Pillow + numpy).
def extraer_mejor_fotograma(ruta_video: Path, t: float, destino: Path, ffmpeg: str, duracion: float,
                            ventana: float = config.VENTANA_NITIDEZ_SEG, fps_rafaga: float = config.FPS_RAFAGA,
                            ancho_max: int = config.ANCHO_MAX_CAPTURA, *, log=print) -> tuple[Path | None, float | None]
    # acota t a [0, duracion-0.5]; ráfaga `-ss max(0,t-ventana) -t 2*ventana -i V -vf "fps=F,scale=..."` a un
    # directorio temporal; elige el de mayor nitidez; lo mueve a `destino`; devuelve (destino, t_real) donde
    # t_real = inicio_rafaga + (i-1)/F.  Si la ráfaga no produce archivos: extraer_fotograma en t, luego en
    # t-1; si nada, (None, None).  Nunca lanza por un fotograma: devuelve None y hace log del motivo.
def detectar_escenas(ruta_video: Path, ffmpeg: str, umbral: float = config.UMBRAL_ESCENA) -> list[tuple[float, float]]
    # `-vf "scdet=threshold=UMBRAL" -f null -`; parsea `lavfi.scd.score: X, lavfi.scd.time: T`; [(t, score)] ordenado.
def extraer_audio_16k(ruta_video: Path, destino_wav: Path, ffmpeg: str) -> Path
def transcodificar_para_subida(ruta_video: Path, destino_mp4: Path, ffmpeg: str,
                               alto: int = config.TRANSCODIFICAR_ALTO, fps: int = config.TRANSCODIFICAR_FPS,
                               *, log=print) -> Path
    # `-vf "fps=FPS,scale=w='if(gt(iw,ih),-2,2*trunc(min(ALTO,iw)/2))':h='if(gt(iw,ih),2*trunc(min(ALTO,ih)/2),-2)'
    #  [,TONEMAP]" -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -c:a aac -b:a 64k -ac 1 -movflags +faststart`.
    # ALTO es el lado MENOR de la copia (720 → 1280x720 en horizontal y 720x1280 en vertical; limitar solo el alto
    # dejaba los videos verticales en 406x720, ilegibles); no se amplía.  fps va primero (menos fotogramas que
    # escalar) y el tonemap HDR, si toca, al final.  Conserva los tiempos (no corta nada).
def necesita_transcodificar(info: InfoVideo, umbral_mb: int = config.UMBRAL_TRANSCODIFICAR_MB) -> str | None
    # devuelve el motivo ("extensión .mov: se sube una copia ligera", "tamaño 2300 MB > 300 MB") o None.
    # Regla: extensión fuera de config.EXTENSIONES_SUBIDA_DIRECTA, o tamaño > umbral_mb.  Estrategia iPhone:
    # el .MOV HEVC de varios GB nunca se sube; se sube la copia 480p/2 fps con audio mono 64 kbps (decenas de MB)
    # y las capturas se sacan del original en alta calidad.
def es_hdr(info_extra: dict) -> bool
    # obtener_info debe guardar en InfoVideo.extra (añade el campo `extra: dict = field(default_factory=dict)` a
    # InfoVideo en modelos.py) color_transfer/pix_fmt/codec cuando ffprobe está disponible.  HDR = color_transfer en
    # {"smpte2084", "arib-std-b67"}.  Si el video es HDR, `video.TONEMAP_HDR` es True (no se pasó --sin-tonemap) y el
    # ffmpeg tiene los filtros zscale+tonemap (comprobar con `ffmpeg -hide_banner -filters` una vez y cachear), las
    # capturas y la copia ligera añaden AL FINAL de la cadena (después de fps y scale: solo se convierten los
    # fotogramas conservados y ya reducidos, ~10 veces más rápido en 4K60 con la misma salida)
    # "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,zscale=t=bt709:m=bt709:r=tv,format=yuv420p".
    # Si no hay filtros se extraen tal cual (pueden verse lavadas; aviso).  En todos los casos se registra UNA línea
    # por video diciendo qué se hace (conversión a SDR / desactivada con --sin-tonemap / sin filtros).  Los .MOV de
    # iPhone con rotación se auto-rotan (ffmpeg).
def es_video(ruta: Path) -> bool   # extensión en config.EXTENSIONES_VIDEO
def listar_videos(carpeta: Path) -> list[Path]   # ordenados por nombre, sin ocultos
```
`tools/crear_video_prueba.py [destino] [--duracion 90] [--escenas 8]`: adapta `make_test_video.py`
(overlay de rótulos con Pillow + fuente DejaVu del paquete, tonos por escena, tamaño < 3 MB). Debe poder
importarse (`crear_video_prueba(destino, duracion=90, escenas=8) -> Path`).
`tests/conftest.py`: fixture de sesión `video_prueba` → genera el video en `tmp_path_factory` con
`tools/crear_video_prueba.py` (skip si no hay ffmpeg); fixture `ffmpeg` → ruta o skip.

## 4. `gemini.py`

### 4.1 Prompt y esquema (constantes públicas)
`PROMPT_SISTEMA` (instrucción de sistema, español), `PROMPT_USUARIO` (texto que acompaña al video),
`ESQUEMA_RESPUESTA` (dict JSON Schema para `response_json_schema`; **crear una copia nueva por petición**,
nunca `response_schema`).
Contenido obligatorio del prompt (redáctalo bien, es el corazón del proyecto):
- Rol: instructor clínico experto en la operación de equipos médicos (`equipo` es un parámetro de texto que se
  inserta en el prompt; por defecto `config.EQUIPO_POR_DEFECTO`; la CLI lo expone como `--equipo "arco en C"`);
  ve el video completo (imagen y audio). Rigor: "Es documentación clínica: sé fiel a lo que se dice y se ve, usa
  los valores, nombres de botones y ajustes exactos que se mencionen, no inventes ni completes con conocimiento
  general; si algo no se ve u oye con claridad, indícalo en la descripción (p. ej. 'valor no audible')".
- Tarea: extraer **todos** los momentos con valor didáctico para armar un manual/protocolo sencillo con
  capturas: cada paso del procedimiento, cada parte de la máquina que se muestra o señala, cada explicación,
  advertencia, valor o parámetro que se menciona, cada error a evitar. La cantidad la decide el contenido:
  "si en 10 minutos se enseñan 30 cosas, devuelve 30; si solo hay 3 importantes, devuelve 3. No rellenes con
  momentos triviales ni omitas ninguno importante. Presta tanta atención a lo que se DICE como a lo que se VE:
  si la persona explica algo relevante sin que cambie la imagen, es un momento igual". Sin rangos numéricos.
- Por momento: `tiempo` "mm:ss" desde el inicio del video (nunca hh:mm:ss; si pasa de una hora, minutos > 59),
  elegido como el instante en que **mejor se ve** lo descrito; `titulo` (≤ 8 palabras, estilo manual:
  "Ajustar el colimador a 10 cm"); `descripcion` (1-2 frases, ≤ 260 caracteres, qué se hace y por qué, con los
  valores exactos que se digan); `importancia` 1-5; `fuente` visual|audio|ambos; `seccion` (nombre de la fase,
  consistente entre momentos consecutivos, típicamente 3-8 secciones por video); `zona` opcional
  `{"x": 0-1000, "y": 0-1000}` = punto de la imagen que la persona señala o donde está lo importante (omitir
  si no aplica). Sin emojis ni símbolos especiales. Español neutro. Orden cronológico.
- Nivel documento: `titulo_video` (título corto del manual), `resumen` (2-3 frases: qué enseña el video),
  `momentos` (lista).
- Para tramos (§4.4): "Este fragmento corresponde al tramo mm:ss–mm:ss del video completo; los tiempos van
  relativos al inicio del fragmento".

### 4.2 Cliente y subida
```python
def crear_cliente(api_key: str, timeout_ms: int = config.TIMEOUT_HTTP_MS) -> "genai.Client"
    # genai.Client(api_key=..., http_options=types.HttpOptions(timeout=timeout_ms, retry_options=types.HttpRetryOptions()))
    # (sin retry_options el SDK NO reintenta).
def obtener_api_key() -> str | None     # GEMINI_API_KEY, si no GOOGLE_API_KEY; "" cuenta como ausente
def subir_video(cliente, ruta: Path, nombre: str, timeout_procesado: int = config.TIMEOUT_PROCESADO_SEG,
                intervalo: int = config.INTERVALO_SONDEO_ARCHIVO_SEG, *, conservar_subida: bool = False,
                log=print) -> "types.File"
    # mime explícito de config.MIME_SUBIDA (ValueError si no está); UploadFileConfig(mime_type, display_name);
    # sondea mientras state == PROCESSING; FAILED -> RuntimeError(f.error.message); timeout -> RuntimeError.
    # En TODOS los caminos de fallo tras la subida (FAILED, timeout, error de red, Ctrl+C) borra el archivo remoto
    # antes de propagar, salvo conservar_subida (son videos clínicos: no deben quedar en Google sin usarse).
def obtener_archivo(cliente, nombre: str) -> "types.File"       # files.get (p. ej. para reenviar un lote)
def eliminar_archivo(cliente, archivo, *, log=print) -> None    # nunca lanza (solo log)
```
### 4.3 Análisis de un video (síncrono)
```python
def analizar_video(cliente, archivo, info: InfoVideo, modelo: str = config.MODELO_POR_DEFECTO,
                   fps: float | None = None, tramo_max_seg: float = config.TRAMO_MAX_MIN * 60,
                   precios: dict | None = None, equipo: str = config.EQUIPO_POR_DEFECTO,
                   resolucion: str = config.RESOLUCION_VIDEO, *, max_momentos: int | None = None,
                   importancia_minima: int = 1, temperatura: float | None = config.TEMPERATURA, log=print) -> ResultadoAnalisis
```
- Construye `contents=[types.Content(role="user", parts=[types.Part(file_data=types.FileData(file_uri=archivo.uri,
  mime_type=archivo.mime_type), video_metadata=<opcional>), types.Part.from_text(text=PROMPT_USUARIO...)])]`.
  `video_metadata=types.VideoMetadata(fps=fps, start_offset="Ns", end_offset="Ms")` solo cuando hay fps o tramo.
- `config=types.GenerateContentConfig(system_instruction=PROMPT_SISTEMA, temperature=<temperatura o None>,
  max_output_tokens=config.MAX_TOKENS_SALIDA, response_mime_type="application/json",
  response_json_schema=<copia de ESQUEMA_RESPUESTA>, media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
  thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW))`.
  `config.TEMPERATURA = None`: no se envía `temperature` (vale la del modelo; Google recomienda no bajarla en Gemini 3,
  donde valores bajos degradan el razonamiento y provocan cortes por MAX_TOKENS). La CLI expone `--temperatura T`,
  que llega a análisis, refinado, redactor y batch (`Opciones.temperatura`).
- **Escalera de fallbacks** (una función `_generar_con_fallbacks` que prueba en orden y registra cada paso en
  `avisos`): (1) tal cual; (2) `ClientError` 400 cuyo mensaje mencione `thinking` → sin `thinking_config`;
  (3) 400 que mencione `mediaResolution`/`media_resolution` → sin `media_resolution`; (4) 400 que mencione
  `responseJsonSchema`/`schema` → sin esquema (dejando `response_mime_type` y añadiendo "Responde SOLO con el
  JSON" al prompt); (5) `ClientError` 404/NOT_FOUND o mensaje "not found"/"not supported" con el modelo →
  siguiente modelo de `config.MODELOS_ALTERNATIVOS` (repitiendo 1-4). Otras excepciones se propagan.
- Respuesta: `resp.text` None → RuntimeError con `prompt_feedback.block_reason` / `finish_reason`, **salvo** que
  `finish_reason == MAX_TOKENS` (el pensamiento consumió todo el presupuesto y no hay texto): ese caso cuenta como
  respuesta truncada vacía y llega al reintento; si el reintento tampoco trae texto, RuntimeError que lo explica.
  Parseo: `resp.parsed` si es dict; si no, `extraer_json(resp.text)`. Si `finish_reason == MAX_TOKENS` o el JSON
  quedó truncado → **un** reintento con `max_output_tokens*2`; si sigue truncado, conserva lo rescatado y
  `truncado=True`. Cuando no hay JSON utilizable, el `RuntimeError` lleva en `.uso` los tokens ya pagados.
- Uso: `uso_desde_respuesta(resp, modelo, batch=False) -> Uso` (usage_metadata puede ser None → ceros;
  `prompt_tokens_details` → `detalle_entrada` por `modality.value`); `estimar_costo(uso, precios, batch)`.
### 4.3a Resolución de análisis y refinado con capturas en alta (IMPORTANTE: hay texto e iconos en pantalla)
- `analizar_video(..., resolucion: str = config.RESOLUCION_VIDEO, ...)`: "baja" → `MEDIA_RESOLUTION_LOW`, "media" →
  `MEDIA_RESOLUTION_MEDIUM`, "alta" → `MEDIA_RESOLUTION_HIGH`. Por defecto **media**. Constante pública
  `RESOLUCIONES = {"baja": ..., "media": ..., "alta": ...}`. (Igual en las peticiones batch: `enviar_lote(..., resolucion=...)`.)
- La copia ligera que se sube es 720p por defecto (`config.TRANSCODIFICAR_ALTO`); la CLI expone `--copia-alto 480|720|1080`
  y `--subir-original` (sube el archivo tal cual si cabe en `MAX_SUBIDA_MB` y su extensión está en `MIME_SUBIDA`).
```python
def refinar_con_capturas(cliente, resultado: ResultadoAnalisis, modelo: str, precios: dict | None = None,
                         equipo: str = config.EQUIPO_POR_DEFECTO, max_lado_px: int = config.REFINADO_MAX_LADO_PX,
                         lote: int = config.REFINADO_LOTE, *, temperatura: float | None = config.TEMPERATURA,
                         log=print) -> ResultadoAnalisis
```
Segunda pasada tras extraer las capturas del ORIGINAL (alta resolución). Para los momentos con `ruta_captura`: carga el
JPEG con Pillow, lo reduce a `max_lado_px` de lado mayor (JPEG calidad 85, en memoria) y lo envía como
`types.Part.from_bytes(data=..., mime_type="image/jpeg")`, en lotes de `lote` capturas por petición, precedida cada
imagen de un texto "Captura del paso N (mm:ss)" con el **tiempo real del fotograma** (`tiempo_real_seg`; si no hay, el
pedido). Junto con las imágenes va el JSON de esos momentos (número, tiempo, título, descripción, **importancia,
fuente**, sección, zona) y la instrucción: "Corrige y completa el título y la descripción de cada paso con lo que ahora
se lee con claridad: texto en pantalla, valores y unidades, nombres de botones, iconos, indicadores. Conserva siempre lo
que la descripción dice que se DIJO en el audio (la captura no lo muestra); si la pantalla contradice lo dicho, escribe
ambos. Los pasos con fuente "audio" solo se completan, no se corrigen. Ajusta la zona señalada si con la imagen se ve
mejor dónde está lo importante; si el elemento señalado no aparece en la captura, devuelve zona null; si no devuelves
zona se conserva la actual. NO cambies los tiempos, NO agregues ni quites pasos, NO inventes: si en la captura no se lee
nada nuevo, deja el texto igual. Devuelve el mismo JSON (lista de momentos con el mismo número)". Esquema:
`ESQUEMA_REFINADO` (objeto con `momentos`: lista de {numero:int, titulo, descripcion, zona: `{"anyOf": [ESQUEMA_ZONA,
{"type": "null"}]}`}).
Fusión: por `numero` (respaldo: por orden); solo se actualizan `titulo`, `descripcion` y `zona` (normalizados con las
mismas reglas de §4.5); los demás campos se conservan. Reglas de fusión (`_refinar_momento`): con `fuente == "audio"`
el texto nuevo solo se acepta si **contiene** el original (completar, no corregir); `zona` ausente → se conserva, `null`
→ se quita, inválida → se conserva; si el título o la descripción cambian, el texto previo queda en
`Momento.titulo_original` / `descripcion_original` (se conserva el más antiguo; salen en el JSON solo cuando existen)
para poder auditar el cambio. `Uso` se suma (`modelo` = "<modelo> (+refinado)"). Cualquier excepción → se devuelve el
resultado original con un aviso y se sigue, **sumando igualmente los tokens ya pagados** (`modelo` = "<modelo> (+refinado
fallido)", el aviso dice cuántos). Se usa por defecto (`config.REFINAR_CON_CAPTURAS`); la CLI expone `--sin-refinado`.
Orden en pipeline: análisis → capturas → **refinado** → redactor (si hay) → anotaciones → documentos.

### 4.3b Redactor opcional (segunda pasada solo de texto)
```python
def pulir_redaccion(cliente, resultado: ResultadoAnalisis, modelo_redactor: str, precios=None, *,
                    temperatura: float | None = config.TEMPERATURA, log=print) -> ResultadoAnalisis
```
Envía **solo texto** (el JSON de momentos, sin video: barato) a `modelo_redactor` con la instrucción: "Eres el
redactor de un manual clínico. Mejora título y descripción de cada paso para que sean claros, precisos y
homogéneos (imperativo, estilo protocolo), corrige la agrupación en secciones si es incoherente y redacta el
resumen. NO cambies tiempos, NO añadas ni quites pasos, NO inventes datos que no estén en el borrador. Devuelve el
mismo JSON". Mismo esquema, mismo parseo; si falla (cualquier excepción) devuelve el resultado original con un
aviso (sumando los tokens de la llamada pagada, `modelo` "…+b (fallido)"). Suma el `Uso`: `resultado.modelo` pasa a
"a+b" y `uso.modelo` conserva la historia ("a (+refinado)+b"). Solo se usa si `--redactor` está presente y va
**después** del refinado con capturas (para que este no deshaga su trabajo); nunca con `--regenerar`.

### 4.4 Tramos (videos largos)
Si `info.duracion > tramo_max_seg`: tramos consecutivos `[a, b)` de `tramo_max_seg` (el último puede ser corto;
si el último dura < 5 min se fusiona con el anterior). Un `generate_content` por tramo con `start_offset`/
`end_offset` y el aviso de tramo en el prompt. Los tiempos devueltos se suman a `a`; **heurística**: si el
máximo de los tiempos devueltos supera `(b-a) + 5`, el modelo los dio absolutos → no sumar. Se concatenan
momentos, se suman `Uso`, `tramos=n`, `resumen` = el del primer tramo (o el más largo).
### 4.5 Normalización (pura, sin red; muy testeada)
```python
def extraer_json(texto: str) -> tuple[dict | list | None, bool]
    # quita ```json fences y prosa; acepta objeto o lista; tolera comas finales; si el array está truncado,
    # rescata los objetos completos y devuelve (obj, True).
def parsear_tiempo(valor) -> float | None
    # '00:10','0:10','1:05','01:05.5','01:05,5','00:01:05','1:01:05','65','65.5',65,'1m05s','2 min','1 min 5 seg','5s';
    # negativos → 0; no parseable → None.
def normalizar_momentos(brutos: list[dict], duracion: float, max_momentos: int | None = None,
                        importancia_minima: int = 1, separacion_min: float = config.SEPARACION_MINIMA_SEG,
                        desplazamiento: float = 0.0) -> tuple[list[Momento], list[str]]
    # descarta sin tiempo parseable; acota a [0, duracion-0.5]; recorta titulo/descripcion (config.MAX_*) con "…";
    # importancia a int 1-5 (por defecto 3); fuente válida (por defecto "ambos"); seccion str o None;
    # zona: {"x":0-1000,"y":0-1000} → {"x": x/1000, "y": y/1000} (también acepta ya normalizada 0-1 y
    # {"caja":[ymin,xmin,ymax,xmax]} 0-1000 → {"caja":[x1,y1,x2,y2]} 0-1); inválida → None.
    # ordena cronológicamente; duplicados = a menos de separacion_min Y títulos casi iguales
    # (titulos_casi_iguales: difflib ratio >= SIMILITUD_TITULOS_DUPLICADOS = 0.8) → conserva el de mayor
    # importancia y el aviso dice cuál se fusionó con cuál; dos momentos cercanos con títulos distintos son
    # contenido y se conservan ambos.  Luego filtrar_momentos.  Devuelve avisos (cuántos descartados y por qué).
def titulos_casi_iguales(a: str, b: str, umbral: float = SIMILITUD_TITULOS_DUPLICADOS) -> bool
def filtrar_momentos(momentos: list[Momento], max_momentos: int | None = None,
                     importancia_minima: int = 1) -> tuple[list[Momento], list[str]]
    # filtros del usuario sobre Momento ya normalizados (también al regenerar desde momentos.json): descarta
    # importancia < importancia_minima; si max_momentos: conserva los N más importantes (empate: mayor puntaje
    # local, luego el más temprano) y reordena cronológicamente.
def construir_resultado(datos: dict | list, info: InfoVideo, modelo: str, texto_bruto: str, uso: Uso | None,
                        truncado: bool, modo: str = "gemini", desplazamiento: float = 0.0, **filtros) -> ResultadoAnalisis
```
### 4.6 Batch (50 % más barato; puede tardar horas)
```python
@dataclass
class OpcionesLote:   # thinking=True, con_resolucion=True, esquema=True, reintentos=0, avisos=[]; a_dict()/desde_dict()
    # El batch NO tiene la escalera de fallbacks síncrona (el rechazo llega horas después, en todas las respuestas):
    # registra con qué opciones se construyó el lote para poder reenviarlo sin la rechazada.
class ErrorItemLote(RuntimeError)   # inlined.error de una petición: .codigo, .mensaje
def enviar_lote(cliente, peticiones: list[dict], modelo: str, nombre_lote: str, *, equipo=..., fps=None,
                resolucion: str = config.RESOLUCION_VIDEO, temperatura: float | None = config.TEMPERATURA,
                opciones_lote: OpcionesLote | None = None, log=print) -> "types.BatchJob"
    # peticiones: [{"archivo": types.File, "info": InfoVideo, "tramo": (a, b) | None, "clave"?: str}] →
    #   types.InlinedRequest(contents=..., config=..., metadata={"video": clave (por defecto info.nombre), "tramo": "k",
    #   "inicio": "a", "fin": "b"}).  La clave la pone el pipeline (nombre de la carpeta de salida) para que dos videos
    #   con el mismo nombre base no se mezclen.
    # client.batches.create(model=modelo, src=[...], config=types.CreateBatchJobConfig(display_name=nombre_lote)).
    # Si create responde 400 mencionando thinking / media_resolution / schema, apaga esa opción en opciones_lote y
    # reintenta (máx. MAX_REINTENTOS_LOTE = 2), anotándolo en opciones_lote.avisos.
def opcion_rechazada_en_lote(resultados: dict, opciones: OpcionesLote, modelo: str, resolucion=...) -> str | None
    # tras recoger_lote: si TODAS las respuestas son errores y alguna es un ErrorItemLote 400/INVALID_ARGUMENT que
    # menciona una opción, devuelve el peldaño ("sin_thinking" | "sin_resolucion" | "sin_esquema") para reenviar.
def estado_lote(cliente, nombre_job: str) -> "types.BatchJob"
def lote_terminado(job) -> bool   # SUCCEEDED, PARTIALLY_SUCCEEDED, FAILED, CANCELLED, EXPIRED
def recoger_lote(cliente, job, mapa_videos: dict, precios=None, *, log=print) -> dict[str, ResultadoAnalisis | Exception]
    # job.dest.inlined_responses[i]: mapea por metadata["video"] (y tramo) y por índice como respaldo;
    # `.parsed` es None en batch → json.loads/extraer_json(resp.text); inlined.error → ErrorItemLote para ese video;
    # sin respuestas (FAILED/EXPIRED…) → RuntimeError por video con el estado legible y la indicación de volver a
    # ejecutar --batch; uso con batch=True (costo × config.DESCUENTO_BATCH).  mapa_videos: {clave: {"info": InfoVideo, ...}}.
```
### 4.7 Tests (`tests/test_gemini.py`)
Cliente falso con la misma forma (`files.upload/get/delete`, `models.generate_content`, `batches.create/get`)
que devuelve **objetos reales del SDK** (`types.File`, `types.GenerateContentResponse(candidates=[...],
usage_metadata=types.GenerateContentResponseUsageMetadata(...))`, `types.BatchJob(...)`). Cubre: subida con
PROCESSING→ACTIVE, FAILED, respuesta con fences, truncada + reintento, MAX_TOKENS, escalera completa de
fallbacks (400 thinking → 400 mediaResolution → 404 modelo), MAX_TOKENS sin texto (reintento), tramos con la
heurística de tiempos, batch (mapeo por metadata, error por ítem, reenvío sin la opción rechazada), refinado
(fuente audio solo se completa, zona null, originales, tokens de un fallo), `parsear_tiempo` (todos los ejemplos),
`normalizar_momentos` (duplicados solo con títulos casi iguales, acotado, zona, max_momentos), `extraer_json`
(fences, lista, coma final, truncado),
`estimar_costo` (modelo desconocido → None; batch × 0.5), y que **el prompt no contiene rangos numéricos** de
momentos (regex sobre `PROMPT_SISTEMA + PROMPT_USUARIO`: no debe aparecer "entre \d+ y \d+ momentos" ni
"máximo de \d+ momentos").

## 5. `documentos.py` y `anotar.py`

Parte de `proto_docs.py` (ya verificado: rejillas 1x1/1x2/1x3/2x2, filas EXACTAS en docx, canvas absoluto en
PDF, fuentes, acentos). Cambios respecto del prototipo:
- **Sin tope de páginas.** `calcular_layout(n, por_pagina="auto"|1|2|3|4, ratio=16/9) -> Layout`. "auto": n ≤ 5 → 1;
  6-10 → 2; 11-15 → 3; ≥ 16 → 4 (A4 horizontal 2x2). Páginas = portada + índice (0-n páginas) + ceil(n/mpp).
  `ratio` es el aspecto mediano de las capturas: si es < 1 (**capturas verticales**, iPhone en vertical) "auto" usa
  como máximo 2 por página, lado a lado en A4 horizontal (rejilla 2x1, `Layout.vertical=True`), con la imagen a
  toda la altura que deja el texto; en 1x1 la imagen ocupa toda la altura. En 1x3/2x2 quedarían de 3,7 cm de ancho.
- **Portada**: título del manual (`titulo_video` o nombre del video), nombre del archivo, fecha, duración,
  número de pasos, número de secciones, `resumen` (2-3 frases), pie pequeño "Generado con resumen_videos ·
  modo X · modelo Y".
- **Índice** (si `incluir_indice`): lista por sección: "Sección" en negrita y debajo "N. título …… mm:ss · pág. P".
  **Una sola columna** (también en A4 horizontal), títulos completos (en varias líneas si hace falta; nunca
  recortados) y el número de página real del paso (portada + páginas de índice + ceil(N/mpp); la paginación es
  determinista). Se pagina por medida (`_paginar_indice`) **igual en PDF y docx**: en el docx cada página de índice
  es una cabecera con salto ANTES y una tabla sin bordes de 2 columnas con filas EXACTAS del alto medido, así los
  recuentos de páginas coinciden y `contar_paginas_indice(momentos, por_pagina)` vale para ambos.
- **Celda de paso**: línea pequeña gris "PASO N · SECCIÓN · mm:ss" (sección en mayúsculas, abreviada si no cabe),
  título en negrita y descripción. **Nunca se recorta en silencio** (`_maquetar_celda`): la imagen se reduce desde
  su tamaño de referencia hasta que quepan el título completo (≤ 3 líneas) y la descripción completa (hasta 6 líneas
  en 2x2 y 8 en las demás rejillas, o más si caben bajo la imagen de referencia); si no cabe, se bajan 1 pt las
  fuentes; solo entonces se recorta con "…" (quitando palabras del final, no un 10 % de golpe) y se devuelve un
  aviso "paso N: … se recortó descripción de X a Y caracteres" (por `log` y en `avisos`). Imagen =
  `momento.captura_para_documento`; si None → caja gris "[sin captura]". En el docx el contenido de cada celda deja
  ≥ 4 pt libres en la fila EXACTA (`SEGURIDAD_CELDA_PT`; la marca de párrafo de la línea de la imagen va a 2 pt).
- **Imagen por aspecto** (corrección al prototipo): leer el tamaño con Pillow; si `w/h >= img_w/img_h` fijar
  ancho, si no fijar alto (docx: `add_picture(height=Cm(...))`; PDF: escala mínima). Nunca usar ancho/alto de
  ffprobe.
- **Caracteres de control** (`\x00-\x08\x0b\x0c\x0e-\x1f\x7f`, inválidos en el XML del docx) se eliminan de todos
  los textos antes de escribir (`_limpiar`, `_run`, `_plano`).
- Fuentes PDF (`_fuentes_pdf`): la primera TrueType disponible: `config.CARPETA_FUENTES / "DejaVuSans.ttf"` +
  `DejaVuSans-Bold.ttf` (incluidas; nombres "DejaVu", "DejaVu-Bold"), si no las del sistema (`FUENTES_SISTEMA`:
  Windows `C:/Windows/Fonts/arial.ttf`+`arialbd.ttf` o `segoeui.ttf`+`segoeuib.ttf`; macOS `/Library/Fonts/Arial.ttf`
  + `Arial Bold.ttf` y `/System/Library/Fonts/Supplemental/Arial.ttf`; Linux DejaVu de `/usr/share/fonts`), y solo
  como último recurso Helvetica con cp1252 (aviso por `log` una vez; `≤ ≥ → ← − ≈` se escriben como `<= >= -> <- - ~`
  antes del `encode("cp1252","replace")`). El docx **nunca** sustituye símbolos (Calibri los soporta).
```python
def generar_documentos(nombre_video: str, momentos: list[Momento], carpeta_salida: Path, *, titulo: str | None = None,
                       resumen: str | None = None, fecha: str | None = None, duracion: float | None = None,
                       modo: str = "", modelo: str = "", por_pagina="auto", incluir_indice: bool = True,
                       log=print, avisos: list | None = None) -> tuple[Path, Path, int]
    # escribe <carpeta>/<nombre_video>.docx y .pdf; devuelve (docx, pdf, paginas_pdf) contando con pymupdf
    # (import perezoso; si falla, -1).  momentos vacío → ValueError.  Los recortes de texto (raros) se escriben
    # por `log` ("aviso: paso N: …") y se añaden a `avisos` si se pasa una lista (el pipeline no tiene que hacer nada).
def generar_docx(...), generar_pdf(...)   # mismas entradas (también `avisos`); usables por separado
def contar_paginas_pdf(ruta: Path) -> int
def contar_paginas_indice(momentos: list, por_pagina="auto") -> int   # páginas del índice (pdf y docx)
```
`anotar.py`:
```python
def anotar_captura(ruta_jpg: Path, zona: dict, destino: Path, estilo: str = "circulo", *, log=None) -> Path | None
    # zona {"x","y"} 0-1 → círculo (radio 7 % del ancho, trazo 0.6 % del ancho, mínimo 3 px) rojo-naranja
    # (#FF3B30) con halo blanco; además una flecha corta desde la esquina más lejana al punto (longitud ~18 %
    # del ancho) con punta triangular.  zona {"caja":[x1,y1,x2,y2]} → rectángulo redondeado (mínimo 4 % del ancho,
    # desplazado hacia adentro si queda pegado a un borde o esquina).  Una caja con ambos lados < 0,5 % se anota
    # como punto (su centro); una que cubre > 80 % de la imagen (AREA_MAXIMA_CAJA) no se anota (sería un marco
    # alrededor de toda la captura) → None, con aviso por `log` si se pasa (`pipeline.anotar_momentos` le pasa el
    # log del video, así el aviso queda en log.txt).  Coordenadas inválidas → None sin
    # lanzar.  Guarda JPEG calidad 90.
```
Tests: `tests/test_documentos.py` (n = 1, 3, 8, 13, 20, 47 con capturas falsas → páginas PDF =
1 + índice + ceil(n/mpp); docx recarga con python-docx; `[sin captura]`; captura vertical no desborda:
comprobar que el alto de la imagen ≤ alto de celda vía `inline_shapes`), `tests/test_anotar.py`.

## 6. `local.py` (modo `--local` y modo `--simular`)

```python
def analizar_local(ruta_video: Path, info: InfoVideo, ffmpeg: str, *, whisper_modelo: str | None = config.WHISPER_MODELO,
                   idioma: str = config.WHISPER_IDIOMA, offline: bool = False, umbral_escena: float = config.UMBRAL_ESCENA,
                   separacion_min: float = config.SEPARACION_MINIMA_LOCAL_SEG, max_momentos: int | None = None,
                   importancia_minima: int = 1, log=print) -> ResultadoAnalisis
```
- Escenas: `video.detectar_escenas` (t, score).
- Transcripción: import perezoso de `faster_whisper`; `WhisperModel(modelo, device="cpu", compute_type="int8",
  download_root=os.environ.get("WHISPER_CACHE"), local_files_only=offline)`; `model.transcribe(str(ruta_video),
  language=idioma, vad_filter=True)` (PyAV decodifica el mp4; no hace falta wav). **Todo** dentro de
  `try/except Exception` (ImportError, httpx.ProxyError, LocalEntryNotFoundError, RuntimeError…) → aviso claro
  ("pip install faster-whisper", "descarga el modelo una vez con…") y se sigue solo con escenas.
  `whisper_modelo=None` → no transcribe. Guarda la transcripción en `ResultadoAnalisis.transcripcion`.
- Candidatos = escenas ∪ frases de la transcripción. Puntaje: escena = score/10 (acotado 0-3) + 1; frase =
  0.5 + 0.1·palabras (máx `PUNTOS_LONGITUD_MAX` = 0.9: una frase sin palabra clave ni cambio de plano cerca se
  queda en 1.4 < 1.5 y NO es un momento, por larga que sea) + 1 por cada palabra clave (números con unidad,
  "importante", "cuidado", "nunca", "siempre", "debe", "paso", "primero", "luego", "después", "ajustar",
  "verificar", "colocar", "presionar", "activar", "revisar", "posicionar", "alinear", "medir", "seleccionar",
  "girar", "abrir", "cerrar", "bloquear"; "no" solo como prohibición: "no debe", "no hay que", "no presione",
  "no toque"…, nunca suelto) + 1 si hay cambio de escena a < 5 s. Se conservan los candidatos con puntaje ≥ 1.5
  (sin rango: los que haya), se funden los que estén a < `separacion_min` (gana el mayor puntaje). Título =
  primeras 8 palabras de la frase (capitalizada) o "Cambio de plano k" numerado 1..n sobre los momentos
  DEFINITIVOS (sin huecos por candidatos descartados o fundidos); descripción = la frase (≤ 260) o "Cambio de
  plano a los mm:ss";
  importancia = round(min(5, max(1, puntaje))); fuente = audio/visual/ambos según origen; seccion = "Parte k"
  cada ~duración/5 (mínimo 1 sección); zona None. `modo="local"`, `modelo="scdet+faster-whisper:<m>"` o
  `"scdet"`, `uso=None`. Si no hay ningún candidato: momentos = rejilla uniforme cada max(30 s, duración/12)
  con título "Vista a los mm:ss" e importancia 1, y aviso.

```python
def analizar_simulado(info: InfoVideo, *, log=print) -> ResultadoAnalisis
    # sin API ni whisper: 1 momento cada max(10 s, duración/8) con textos de ejemplo claramente marcados
    # "(SIMULADO)", secciones "Preparación"/"Ejecución"/"Cierre", zona alternando None y puntos; modo="simulado".
```
Tests (`tests/test_local.py`): con `video_prueba` (skip sin ffmpeg): `analizar_local(..., whisper_modelo=None)`
devuelve ≥ 5 momentos cerca de los cortes (12, 23, 35, 46, 58, 69, 80 s ± 1.5); con un `faster_whisper`
falso inyectado por `monkeypatch` en `sys.modules` que devuelva segmentos con frases clave → los momentos
incluyen esas frases; fallo de import → sigue sin transcripción y con aviso; `analizar_simulado`; sin ffmpeg (escenas
inyectadas): 20 min de charla neutra continua → solo los momentos con palabra clave; numeración de planos 1..n.

## 7. `pipeline.py`

```python
@dataclass
class Opciones:   # espejo de la CLI; todos con valores por defecto de config
    carpeta_videos: Path; carpeta_salida: Path; modo: str = "gemini"  # gemini | batch | batch-recoger | local | simulado
    modelo: str = config.MODELO_POR_DEFECTO; fps: float | None = None; api_key: str | None = None
    equipo: str = config.EQUIPO_POR_DEFECTO; redactor: str | None = None
    resolucion: str = config.RESOLUCION_VIDEO; copia_alto: int = config.TRANSCODIFICAR_ALTO; subir_original: bool = False
    refinar: bool = config.REFINAR_CON_CAPTURAS; regenerar: bool = False
    lote_id: str | None = None; esperar_lote: bool = False
    whisper_modelo: str | None = config.WHISPER_MODELO; offline: bool = False
    ffmpeg: str | None = None; ffprobe: str | None = None
    max_subida_mb: int = config.MAX_SUBIDA_MB; timeout_procesado: int = config.TIMEOUT_PROCESADO_SEG
    precio_entrada: float | None = None; precio_salida: float | None = None
    conservar_subida: bool = False; pausa: float = 0.0
    max_momentos: int | None = None; importancia_minima: int = 1
    por_pagina: str | int = "auto"; incluir_indice: bool = True; anotar: bool = True
    tramo_min: int = config.TRAMO_MAX_MIN; forzar: bool = False; solo: list[str] | None = None; verbose: bool = False
    temperatura: float | None = config.TEMPERATURA   # --temperatura; None = no se envía

@dataclass
class ResumenEjecucion: resultados: list[ResultadoVideo]; uso_total: Uso | None; segundos: float; uso_acumulado: Uso | None = None
    # uso_total = lo pagado a la API EN ESTA ejecución (sumar_uso: ResultadoVideo.uso_ejecucion de los OK no omitidos);
    # uso_acumulado = lo pagado por los videos listados en todas las ejecuciones (sumar_acumulado: uso_acumulado).
    # métodos: exitosos(), fallidos(), tabla() -> str (texto alineado para consola)

def procesar_carpeta(op: Opciones, *, log=print) -> ResumenEjecucion
def procesar_video(ruta: Path, op: Opciones, cliente=None, *, log=print) -> ResultadoVideo
def preparar_carpeta_salida(info: InfoVideo, op: Opciones) -> Path   # salida/<nombre>/ ; colisión de nombre con
    # ruta distinta → sufijo -<6 hex del sha1 de la ruta absoluta>
def guardar_json(carpeta: Path, info: InfoVideo, analisis: ResultadoAnalisis, extra: dict | None = None) -> Path
def cargar_json(carpeta: Path) -> tuple[ResultadoAnalisis | None, dict]   # (análisis con los momentos_descartados
    # reincorporados en orden cronológico, "documentos"); (None, {}) si no se puede leer
def capturar_momentos(info: InfoVideo, momentos: list[Momento], carpeta: Path, ffmpeg: str, *, anotar=True, log=print) -> None
    # capturas/NN_mm-ss.jpg (NN de 2-3 dígitos según n); rellena ruta_captura/tiempo_real_seg; si zona y anotar:
    # capturas/NN_mm-ss_anotada.jpg → ruta_captura_anotada.
```
Flujo de `procesar_video`:
1. `obtener_info`; carpeta de salida; estado previo (`_estado_salida`): sin `momentos.json` → analizar; JSON **y**
   documentos (.docx y .pdf registrados existen, sin `error.txt`) y no `forzar` → "ya procesado" (`exito=True`,
   `omitido=True`, `analisis` cargado del JSON, log "ya procesado, use --forzar / --regenerar"); JSON **sin**
   documentos (una ejecución anterior falló a medias) y no `forzar` → NO es "ya procesado": se **retoma** desde el
   JSON sin API (como `--regenerar`, aviso "Retomado desde momentos.json"; `--forzar` repite el análisis).
   `--forzar` y `--regenerar` juntos → `ValueError` en `procesar_carpeta` (la CLI lo muestra como error de configuración).
2. `log.txt` en la carpeta del video: cada línea con hora `HH:MM:SS`. La función `log` que reciben los módulos
   escribe en consola **y** en ese archivo.
3. Análisis según `op.modo`: gemini → (transcodificar si `necesita_transcodificar`) → `subir_video` →
   `analizar_video` (con `tramo_max_seg`, `precios` de la CLI o config) → `eliminar_archivo` (salvo
   `conservar_subida`) en `finally`. local → `analizar_local`. simulado → `analizar_simulado`.
4. **Guardar `momentos.json` inmediatamente** tras el análisis (antes de capturas: no perder un resultado
   pagado). Estructura: `{"video": info.a_dict(), "generado": iso, "version": __version__, "analisis":
   analisis.a_dict(base=carpeta), "documentos": {...}}`.
5. `capturar_momentos` (sin anotar todavía) → si hay cliente: `gemini.refinar_con_capturas` (si `op.refinar`) y
   después `gemini.pulir_redaccion` (si `--redactor`; el redactor va DESPUÉS del refinado para que este no deshaga su
   trabajo) → guardar JSON de nuevo → anotaciones (`anotar.anotar_captura` para los momentos con zona) →
   `generar_documentos` → actualizar JSON con rutas docx/pdf y páginas.
   El JSON lleva además `uso_acumulado` (todo lo pagado por el video: lo registrado antes + lo de esta ejecución;
   con `--forzar` se suma al de la ejecución anterior) y `ResultadoVideo` distingue `uso_ejecucion` (solo si se llamó
   a la API ahora) de `uso_acumulado`.
   `--regenerar`: exige `momentos.json` (si no existe → error "ejecute sin --regenerar") y **NUNCA llama a la API**
   (`_cliente_para` devuelve None: ni análisis, ni refinado, ni redactor, aunque haya clave): se carga el JSON tal cual
   (`cargar_json`; respeta las correcciones a mano) y se rehacen capturas, anotaciones y documentos. Con `--regenerar`
   sí aplican `--max-momentos` e `--importancia-minima` (`gemini.filtrar_momentos`); los momentos apartados quedan en
   `analisis.descartados` → `momentos_descartados` del JSON, de donde `cargar_json` los recupera (regenerar sin filtros
   devuelve el análisis completo; nunca se recorta en silencio).
6. Cualquier excepción: `error.txt` con traza y mensaje amable, `exito=False`, se sigue con el siguiente video.
   Errores de API (`errors.APIError`): mensaje con `code`/`status`/`message`.
Modo **batch**: `procesar_carpeta` sube todos (con transcodificación si toca; los ya completos se omiten y los
incompletos se retoman desde su JSON sin API), prepara peticiones (con tramos) con `clave` = nombre de la carpeta de
salida (dos videos con el mismo nombre base, `IMG_0001.mp4` e `IMG_0001.mov`, van a carpetas y claves distintas:
`_carpeta_lote`), `enviar_lote` con un `OpcionesLote`, escribe `salida/_lotes/<id_corto>.json` (nombre del job,
modelo, `enviado`, opciones, `escalera` = nota de que el batch no tiene escalera + opciones/reintentos/avisos, y por
video: `nombre` (clave), ruta, `archivo.name`, duración, tramos, carpeta, info) y `momentos.json` NO se escribe aún.
Si Ctrl+C o un fallo interrumpe las subidas o la creación del lote, se borran los archivos ya subidos (salvo
`--conservar-subida`). Imprime cómo recoger: `python resumir_videos.py --batch-recoger <id_corto> [--esperar]`.
`batch-recoger`: lee el JSON del lote, `estado_lote`; si no terminó y no `esperar` → informa y sale; si `esperar` →
sondea cada `config.INTERVALO_SONDEO_LOTE_SEG`; FAILED/CANCELLED/EXPIRED → aviso claro (los videos sin
`momentos.json` se reenvían al volver a ejecutar `--batch`); al terminar `recoger_lote` (con `--max-momentos` /
`--importancia-minima` del envío si la CLI no los indica). Si `opcion_rechazada_en_lote` detecta que TODAS las
respuestas son un 400 de configuración, el lote se **reenvía automáticamente** sin esa opción (`_reenviar_lote`:
mismos archivos remotos vía `obtener_archivo`, nuevo `_lotes/<id>.json` con `reenvio_de`, el viejo queda
`recogido` con `reenviado_como`; máximo `MAX_REINTENTOS_LOTE` = 2). Si no, para cada video: si ya tiene `momentos.json`
generado DESPUÉS del envío del lote y no `forzar` → se omite (completo) o se retoma sin API (incompleto): recoger dos
veces no repite el refinado ni pisa correcciones a mano; si no → continúa desde el paso 4 (captura + refinado +
documentos). Los archivos remotos se borran en la primera recogida (salvo `--conservar-subida`).
Al final: `ResumenEjecucion.tabla()` con columnas Video | Momentos | Págs | Tokens | Costo est. | Acumulado | Estado
(Tokens y Costo est. = ESTA ejecución; Acumulado = todo lo pagado por el video según su JSON, también en omitidos y
regenerados), y línea "ESTIMACIÓN de costo de esta ejecución: US$ x.xxxx (N tokens); acumulado de estos videos en
todas las ejecuciones: US$ y.yyyy (M tokens) (precios de config, verificar en ai.google.dev)". Sin API en esta
ejecución (local, simulado, `--regenerar`, omitidos): "US$ 0.0000 (sin API)".

`resumir_videos.py` (argparse, `description` en español, ejemplos en `epilog`):
```
python resumir_videos.py [CARPETA] [--salida DIR] [--modelo M] [--equipo TEXTO] [--redactor M] [--fps F]
   [--resolucion baja|media|alta] [--copia-alto 480|720|1080] [--subir-original] [--sin-refinado] [--regenerar]
   [--batch] [--batch-recoger ID] [--batch-pendiente] [--esperar]
   [--local] [--simular] [--whisper-modelo M] [--sin-whisper] [--offline] [--ffmpeg RUTA] [--ffprobe RUTA]
   [--sin-tonemap] [--max-subida-mb N] [--timeout-procesado S] [--precio-entrada X] [--precio-salida Y]
   [--conservar-subida] [--pausa S] [--temperatura T] [--max-momentos N] [--importancia-minima 1-5] [--por-pagina auto|1|2|3|4]
   [--sin-indice] [--sin-anotaciones] [--tramo-min N] [--forzar] [--solo NOMBRE [--solo NOMBRE ...]] [--verbose]
   [--version]
```
Reglas de argparse para que la carpeta posicional nunca quede "tragada" por una opción: `--solo` es
`action="append"` (un nombre por uso, repetible), `--batch-recoger` exige el ID (lo imprime `--batch`) y
`--batch-pendiente` (bandera, excluyente con los demás modos) recoge el único lote pendiente. `--copia-alto` es el
**lado menor** de la copia ligera. `--sin-tonemap` pone `video.TONEMAP_HDR = False`. `.env`: ver §2.
Comprobaciones previas: si un valor de `--solo` o el ID de `--batch-recoger` es una carpeta existente → error de
configuración explicando que la carpeta va como primer argumento; carpeta existe y tiene videos (si no, mensaje
claro); ffmpeg localizable (se imprime ruta y versión, y el ffprobe encontrado junto a él:
`localizar_ffprobe(op.ffprobe, ffmpeg=ffmpeg)`); en modo gemini/batch, clave presente (si no: mensaje explicando
`.env`, y sugerencia de `--local`/`--simular`; con `--regenerar` solo aviso: no usa la API); en `--local` sin
faster-whisper instalado: aviso, no error. Los mensajes de consola usan solo caracteres de cp1252/cp850 (`->`, no
`→`) para leerse bien al redirigir la salida en PowerShell 5.1/cmd. Código de salida 0 si todos OK, 1 si alguno
falló, 2 si error de configuración.

Tests (`tests/test_pipeline.py`): con `video_prueba`: `procesar_carpeta` en modo simulado → existen
`momentos.json`, `capturas/*.jpg` (≥ 1 anotada), `.docx`, `.pdf`, páginas = esperado; modo gemini con un
cliente falso inyectado (monkeypatch de `gemini.crear_cliente`) que devuelve una respuesta JSON de 5
momentos con zona → capturas anotadas y JSON con uso/costo; orden refinado → redactor; `--regenerar` sin ninguna
llamada a la API (también tras un análisis `--local`) y con filtros sin perder momentos; fallo tras el JSON →
retomar; costo de esta ejecución vs acumulado (`--forzar` acumula); un video corrupto (archivo de texto .mp4) en la
misma carpeta → `error.txt` y el otro video sí se procesa; `--forzar` / salto de ya procesado; batch: recogida
repetida idempotente, nombres repetidos, lote expirado, Ctrl+C durante las subidas, reenvío por opción rechazada;
CLI `--version` y `--help` vía `subprocess`. Los tests de la CLI ponen `GEMINI_API_KEY=""`/`GOOGLE_API_KEY=""` en
el entorno del subproceso (no basta quitarlas: `load_dotenv` no pisa variables presentes, pero sí rellena las
ausentes con el `.env` del usuario y el test llamaría a la API real).

## 8. `README.md` (corto, en español, para Windows principalmente)

Secciones: Qué hace (con el principio "la cantidad de capturas la decide el contenido") · Requisitos
(Python 3.10+, ffmpeg incluido vía imageio-ffmpeg y orden de búsqueda `--ffmpeg` → `FFMPEG_BIN` → incluido →
PATH; ffprobe opcional, se busca junto a ffmpeg; fuentes DejaVu incluidas y opcionales) · Instalación con doble
clic (`instalar.bat`/`instalar.ps1` y `resumir.bat` en Windows; `instalar.command`/`resumir.command` en macOS;
`GUIA_RAPIDA.md`) e instalación manual (venv, `pip install -r requirements.txt`, `.env` con `GEMINI_API_KEY`,
plan de pago y por qué) · Uso básico (`python resumir_videos.py` procesa `videos/` → `salida/`; `--solo` repetible;
videos verticales admitidos) · Qué sale en `salida/<video>/` y cómo se maquetan los documentos (texto íntegro,
índice con página, capturas verticales 2 por página, avisos en `log.txt`) · Opciones principales (tabla, debe
coincidir con `--help`) · `--regenerar` (nunca usa la API) · Modo batch (50 %; `--batch-recoger ID`,
`--batch-pendiente`) · Modo local (privacidad; instalar `faster-whisper`; descargar modelo una vez; limitaciones) ·
Costos (cifras **orientativas, no verificadas**: ~100-300 tokens/s; verificar precios) · Privacidad (plan pagado;
revisar qué permite el INC si aparecen pacientes) · Probar sin gastar (`tools/crear_video_prueba.py` + `--simular`)
· Solución de problemas (clave inválida, modelo no disponible, ffmpeg, HDR y `--sin-tonemap`, fuentes, acentos al
redirigir la consola, Whisper sin internet, abrir un docx en Word para comprobar) · Mejoras posibles.
`requirements.txt`: google-genai>=2.25, python-docx>=1.1, reportlab>=4, python-dotenv, pillow, numpy,
pymupdf, imageio-ffmpeg, av. `requirements-local.txt`: faster-whisper. `.env.ejemplo`. `.gitignore`:
salida/, videos/* (menos .gitkeep), .env, __pycache__, *.pyc, .pytest_cache.
