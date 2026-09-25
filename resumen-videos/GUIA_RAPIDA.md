# Guía rápida (4 pasos)

**Qué hace**: toma los videos de la carpeta `videos/` y genera, por cada uno, un manual con
capturas (`.docx` editable y `.pdf`) en `salida/<nombre del video>/`. La cantidad de pasos y
capturas la decide el contenido del video: lo que se enseña, se muestra o se señala.

## 1. Descargar el proyecto
En Google Drive, clic derecho sobre la carpeta **Resumen de videos a documento** → **Descargar**.
Descomprime el ZIP donde quieras (por ejemplo en `Documentos`). Copia la carpeta completa: dentro
van las fuentes del PDF y el ffmpeg se instala solo con las librerías.

## 2. Instalar (una sola vez)
- **Windows**: doble clic en `instalar.bat` (abre `instalar.ps1` en PowerShell). Si pide Python,
  instálalo desde la página que se abre marcando *Add python.exe to PATH* y vuelve a ejecutar
  `instalar.bat`.
- **macOS**: doble clic en `instalar.command` (si macOS lo bloquea: clic derecho → Abrir).

El instalador pide la **clave de la API de Gemini**: se obtiene en
https://aistudio.google.com/apikey con un proyecto de Google Cloud con facturación activa
(plan de pago: Google no usa tus videos para entrenar). Se guarda en el archivo `.env`, junto a
`resumir_videos.py`. Si la escribes a mano con el Bloc de notas, cuida que el archivo se llame
`.env` y no `.env.txt` (el programa avisa si pasa).

## 3. Poner los videos
Copia los `.mov` / `.mp4` en la carpeta `videos/` del proyecto (los de iPhone van tal cual,
aunque pesen gigas o estén grabados en vertical o en HDR: se sube una copia liviana y las
capturas salen del original).

## 4. Procesar
- **Windows**: doble clic en `resumir.bat`.
- **macOS**: doble clic en `resumir.command`.

Al terminar aparece una tabla con los videos procesados, el número de pasos, las páginas y una
**estimación de costo** (orientativa; la cifra real la da la facturación de Google). Los
resultados están en `salida/<nombre del video>/`: `<nombre>.pdf`, `<nombre>.docx`, `capturas/`,
`momentos.json` y `log.txt`.

## Variantes útiles
| Quiero…                                            | Windows (PowerShell en la carpeta)                                  |
|----------------------------------------------------|---------------------------------------------------------------------|
| probar sin gastar nada                             | `.\resumir.bat --simular`                                            |
| que nada salga del computador (sin Gemini)         | `.\resumir.bat --local`                                              |
| corregir a mano `momentos.json` y rehacer el PDF   | `.\resumir.bat --regenerar` (no usa la API ni la clave)              |
| procesar solo un video                             | `.\resumir.bat --solo "nombre del video"` (repetir `--solo` por video) |
| videos en otra carpeta                             | `.\resumir.bat D:\grabaciones` (la carpeta va primero)                |
| decir qué equipo es                                | `.\resumir.bat --equipo "arco en C"`                                 |
| sin transcripción del audio al final del manual    | `.\resumir.bat --sin-transcripcion`                                  |
| sin el recuadro de zoom en las capturas            | `.\resumir.bat --sin-lupa`                                           |
| varios clips cortos como una sola capacitación     | editar la lista en `tools/unir_clips.sh` y ejecutarlo; luego procesar el video unido |
| 50 % más barato (resultado en horas)               | `.\resumir.bat --batch` y luego `.\resumir.bat --batch-recoger ID` (el ID lo imprime el primero; `--batch-pendiente` si solo hay uno) |
| capturas oscuras en un video HDR                   | `.\resumir.bat --sin-tonemap --regenerar`                            |
| ver todas las opciones                             | `.\resumir.bat --help`                                               |

En macOS es igual con `./resumir.command`.

## Si algo falla
- Cada video tiene su `log.txt`; si falló, además un `error.txt` con la causa.
- Un video que falla no detiene a los demás.
- Si en `log.txt` aparece `aviso: paso N: ... se recortó`, ese paso tenía un texto enorme: el
  texto completo está en `momentos.json`.
- Más detalle en `README.md` (sección *Solución de problemas*).
