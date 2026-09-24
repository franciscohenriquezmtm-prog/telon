# resumen_videos

Convierte videos de capacitación (por ejemplo, un colega explicando cómo se usa un
arco en C grabado con el iPhone) en un **manual sencillo con capturas**: un `.docx`
editable y un `.pdf` por video, con pasos numerados agrupados por sección, una
captura nítida por paso y, cuando la persona señala algo, un círculo con flecha
sobre la imagen.

**La cantidad de capturas la decide el contenido.** No hay un rango fijo: si en 10
minutos se enseñan 30 cosas, salen 30 pasos; si solo se dicen 3 cosas importantes,
salen 3. Cuenta tanto lo que se **ve** como lo que se **dice**. Es documentación
clínica: el análisis es fiel a lo que aparece en el video, usa los valores y
nombres de botones exactos y, si algo no se ve u oye bien, lo indica.

## Qué hace

1. Lee cada video de la carpeta de entrada (`videos\` por defecto).
2. Lo analiza con Gemini (imagen y audio) y obtiene la lista de momentos: tiempo,
   título estilo manual, descripción breve, importancia, sección y zona señalada.
3. Guarda `momentos.json` en cuanto llega la respuesta (nada pagado se pierde).
4. Extrae del **archivo original** el fotograma más nítido cerca de cada momento.
5. Vuelve a enviar esas capturas en alta resolución para que el modelo corrija los
   textos con lo que ahora se lee en pantalla (refinado).
6. Dibuja las anotaciones y genera el `.docx` y el `.pdf`.

### Videos pesados de iPhone (.MOV HEVC)

Un `.MOV` HEVC 4K/60 fps de varios GB no se sube nunca tal cual: se genera una
**copia ligera** MP4 (720p, 2 fotogramas/s, audio mono) de unas decenas de MB, que
es lo que se sube a Gemini, y las **capturas salen del original** en alta calidad.
La copia es temporal y se borra al terminar.

### Por qué 720p, resolución "media" y un refinado

En estos videos hay **texto e iconos en pantalla** (menús del equipo, valores,
nombres de botones). Una copia demasiado pequeña o un análisis a baja resolución
hacen que el modelo no pueda leerlos y el manual pierda justo lo más valioso. Por
eso:

- la copia ligera es de **720p** por defecto (`--copia-alto 480|720|1080`); 480p
  sirve solo para reconocer la máquina y ahorrar, 1080p cuando la pantalla del
  equipo ocupa una parte pequeña del encuadre;
- Gemini mira el video con resolución **media** (`--resolucion baja|media|alta`);
  "baja" cuesta un tercio pero no distingue textos pequeños;
- tras extraer las capturas del original, el **refinado** envía esas imágenes fijas
  (muy baratas frente al video) y el modelo corrige título y descripción con lo
  que ahora se lee con claridad: valores, unidades, iconos, indicadores. No cambia
  tiempos ni añade o quita pasos; `--sin-refinado` lo desactiva.

Si algo sigue sin leerse bien, `--subir-original` sube el archivo tal cual (si cabe
en el límite de 2 GB de la API y es `.mp4`/`.mov`/`.webm`, etc.).

## Requisitos

- Windows, macOS o Linux con **Python 3.10 o superior**.
- ffmpeg: viene incluido con el paquete `imageio-ffmpeg`; si tiene otro, `--ffmpeg
  RUTA` o la variable `FFMPEG_BIN`. ffprobe es opcional.
- Una clave de la API de Gemini (https://aistudio.google.com/apikey) en un proyecto
  con **plan de pago** (ver Privacidad).

## Instalación

PowerShell (Windows):

```powershell
cd C:\ruta\resumen-videos
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.ejemplo .env
notepad .env          # escriba GEMINI_API_KEY=su_clave
```

macOS / Linux:

```bash
cd ~/resumen-videos
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.ejemplo .env && nano .env
```

Si PowerShell no deja activar el entorno: `Set-ExecutionPolicy -Scope CurrentUser
RemoteSigned` una sola vez.

## Uso básico

```powershell
python resumir_videos.py                                   # videos\  ->  salida\
python resumir_videos.py D:\grabaciones --equipo "arco en C" --salida D:\manuales
python resumir_videos.py --solo "arco parte 1" "arco parte 2"
```

Al terminar imprime una tabla (video, momentos, páginas, tokens, costo estimado,
estado) y la línea `ESTIMACIÓN de costo total`. Código de salida: 0 si todo salió
bien, 1 si algún video falló, 2 si hay un error de configuración (sin clave, sin
videos, sin ffmpeg).

Un video ya procesado (existe su `momentos.json`) se omite; `--forzar` lo vuelve
a analizar. Los videos que fallan no detienen a los demás.

## Qué sale en `salida\<video>\`

| Archivo | Contenido |
|---|---|
| `<video>.docx` | manual editable (Word): portada, índice por secciones, pasos con captura |
| `<video>.pdf` | el mismo manual en PDF |
| `momentos.json` | el análisis completo: momentos, uso de tokens, avisos, rutas de las capturas |
| `capturas\NN_mm-ss.jpg` | captura de cada paso; `..._anotada.jpg` si lleva círculo/flecha |
| `log.txt` | todo lo que se hizo, con hora |
| `error.txt` | solo si falló: mensaje claro y traza técnica |

## Opciones principales

| Opción | Efecto |
|---|---|
| `CARPETA` | carpeta con los videos (por defecto `videos\`) |
| `--salida DIR` | carpeta de salida (por defecto `salida\`) |
| `--equipo "arco en C"` | nombre del equipo que se enseña; se inserta en el prompt |
| `--modelo M` | modelo de Gemini (por defecto `GEMINI_MODELO` del `.env` o `gemini-3.1-flash-lite`) |
| `--resolucion baja\|media\|alta` | resolución con la que Gemini mira el video (por defecto media) |
| `--copia-alto 480\|720\|1080` | alto de la copia ligera que se sube (por defecto 720) |
| `--subir-original` | sube el archivo tal cual en vez de la copia ligera |
| `--sin-refinado` | no hacer la segunda pasada con las capturas en alta |
| `--redactor M` | segunda pasada solo de texto con un modelo más potente que pule la redacción |
| `--regenerar` | reutiliza `momentos.json` y rehace capturas y documentos sin volver a analizar |
| `--forzar` | volver a analizar videos ya procesados |
| `--solo NOMBRE ...` | procesar solo esos videos (nombre con o sin extensión) |
| `--batch` / `--batch-recoger [ID] [--esperar]` | modo batch (ver abajo) |
| `--local` / `--simular` | sin API (ver abajo) |
| `--max-momentos N` | conservar solo los N más importantes (por defecto sin límite) |
| `--importancia-minima 1-5` | descartar los momentos de importancia menor |
| `--por-pagina auto\|1\|2\|3\|4` | capturas por página (auto: según la cantidad de pasos) |
| `--sin-indice`, `--sin-anotaciones` | omitir el índice o el círculo/flecha |
| `--tramo-min N` | videos más largos que N minutos se analizan por tramos (por defecto 45) |
| `--fps F` | fotogramas por segundo que muestrea Gemini (por defecto 1/s) |
| `--precio-entrada X --precio-salida Y` | precios por millón de tokens para la estimación |
| `--pausa S`, `--conservar-subida`, `--timeout-procesado S`, `--max-subida-mb N` | ajustes finos |
| `--ffmpeg RUTA`, `--ffprobe RUTA` | binarios propios |
| `--verbose` | trazas técnicas en consola |
| `--version` | muestra la versión y sale |

`python resumir_videos.py --help` muestra la lista completa con ejemplos.

## Corregir a mano y regenerar (`--regenerar`)

`momentos.json` es editable. Puede corregir un título, ajustar un tiempo, borrar un
paso que sobra, cambiar la sección o la zona señalada (`"zona": {"x": 0.62, "y":
0.40}` en fracciones de la imagen) y volver a generar capturas y documentos sin
pagar otro análisis:

```powershell
notepad salida\arco_parte_1\momentos.json
python resumir_videos.py --regenerar --solo arco_parte_1
```

También sirve para pegar un análisis hecho en el chat de Gemini: respete la
estructura `{"analisis": {"momentos": [{"tiempo_seg": 65, "titulo": ...,
"descripcion": ..., "seccion": ..., "zona": ...}, ...]}}` (los demás campos son
opcionales). Con `--regenerar` no se llama a la API salvo para el refinado, que se
hace si hay clave; añada `--sin-refinado` para que no toque sus textos
corregidos, o use `--simular --regenerar` para trabajar totalmente sin API.

## Modo batch (mitad de precio)

Gemini cobra el 50 % en modo batch a cambio de entregar los resultados minutos u
horas más tarde. Es ideal para procesar muchos videos de una vez:

```powershell
python resumir_videos.py --batch                     # sube los videos y envía el lote
python resumir_videos.py --batch-recoger --esperar   # más tarde: recoge y genera los documentos
```

El envío deja `salida\_lotes\<id>.json`. `--batch-recoger` sin ID toma el único
lote pendiente; sin `--esperar` solo informa el estado y sale. Los archivos
subidos se borran al recoger el lote.

## Modo local (sin API, privacidad total)

`--local` no envía nada fuera del computador: detecta los cambios de plano con
ffmpeg y transcribe el audio con faster-whisper; los momentos salen de las frases
con contenido (valores, "importante", "nunca", verbos de acción) y de los cortes de
cámara. El resultado es mucho más tosco que con Gemini (no entiende lo que se ve),
pero permite un borrador útil.

```powershell
pip install -r requirements-local.txt
python resumir_videos.py --local                     # descarga el modelo "base" la primera vez
python resumir_videos.py --local --whisper-modelo small --offline
python resumir_videos.py --local --sin-whisper       # solo cambios de plano
```

El modelo de Whisper se descarga una vez desde huggingface.co (variable
`WHISPER_CACHE` para elegir la carpeta). Sin faster-whisper instalado, o sin
internet para la descarga, el modo local sigue solo con los cambios de plano y lo
avisa.

## Costos

Gemini cobra por tokens. Un video consume aproximadamente **100 tokens por segundo
a resolución baja y 300 a resolución media** (más el audio y un texto de salida
pequeño). Con `gemini-3.1-flash-lite` a 0,25 US$ por millón de tokens de entrada:

| Duración | Tokens de entrada aprox. | Costo aprox. (síncrono) | En batch (50 %) |
|---|---|---|---|
| 5 min | 30-90 mil | 0,01-0,03 US$ | 0,005-0,015 US$ |
| 15 min | 90-270 mil | 0,03-0,08 US$ | 0,015-0,04 US$ |
| 30 min | 180-540 mil | 0,05-0,15 US$ | 0,03-0,08 US$ |
| 40 min | 240-720 mil | 0,07-0,20 US$ | 0,04-0,10 US$ |

El refinado con capturas añade unos pocos miles de tokens por imagen (menos de
0,01 US$ por video). La tabla que imprime el programa es una **estimación** a
partir de los tokens reportados por la API y los precios de `config.py`:
verifíquelos en https://ai.google.dev/gemini-api/docs/pricing y ajústelos con
`--precio-entrada` / `--precio-salida` si cambian.

## Privacidad

- Use la clave de un proyecto con **plan de pago**: en el nivel gratuito Google
  puede usar el contenido enviado para mejorar sus productos; en el de pago no.
- Los archivos subidos se borran de la API al terminar cada video (salvo
  `--conservar-subida`); Google los eliminaría igualmente a las 48 horas.
- Si en los videos aparecen pacientes, datos personales o pantallas con
  información clínica, revise antes qué permite el INC y la normativa aplicable;
  en caso de duda use `--local`, que no envía nada fuera del computador.
- La clave nunca se imprime ni se guarda en los resultados. No comparta `.env`.

## Probar sin gastar

```powershell
python tools\crear_video_prueba.py videos\prueba.mp4     # video sintético de 90 s con 8 escenas
python resumir_videos.py --simular                       # momentos de ejemplo, sin API ni Whisper
```

`--simular` recorre todo el circuito (capturas, anotaciones, docx, pdf) con textos
marcados "(SIMULADO)". Abra el `.docx` en Word y el `.pdf` para comprobar que todo
se ve bien antes de gastar con videos reales. Los tests (`pip install pytest` y
`pytest`) tampoco usan la red.

## Solución de problemas

| Síntoma | Qué hacer |
|---|---|
| `No hay clave de API de Gemini` | cree `.env` junto a `resumir_videos.py` con `GEMINI_API_KEY=...`; sin clave, `--local` o `--simular` |
| `Error de la API de Gemini (401/403 ...)` | clave inválida o proyecto sin facturación: revise la clave en AI Studio |
| `(404 NOT_FOUND) ... model` | el modelo no está disponible para su clave: el programa prueba solos los alternativos; si no, `--modelo gemini-2.5-flash-lite` |
| `(429 ...)` | cuota o límite de velocidad: espere, use `--pausa 30` o el modo `--batch` |
| `No se encontró ffmpeg` | `pip install imageio-ffmpeg`, o instale ffmpeg y use `--ffmpeg RUTA` |
| Whisper no descarga el modelo | necesita internet la primera vez; después `--offline`; o `--sin-whisper` |
| Capturas oscuras o lavadas (video HDR del iPhone) | el ffmpeg incluido aplica tonemap si tiene los filtros; si no, pruebe con un ffmpeg completo (`--ffmpeg`) |
| El modelo no lee un valor de la pantalla | `--resolucion alta`, `--copia-alto 1080` o `--subir-original`; corrija en `momentos.json` y `--regenerar` |
| El docx se ve distinto | ábralo en Word (no en el visor de Windows): las páginas están maquetadas con filas de alto exacto |
| Un video falló | lea `salida\<video>\error.txt`; `--verbose` muestra la traza en consola |

## Mejoras posibles

- Marcar la zona con un rectángulo cuando el modelo devuelve una caja.
- Revisión asistida: una página HTML para aprobar o editar cada paso antes de generar el manual.
- Exportar también a Markdown o a una presentación.
- Detectar automáticamente varios videos de una misma sesión y unirlos en un solo manual.
- Un modo "solo audio" más barato para videos donde la imagen aporta poco.
