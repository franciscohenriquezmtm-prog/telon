"""resumen_videos: convierte videos largos en documentos didácticos con capturas.

Paquete con los módulos:
  - config:      constantes y valores por defecto.
  - modelos:     estructuras de datos compartidas (InfoVideo, Momento, Uso, ...).
  - video:       utilidades ffmpeg (información, fotogramas nítidos, escenas, audio).
  - gemini:      análisis con la API de Gemini (Files API, JSON, modo batch, costo).
  - documentos:  generación del .docx y del .pdf.
  - local:       modo 100 % local (detección de escenas + faster-whisper).
  - pipeline:    orquestación por carpeta y por video.
"""

__version__ = "1.0.0"
