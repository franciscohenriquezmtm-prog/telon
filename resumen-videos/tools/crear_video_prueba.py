#!/usr/bin/env python3
"""Genera un video sintético de prueba con escenas, rótulos y tonos.

Uso:  python tools/crear_video_prueba.py [destino] [--duracion 90] [--escenas 8]

Sirve para probar el programa sin gastar en la API (``--simular`` o ``--local``)
y para los tests.  Con los valores por defecto (90 s, 8 escenas) los cortes de
escena quedan exactamente en 12, 23, 35, 46, 58, 69 y 80 s.  No necesita el
filtro ``drawtext`` de ffmpeg: los rótulos se dibujan con Pillow usando la
fuente DejaVu incluida en el paquete y se superponen con el filtro ``overlay``.
También puede importarse: ``crear_video_prueba(destino, duracion=90, escenas=8) -> Path``.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from resumen_videos import config  # noqa: E402
from resumen_videos.video import localizar_ffmpeg  # noqa: E402

ANCHO, ALTO, FPS = 640, 360, 25
# Con 90 s y 8 escenas se respetan estos tiempos (cortes en 12, 23, 35, 46, 58, 69 y 80 s),
# que es lo que documentan los tests del proyecto.
DURACIONES_POR_DEFECTO = (12, 11, 12, 11, 12, 11, 11, 10)

# (fuente lavfi, opciones propias, título de la escena).  Las fuentes testsrc/testsrc2
# tienen movimiento (sirven para probar la elección del fotograma más nítido).
ESCENAS_BASE = [
    ("testsrc2", "", "Encendido de la máquina"),
    ("color", "c=navy", "Pantalla de inicio"),
    ("smptebars", "", "Calibración de barras"),
    ("color", "c=darkgreen", "Posicionamiento del paciente"),
    ("testsrc", "", "Verificación de imagen"),
    ("color", "c=maroon", "Inicio del tratamiento"),
    ("color", "c=steelblue", "Monitoreo de dosis"),
    ("color", "c=orange", "Apagado y cierre"),
]
COLORES_EXTRA = ["teal", "purple", "gold", "crimson", "olive", "slategray", "chocolate", "deeppink"]


def duraciones_escenas(duracion: int, escenas: int) -> list[int]:
    """Duración en segundos de cada escena (suman ``duracion``)."""
    if escenas < 1:
        raise ValueError("escenas debe ser >= 1")
    if duracion < escenas:
        raise ValueError("la duración debe ser al menos de 1 s por escena")
    if (duracion, escenas) == (90, len(DURACIONES_POR_DEFECTO)):
        return list(DURACIONES_POR_DEFECTO)
    base, resto = divmod(int(duracion), int(escenas))
    return [base + (1 if i < resto else 0) for i in range(escenas)]


def limites_escenas(duracion: int, escenas: int) -> list[int]:
    """Instantes (s) en los que cambia la escena, sin contar el inicio ni el final."""
    limites, acumulado = [], 0
    for d in duraciones_escenas(duracion, escenas)[:-1]:
        acumulado += d
        limites.append(acumulado)
    return limites


def _escena(indice: int) -> tuple[str, str, str]:
    """Fuente, opciones y título de la escena ``indice`` (0-based); más allá de las 8 base se generan colores."""
    if indice < len(ESCENAS_BASE):
        return ESCENAS_BASE[indice]
    color = COLORES_EXTRA[(indice - len(ESCENAS_BASE)) % len(COLORES_EXTRA)]
    return "color", f"c={color}", f"Escena adicional {indice + 1}"


def _fuente(nombre: str, tamano: int):
    """Carga una fuente DejaVu del paquete (nunca la del sistema)."""
    from PIL import ImageFont

    ruta = config.CARPETA_FUENTES / nombre
    if not ruta.is_file():
        raise FileNotFoundError(f"Falta la fuente {ruta}: debe venir incluida en resumen_videos/fuentes")
    return ImageFont.truetype(str(ruta), tamano)


def _crear_rotulo(indice: int, titulo: str, t0: int, destino: Path) -> Path:
    """PNG con fondo semitransparente y el texto 'ESCENA n / título (desde mm:ss)'."""
    from PIL import Image, ImageDraw

    grande = _fuente("DejaVuSans-Bold.ttf", 40)
    pequena = _fuente("DejaVuSans.ttf", 22)
    img = Image.new("RGBA", (ANCHO, 130), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20, 10, ANCHO - 20, 120], radius=14, fill=(0, 0, 0, 170),
                        outline=(255, 255, 255, 220), width=3)
    d.text((ANCHO / 2, 45), f"ESCENA {indice}", font=grande, fill=(255, 255, 255, 255), anchor="mm")
    d.text((ANCHO / 2, 92), f"{titulo}  (desde {t0 // 60:02d}:{t0 % 60:02d})", font=pequena,
           fill=(255, 230, 120, 255), anchor="mm")
    img.save(destino)
    return destino


def _ruta_concat(ruta: Path) -> str:
    """Ruta en el formato del demuxer concat de ffmpeg (barras normales, comilla simple escapada)."""
    return ruta.as_posix().replace("'", r"'\''")


def _ejecutar(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falló ({r.returncode}): {r.stderr.strip()[-800:]}")


def crear_video_prueba(destino: Path, duracion: int = 90, escenas: int = 8, ffmpeg: str | None = None) -> Path:
    """Crea el video sintético en ``destino`` (mp4 H.264 + AAC, 640x360, 25 fps) y devuelve su ruta."""
    destino = Path(destino)
    ffmpeg = localizar_ffmpeg(ffmpeg)
    duraciones = duraciones_escenas(int(duracion), int(escenas))
    destino.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="video_prueba_") as tmp:
        trabajo = Path(tmp)
        segmentos, t0 = [], 0
        for i, dur in enumerate(duraciones):
            fuente, opciones, titulo = _escena(i)
            rotulo = _crear_rotulo(i + 1, titulo, t0, trabajo / f"rotulo_{i + 1}.png")
            origen = f"{fuente}={opciones + ':' if opciones else ''}s={ANCHO}x{ALTO}:r={FPS}:d={dur}"
            frecuencia = round(220 * 2 ** (i / 4), 1)   # un tono distinto por escena
            segmento = trabajo / f"seg_{i + 1:03d}.mp4"
            _ejecutar([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", origen,
                "-i", str(rotulo),
                "-f", "lavfi", "-i", f"sine=frequency={frecuencia}:sample_rate=44100:d={dur}",
                "-filter_complex", "[0:v][1:v]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p[v]",
                "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", str(2 * FPS),
                "-c:a", "aac", "-b:a", "48k", "-ar", "44100",
                "-t", str(dur), str(segmento),
            ])
            segmentos.append(segmento)
            t0 += dur

        lista = trabajo / "concat.txt"
        # Rutas con barras normales y comillas simples escapadas: así las entiende el demuxer concat en Windows.
        lista.write_text("".join(f"file '{_ruta_concat(s)}'\n" for s in segmentos), encoding="utf-8")
        _ejecutar([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                   "-i", str(lista), "-c", "copy", "-movflags", "+faststart", str(destino)])
    return destino


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera un video sintético (escenas de colores con rótulos y tonos) para probar resumen_videos.")
    parser.add_argument("destino", nargs="?", default=str(RAIZ / config.CARPETA_VIDEOS / "prueba_maquina.mp4"),
                        help="ruta del mp4 a crear (por defecto videos/prueba_maquina.mp4)")
    parser.add_argument("--duracion", type=int, default=90, help="duración total en segundos (90)")
    parser.add_argument("--escenas", type=int, default=8, help="número de escenas (8)")
    parser.add_argument("--ffmpeg", default=None, help="ruta a ffmpeg (por defecto se localiza sola)")
    args = parser.parse_args(argv)
    try:
        ruta = crear_video_prueba(Path(args.destino), args.duracion, args.escenas, args.ffmpeg)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Video creado: {ruta} ({ruta.stat().st_size / 1e6:.2f} MB, {args.duracion} s, {args.escenas} escenas)")
    print("Cortes de escena (s):", ", ".join(str(t) for t in limites_escenas(args.duracion, args.escenas)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
