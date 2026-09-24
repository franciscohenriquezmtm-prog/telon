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
- Nunca se imprime ni se guarda la clave de API. `.env` se carga con `python-dotenv`
  (`load_dotenv(find_dotenv(usecwd=True))` y además el `.env` junto a `resumir_videos.py`).
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
    # orden: argumento → env FFMPEG_BIN → shutil.which("ffmpeg") → imageio_ffmpeg.get_ffmpeg_exe()
    # (import perezoso) → RuntimeError con instrucciones claras.
def localizar_ffprobe(ruta: str | None = None) -> str | None
    # argumento → env FFPROBE_BIN → junto al ffmpeg encontrado → shutil.which → None (no es obligatorio)
def sanear_nombre(nombre: str) -> str
    # letras/dígitos/áéíóúüñ/espacio/-/_ ; colapsa espacios; quita puntos/espacios finales; máx 80 chars;
    # "" -> "video"
def obtener_info(ruta: Path, ffmpeg: str, ffprobe: str | None = None) -> InfoVideo
    # ffprobe → regex sobre stderr de `ffmpeg -hide_banner -i` (exit 1 es normal) → PyAV (import perezoso).
    # nombre = sanear_nombre(ruta.stem).  RuntimeError si no se obtiene la duración.
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
    # `-vf "scale=-2:ALTO,fps=FPS" -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -c:a aac -b:a 48k -ac 1
    #  -movflags +faststart`.  Conserva los tiempos (no corta nada).
def necesita_transcodificar(info: InfoVideo, umbral_mb: int = config.UMBRAL_TRANSCODIFICAR_MB) -> str | None
    # devuelve el motivo ("extensión .mov: se sube una copia ligera", "tamaño 2300 MB > 300 MB") o None.
    # Regla: extensión fuera de config.EXTENSIONES_SUBIDA_DIRECTA, o tamaño > umbral_mb.  Estrategia iPhone:
    # el .MOV HEVC de varios GB nunca se sube; se sube la copia 480p/2 fps con audio mono 64 kbps (decenas de MB)
    # y las capturas se sacan del original en alta calidad.
def es_hdr(info_extra: dict) -> bool
    # obtener_info debe guardar en InfoVideo.extra (añade el campo `extra: dict = field(default_factory=dict)` a
    # InfoVideo en modelos.py) color_transfer/pix_fmt/codec cuando ffprobe está disponible.  HDR = color_transfer en
    # {"smpte2084", "arib-std-b67"}.  Si el video es HDR y el ffmpeg tiene los filtros zscale+tonemap (comprobar con
    # `ffmpeg -hide_banner -filters` una vez y cachear), las capturas usan
    # "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    # antes de scale; si no, se extraen tal cual (pueden verse lavadas; log de aviso).  La transcodificación para
    # subida aplica el mismo tonemap si está disponible.  Los .MOV de iPhone con rotación se auto-rotan (ffmpeg).
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
                intervalo: int = config.INTERVALO_SONDEO_ARCHIVO_SEG, *, log=print) -> "types.File"
    # mime explícito de config.MIME_SUBIDA (ValueError si no está); UploadFileConfig(mime_type, display_name);
    # sondea mientras state == PROCESSING; FAILED -> RuntimeError(f.error.message); timeout -> RuntimeError.
def eliminar_archivo(cliente, archivo, *, log=print) -> None    # nunca lanza (solo log)
```
### 4.3 Análisis de un video (síncrono)
```python
def analizar_video(cliente, archivo, info: InfoVideo, modelo: str = config.MODELO_POR_DEFECTO,
                   fps: float | None = None, tramo_max_seg: float = config.TRAMO_MAX_MIN * 60,
                   precios: dict | None = None, equipo: str = config.EQUIPO_POR_DEFECTO, *, log=print) -> ResultadoAnalisis
```
- Construye `contents=[types.Content(role="user", parts=[types.Part(file_data=types.FileData(file_uri=archivo.uri,
  mime_type=archivo.mime_type), video_metadata=<opcional>), types.Part.from_text(text=PROMPT_USUARIO...)])]`.
  `video_metadata=types.VideoMetadata(fps=fps, start_offset="Ns", end_offset="Ms")` solo cuando hay fps o tramo.
- `config=types.GenerateContentConfig(system_instruction=PROMPT_SISTEMA, temperature=config.TEMPERATURA,
  max_output_tokens=config.MAX_TOKENS_SALIDA, response_mime_type="application/json",
  response_json_schema=<copia de ESQUEMA_RESPUESTA>, media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
  thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW))`.
- **Escalera de fallbacks** (una función `_generar_con_fallbacks` que prueba en orden y registra cada paso en
  `avisos`): (1) tal cual; (2) `ClientError` 400 cuyo mensaje mencione `thinking` → sin `thinking_config`;
  (3) 400 que mencione `mediaResolution`/`media_resolution` → sin `media_resolution`; (4) 400 que mencione
  `responseJsonSchema`/`schema` → sin esquema (dejando `response_mime_type` y añadiendo "Responde SOLO con el
  JSON" al prompt); (5) `ClientError` 404/NOT_FOUND o mensaje "not found"/"not supported" con el modelo →
  siguiente modelo de `config.MODELOS_ALTERNATIVOS` (repitiendo 1-4). Otras excepciones se propagan.
- Respuesta: `resp.text` None → RuntimeError con `prompt_feedback.block_reason` / `finish_reason`. Parseo:
  `resp.parsed` si es dict; si no, `extraer_json(resp.text)`. Si `finish_reason == MAX_TOKENS` o el JSON
  quedó truncado → **un** reintento con `max_output_tokens*2`; si sigue truncado, conserva lo rescatado y
  `truncado=True`.
- Uso: `uso_desde_respuesta(resp, modelo, batch=False) -> Uso` (usage_metadata puede ser None → ceros;
  `prompt_tokens_details` → `detalle_entrada` por `modality.value`); `estimar_costo(uso, precios, batch)`.
### 4.3b Redactor opcional (segunda pasada solo de texto)
```python
def pulir_redaccion(cliente, resultado: ResultadoAnalisis, modelo_redactor: str, precios=None, *, log=print) -> ResultadoAnalisis
```
Envía **solo texto** (el JSON de momentos, sin video: barato) a `modelo_redactor` con la instrucción: "Eres el
redactor de un manual clínico. Mejora título y descripción de cada paso para que sean claros, precisos y
homogéneos (imperativo, estilo protocolo), corrige la agrupación en secciones si es incoherente y redacta el
resumen. NO cambies tiempos, NO añadas ni quites pasos, NO inventes datos que no estén en el borrador. Devuelve el
mismo JSON". Mismo esquema, mismo parseo; si falla (cualquier excepción) devuelve el resultado original con un
aviso. Suma el `Uso` (mismo `Uso` con `modelo` = "a+b"). Solo se usa si `--redactor` está presente.

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
    # ordena cronológicamente; duplicados (< separacion_min) → conserva el de mayor importancia;
    # filtra importancia_minima; si max_momentos: conserva los N más importantes (empate: más temprano) y
    # reordena cronológicamente.  Devuelve avisos (cuántos descartados y por qué).
def construir_resultado(datos: dict | list, info: InfoVideo, modelo: str, texto_bruto: str, uso: Uso | None,
                        truncado: bool, modo: str = "gemini", desplazamiento: float = 0.0, **filtros) -> ResultadoAnalisis
```
### 4.6 Batch (50 % más barato; puede tardar horas)
```python
def enviar_lote(cliente, peticiones: list[dict], modelo: str, nombre_lote: str, *, log=print) -> "types.BatchJob"
    # peticiones: [{"archivo": types.File, "info": InfoVideo, "tramo": (a, b) | None}] → types.InlinedRequest(
    #   contents=..., config=..., metadata={"video": info.nombre, "tramo": "k", "inicio": "a", "fin": "b"}).
    # client.batches.create(model=modelo, src=[...], config=types.CreateBatchJobConfig(display_name=nombre_lote)).
def estado_lote(cliente, nombre_job: str) -> "types.BatchJob"
def lote_terminado(job) -> bool   # SUCCEEDED, PARTIALLY_SUCCEEDED, FAILED, CANCELLED, EXPIRED
def recoger_lote(cliente, job, mapa_videos: dict, precios=None, *, log=print) -> dict[str, ResultadoAnalisis | Exception]
    # job.dest.inlined_responses[i]: mapea por metadata["video"] (y tramo) y por índice como respaldo;
    # `.parsed` es None en batch → json.loads/extraer_json(resp.text); inlined.error → Exception para ese video;
    # uso con batch=True (costo × config.DESCUENTO_BATCH).  mapa_videos: {nombre: {"info": InfoVideo, ...}}.
```
### 4.7 Tests (`tests/test_gemini.py`)
Cliente falso con la misma forma (`files.upload/get/delete`, `models.generate_content`, `batches.create/get`)
que devuelve **objetos reales del SDK** (`types.File`, `types.GenerateContentResponse(candidates=[...],
usage_metadata=types.GenerateContentResponseUsageMetadata(...))`, `types.BatchJob(...)`). Cubre: subida con
PROCESSING→ACTIVE, FAILED, respuesta con fences, truncada + reintento, MAX_TOKENS, escalera completa de
fallbacks (400 thinking → 400 mediaResolution → 404 modelo), tramos con la heurística de tiempos, batch
(mapeo por metadata, error por ítem), `parsear_tiempo` (todos los ejemplos), `normalizar_momentos`
(duplicados, acotado, zona, max_momentos), `extraer_json` (fences, lista, coma final, truncado),
`estimar_costo` (modelo desconocido → None; batch × 0.5), y que **el prompt no contiene rangos numéricos** de
momentos (regex sobre `PROMPT_SISTEMA + PROMPT_USUARIO`: no debe aparecer "entre \d+ y \d+ momentos" ni
"máximo de \d+ momentos").

## 5. `documentos.py` y `anotar.py`

Parte de `proto_docs.py` (ya verificado: rejillas 1x1/1x2/1x3/2x2, filas EXACTAS en docx, canvas absoluto en
PDF, fuentes, acentos). Cambios respecto del prototipo:
- **Sin tope de páginas.** `calcular_layout(n, por_pagina="auto"|1|2|3|4) -> Layout`. "auto": n ≤ 5 → 1;
  6-10 → 2; 11-15 → 3; ≥ 16 → 4 (A4 horizontal 2x2). Páginas = portada + índice (0-n páginas) + ceil(n/mpp).
- **Portada**: título del manual (`titulo_video` o nombre del video), nombre del archivo, fecha, duración,
  número de pasos, número de secciones, `resumen` (2-3 frases), pie pequeño "Generado con resumen_videos ·
  modo X · modelo Y".
- **Índice** (si `incluir_indice`): lista por sección: "Sección" en negrita y debajo "N. título …… mm:ss".
  Puede ocupar más de una página si hay muchos pasos (en PDF se pagina por medida; en docx fluye solo).
- **Celda de paso**: línea pequeña gris "PASO N · SECCIÓN · mm:ss" (sección en mayúsculas, recortada), título
  en negrita, descripción (hasta 3 líneas en 2x2, 2-4 en las otras rejillas según el alto disponible; recorte
  con "…"). Imagen = `momento.captura_para_documento`; si None → caja gris "[sin captura]".
- **Imagen por aspecto** (corrección al prototipo): leer el tamaño con Pillow; si `w/h >= img_w/img_h` fijar
  ancho, si no fijar alto (docx: `add_picture(height=Cm(...))`; PDF: escala mínima). Nunca usar ancho/alto de
  ffprobe.
- Fuentes PDF: registrar `config.CARPETA_FUENTES / "DejaVuSans.ttf"` y `DejaVuSans-Bold.ttf` si existen
  (nombres "DejaVu", "DejaVu-Bold"); si no, Helvetica con `encode("cp1252","replace")`.
```python
def generar_documentos(nombre_video: str, momentos: list[Momento], carpeta_salida: Path, *, titulo: str | None = None,
                       resumen: str | None = None, fecha: str | None = None, duracion: float | None = None,
                       modo: str = "", modelo: str = "", por_pagina="auto", incluir_indice: bool = True,
                       log=print) -> tuple[Path, Path, int]
    # escribe <carpeta>/<nombre_video>.docx y .pdf; devuelve (docx, pdf, paginas_pdf) contando con pymupdf
    # (import perezoso; si falla, -1).  momentos vacío → ValueError.
def generar_docx(...), generar_pdf(...)   # mismas entradas; usables por separado
def contar_paginas_pdf(ruta: Path) -> int
```
`anotar.py`:
```python
def anotar_captura(ruta_jpg: Path, zona: dict, destino: Path, estilo: str = "circulo") -> Path | None
    # zona {"x","y"} 0-1 → círculo (radio 7 % del ancho, trazo 0.6 % del ancho, mínimo 3 px) rojo-naranja
    # (#FF3B30) con halo blanco; además una flecha corta desde la esquina más lejana al punto (longitud ~18 %
    # del ancho) con punta triangular.  zona {"caja":[x1,y1,x2,y2]} → rectángulo redondeado.  Coordenadas
    # inválidas → None sin lanzar.  Guarda JPEG calidad 90.
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
  0.5 + 0.1·palabras (máx 2) + 1 por cada palabra clave (números con unidad, "importante", "cuidado", "nunca",
  "siempre", "debe", "no", "paso", "primero", "luego", "después", "ajustar", "verificar", "colocar", "presionar",
  "activar", "revisar", "posicionar", "alinear", "medir", "seleccionar", "girar", "abrir", "cerrar", "bloquear")
  + 1 si hay cambio de escena a < 5 s. Se conservan los candidatos con puntaje ≥ 1.5 (sin rango: los que haya),
  se funden los que estén a < `separacion_min` (gana el mayor puntaje). Título = primeras 8 palabras de la
  frase (capitalizada) o "Cambio de plano N"; descripción = la frase (≤ 260) o "Cambio de plano a los mm:ss";
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
incluyen esas frases; fallo de import → sigue sin transcripción y con aviso; `analizar_simulado`.

## 7. `pipeline.py`

```python
@dataclass
class Opciones:   # espejo de la CLI; todos con valores por defecto de config
    carpeta_videos: Path; carpeta_salida: Path; modo: str = "gemini"  # gemini | batch | batch-recoger | local | simulado
    modelo: str = config.MODELO_POR_DEFECTO; fps: float | None = None; api_key: str | None = None
    equipo: str = config.EQUIPO_POR_DEFECTO; redactor: str | None = None
    lote_id: str | None = None; esperar_lote: bool = False
    whisper_modelo: str | None = config.WHISPER_MODELO; offline: bool = False
    ffmpeg: str | None = None; ffprobe: str | None = None
    max_subida_mb: int = config.MAX_SUBIDA_MB; timeout_procesado: int = config.TIMEOUT_PROCESADO_SEG
    precio_entrada: float | None = None; precio_salida: float | None = None
    conservar_subida: bool = False; pausa: float = 0.0
    max_momentos: int | None = None; importancia_minima: int = 1
    por_pagina: str | int = "auto"; incluir_indice: bool = True; anotar: bool = True
    tramo_min: int = config.TRAMO_MAX_MIN; forzar: bool = False; solo: list[str] | None = None; verbose: bool = False

@dataclass
class ResumenEjecucion: resultados: list[ResultadoVideo]; uso_total: Uso | None; segundos: float
    # métodos: exitosos(), fallidos(), tabla() -> str (texto alineado para consola)

def procesar_carpeta(op: Opciones, *, log=print) -> ResumenEjecucion
def procesar_video(ruta: Path, op: Opciones, cliente=None, *, log=print) -> ResultadoVideo
def preparar_carpeta_salida(info: InfoVideo, op: Opciones) -> Path   # salida/<nombre>/ ; colisión de nombre con
    # ruta distinta → sufijo -<6 hex del sha1 de la ruta absoluta>
def guardar_json(carpeta: Path, info: InfoVideo, analisis: ResultadoAnalisis, extra: dict | None = None) -> Path
def capturar_momentos(info: InfoVideo, momentos: list[Momento], carpeta: Path, ffmpeg: str, *, anotar=True, log=print) -> None
    # capturas/NN_mm-ss.jpg (NN de 2-3 dígitos según n); rellena ruta_captura/tiempo_real_seg; si zona y anotar:
    # capturas/NN_mm-ss_anotada.jpg → ruta_captura_anotada.
```
Flujo de `procesar_video`:
1. `obtener_info`; carpeta de salida; si existe `momentos.json` y no `forzar` → salta con aviso (devuelve
   `exito=True` con `error="ya procesado"`? No: `exito=True`, `analisis` cargado del JSON si es posible, log
   "ya procesado, use --forzar").
2. `log.txt` en la carpeta del video: cada línea con hora `HH:MM:SS`. La función `log` que reciben los módulos
   escribe en consola **y** en ese archivo.
3. Análisis según `op.modo`: gemini → (transcodificar si `necesita_transcodificar`) → `subir_video` →
   `analizar_video` (con `tramo_max_seg`, `precios` de la CLI o config) → `eliminar_archivo` (salvo
   `conservar_subida`) en `finally`. local → `analizar_local`. simulado → `analizar_simulado`.
4. **Guardar `momentos.json` inmediatamente** tras el análisis (antes de capturas: no perder un resultado
   pagado). Estructura: `{"video": info.a_dict(), "generado": iso, "version": __version__, "analisis":
   analisis.a_dict(base=carpeta), "documentos": {...}}`.
5. `capturar_momentos` → `generar_documentos` → actualizar JSON con rutas docx/pdf y páginas.
6. Cualquier excepción: `error.txt` con traza y mensaje amable, `exito=False`, se sigue con el siguiente video.
   Errores de API (`errors.APIError`): mensaje con `code`/`status`/`message`.
Modo **batch**: `procesar_carpeta` sube todos (con transcodificación si toca), prepara peticiones (con tramos),
`enviar_lote`, escribe `salida/_lotes/<id_corto>.json` (nombre del job, modelo, y por video: ruta, nombre,
`archivo.name`, duración, tramos) y `momentos.json` NO se escribe aún. Imprime cómo recoger:
`python resumir_videos.py --batch-recoger <id_corto> [--esperar]`. `batch-recoger`: lee el JSON del lote,
`estado_lote`; si no terminó y no `esperar` → informa y sale; si `esperar` → sondea cada
`config.INTERVALO_SONDEO_LOTE_SEG`; al terminar `recoger_lote` → para cada video continúa desde el paso 4
(captura + documentos) y borra los archivos remotos.
Al final: `ResumenEjecucion.tabla()` con columnas Video | Momentos | Págs | Tokens | Costo est. | Estado, y
línea "ESTIMACIÓN de costo total: US$ x.xxxx (precios de config, verificar en ai.google.dev)". En modo local
o simulado, costo 0 y "sin API".

`resumir_videos.py` (argparse, `description` en español, ejemplos en `epilog`):
```
python resumir_videos.py [CARPETA] [--salida DIR] [--modelo M] [--equipo TEXTO] [--redactor M] [--fps F] [--batch] [--batch-recoger ID] [--esperar]
   [--local] [--simular] [--whisper-modelo M] [--sin-whisper] [--offline] [--ffmpeg RUTA] [--ffprobe RUTA]
   [--max-subida-mb N] [--timeout-procesado S] [--precio-entrada X] [--precio-salida Y] [--conservar-subida]
   [--pausa S] [--max-momentos N] [--importancia-minima 1-5] [--por-pagina auto|1|2|3|4] [--sin-indice]
   [--sin-anotaciones] [--tramo-min N] [--forzar] [--solo NOMBRE ...] [--verbose] [--version]
```
Comprobaciones previas: carpeta existe y tiene videos (si no, mensaje claro); ffmpeg localizable; en modo
gemini/batch, clave presente (si no: mensaje explicando `.env`, y sugerencia de `--local`/`--simular`); en
`--local` sin faster-whisper instalado: aviso, no error. Código de salida 0 si todos OK, 1 si alguno falló,
2 si error de configuración.

Tests (`tests/test_pipeline.py`): con `video_prueba`: `procesar_carpeta` en modo simulado → existen
`momentos.json`, `capturas/*.jpg` (≥ 1 anotada), `.docx`, `.pdf`, páginas = esperado; modo gemini con un
cliente falso inyectado (monkeypatch de `gemini.crear_cliente`) que devuelve una respuesta JSON de 5
momentos con zona → capturas anotadas y JSON con uso/costo; un video corrupto (archivo de texto .mp4) en la
misma carpeta → `error.txt` y el otro video sí se procesa; `--forzar` / salto de ya procesado; CLI
`--version` y `--help` vía `subprocess`.

## 8. `README.md` (corto, en español, para Windows principalmente)

Secciones: Qué hace (con el principio "la cantidad de capturas la decide el contenido") · Requisitos
(Python 3.10+, ffmpeg incluido vía imageio-ffmpeg; ffprobe opcional) · Instalación (venv, `pip install -r
requirements.txt`, `.env` con `GEMINI_API_KEY`, plan de pago y por qué) · Uso básico (`python
resumir_videos.py` procesa `videos/` → `salida/`) · Qué sale en `salida/<video>/` · Opciones principales
(tabla) · Modo batch (50 %) · Modo local (privacidad; instalar `faster-whisper`; descargar modelo una vez;
limitaciones) · Costos (tabla de la conversación: ~100-300 tokens/s; 30 min ≈ 0,05-0,15 US$; verificar
precios) · Privacidad (plan pagado; revisar qué permite el INC si aparecen pacientes) · Probar sin gastar
(`tools/crear_video_prueba.py` + `--simular`) · Solución de problemas (clave inválida, modelo no
disponible, ffmpeg, Whisper sin internet, abrir un docx en Word para comprobar) · Mejoras posibles.
`requirements.txt`: google-genai>=2.25, python-docx>=1.1, reportlab>=4, python-dotenv, pillow, numpy,
pymupdf, imageio-ffmpeg, av. `requirements-local.txt`: faster-whisper. `.env.ejemplo`. `.gitignore`:
salida/, videos/* (menos .gitkeep), .env, __pycache__, *.pyc, .pytest_cache.
