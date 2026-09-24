"""Estructuras de datos compartidas por todos los módulos.

Todas son dataclasses simples, serializables a dict con ``a_dict()`` para
guardarlas en ``momentos.json``.  Ningún módulo debe definir sus propias
estructuras para estos conceptos: así los módulos escritos por separado
encajan entre sí.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def formatear_tiempo(segundos: float) -> str:
    """Devuelve ``mm:ss`` (los minutos pueden superar 59, p. ej. ``75:03``).

    >>> formatear_tiempo(65.4)
    '01:05'
    >>> formatear_tiempo(4503)
    '75:03'
    """
    total = max(0, int(round(float(segundos))))
    return f"{total // 60:02d}:{total % 60:02d}"


@dataclass
class InfoVideo:
    """Datos básicos de un archivo de video (obtenidos con ffprobe/ffmpeg/PyAV)."""

    ruta: Path                 # ruta absoluta del archivo original
    nombre: str                # nombre base sin extensión, ya saneado para usarlo como carpeta
    duracion: float            # segundos
    fps: float
    ancho: int
    alto: int
    tamano_bytes: int
    extra: dict = field(default_factory=dict)   # codec, pix_fmt, color_transfer, rotación… (si ffprobe está disponible)

    def a_dict(self) -> dict:
        return {
            "ruta": str(self.ruta),
            "nombre": self.nombre,
            "duracion_seg": round(self.duracion, 3),
            "duracion": formatear_tiempo(self.duracion),
            "fps": self.fps,
            "ancho": self.ancho,
            "alto": self.alto,
            "tamano_bytes": self.tamano_bytes,
            "extra": dict(self.extra),
        }


@dataclass
class Momento:
    """Un momento clave del video."""

    tiempo_seg: float                   # segundos desde el inicio (ya validado y acotado a la duración)
    titulo: str                         # corto (<= config.MAX_TITULO caracteres)
    descripcion: str                    # máx. 2 frases (<= config.MAX_DESCRIPCION caracteres)
    importancia: int = 3                # 1 (menor) .. 5 (crítico); lo asigna el modelo o el modo local
    fuente: str = "ambos"               # "visual" | "audio" | "ambos": de dónde sale la importancia
    seccion: Optional[str] = None       # fase o sección del procedimiento (p. ej. "Preparación"); agrupa pasos
    zona: Optional[dict] = None         # dónde señalar en la captura, coords normalizadas 0..1:
                                        #   {"x": 0.62, "y": 0.40}  (punto)  o  {"caja": [x1, y1, x2, y2]}
                                        #   None si no aplica. Sirve para dibujar flecha/círculo.
    ruta_captura: Optional[str] = None  # ruta ABSOLUTA del JPEG original, o None si no se pudo capturar
    ruta_captura_anotada: Optional[str] = None  # JPEG con flecha/círculo dibujado (si hay zona), o None
    tiempo_real_seg: Optional[float] = None  # instante exacto del fotograma elegido (puede diferir ±1 s)
    puntaje: Optional[float] = None     # relevancia numérica (solo modo local)

    @property
    def tiempo(self) -> str:
        """``mm:ss`` del momento (para mostrar en el documento)."""
        return formatear_tiempo(self.tiempo_seg)

    @property
    def captura_para_documento(self) -> Optional[str]:
        """La captura anotada si existe; si no, la original; si no, None."""
        return self.ruta_captura_anotada or self.ruta_captura

    def a_dict(self, base: Optional[Path] = None) -> dict:
        """Dict serializable. Si se pasa ``base``, la ruta de la captura se guarda relativa a ella."""
        def _rel(ruta):
            if ruta and base is not None:
                try:
                    return str(Path(ruta).relative_to(base))
                except ValueError:
                    return str(ruta)
            return ruta
        return {
            "tiempo": self.tiempo,
            "tiempo_seg": round(self.tiempo_seg, 3),
            "titulo": self.titulo,
            "descripcion": self.descripcion,
            "importancia": self.importancia,
            "fuente": self.fuente,
            "seccion": self.seccion,
            "zona": self.zona,
            "captura": _rel(self.ruta_captura),
            "captura_anotada": _rel(self.ruta_captura_anotada),
            "tiempo_real_seg": None if self.tiempo_real_seg is None else round(self.tiempo_real_seg, 3),
            "puntaje": None if self.puntaje is None else round(self.puntaje, 3),
        }


@dataclass
class Uso:
    """Tokens consumidos en una o varias llamadas a Gemini (sumados)."""

    modelo: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_pensamiento: int = 0
    tokens_total: int = 0
    detalle_entrada: dict = field(default_factory=dict)  # {"VIDEO": n, "AUDIO": n, "TEXT": n}
    llamadas: int = 0
    batch: bool = False
    costo_usd: Optional[float] = None   # None si no hay precio conocido para el modelo

    def sumar(self, otro: "Uso") -> None:
        self.tokens_entrada += otro.tokens_entrada
        self.tokens_salida += otro.tokens_salida
        self.tokens_pensamiento += otro.tokens_pensamiento
        self.tokens_total += otro.tokens_total
        self.llamadas += otro.llamadas
        for k, v in otro.detalle_entrada.items():
            self.detalle_entrada[k] = self.detalle_entrada.get(k, 0) + v
        if otro.costo_usd is not None:
            self.costo_usd = (self.costo_usd or 0.0) + otro.costo_usd

    def a_dict(self) -> dict:
        return {
            "modelo": self.modelo,
            "tokens_entrada": self.tokens_entrada,
            "tokens_salida": self.tokens_salida,
            "tokens_pensamiento": self.tokens_pensamiento,
            "tokens_total": self.tokens_total,
            "detalle_entrada": dict(self.detalle_entrada),
            "llamadas": self.llamadas,
            "batch": self.batch,
            "costo_usd": None if self.costo_usd is None else round(self.costo_usd, 4),
        }


@dataclass
class ResultadoAnalisis:
    """Lo que devuelve el análisis de un video (Gemini, batch, local o simulado)."""

    momentos: list                      # list[Momento], en orden cronológico
    modo: str                           # "gemini" | "gemini-batch" | "local" | "simulado"
    modelo: str = ""                    # modelo realmente usado (p. ej. tras un fallback)
    resumen: Optional[str] = None       # 1-2 frases para la portada
    uso: Optional[Uso] = None
    texto_bruto: str = ""               # respuesta cruda del modelo (para depurar)
    truncado: bool = False              # la respuesta llegó cortada y se rescató lo que se pudo
    avisos: list = field(default_factory=list)      # list[str]
    transcripcion: Optional[list] = None            # modo local: [{"inicio": s, "fin": s, "texto": str}]
    tramos: int = 1                     # en cuántos tramos se analizó el video (videos muy largos)
    titulo: Optional[str] = None        # título corto del manual propuesto por el modelo (titulo_video)

    def a_dict(self, base: Optional[Path] = None) -> dict:
        return {
            "modo": self.modo,
            "modelo": self.modelo,
            "titulo": self.titulo,
            "resumen": self.resumen,
            "truncado": self.truncado,
            "tramos": self.tramos,
            "avisos": list(self.avisos),
            "uso": None if self.uso is None else self.uso.a_dict(),
            "momentos": [m.a_dict(base) for m in self.momentos],
            "transcripcion": self.transcripcion,
        }


@dataclass
class ResultadoVideo:
    """Resultado del procesamiento completo de un video."""

    info: InfoVideo
    carpeta_salida: Path
    exito: bool
    analisis: Optional[ResultadoAnalisis] = None
    ruta_docx: Optional[Path] = None
    ruta_pdf: Optional[Path] = None
    paginas_pdf: Optional[int] = None
    error: Optional[str] = None
    segundos: float = 0.0               # duración del procesamiento
    omitido: bool = False               # ya estaba procesado (momentos.json existente): se cargó sin volver a analizar
