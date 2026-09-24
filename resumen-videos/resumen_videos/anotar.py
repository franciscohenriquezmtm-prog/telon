"""Anotación de capturas con Pillow: círculo con halo y flecha, o caja redondeada.

El modelo puede indicar, por momento, la zona de la imagen que la persona
señala (``Momento.zona``).  Aquí se dibuja esa marca sobre la captura para que
el lector del manual vea de inmediato de qué botón, palanca o pantalla se habla.

Las marcas se dibujan sobre una capa transparente supermuestreada (x3) que
luego se reduce y se funde con la foto: bordes suaves sin tocar los píxeles
de la captura.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

COLOR_MARCA = (255, 59, 48)        # rojo-naranja (#FF3B30)
COLOR_HALO = (255, 255, 255)
CALIDAD_JPEG = 90
_SUPERMUESTREO = 3                 # factor de la capa de dibujo (suaviza los bordes)
_RADIO_FRAC = 0.07                 # radio del círculo: 7 % del ancho
_TRAZO_FRAC = 0.006                # grosor del trazo: 0.6 % del ancho (mínimo 3 px)
_FLECHA_FRAC = 0.18                # longitud de la flecha: ~18 % del ancho
_ESTILOS = ("circulo", "flecha")


def _numero(valor) -> float | None:
    """Convierte a float si es un número finito (los bool no cuentan); si no, None."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    valor = float(valor)
    return valor if math.isfinite(valor) else None


def _punto(zona: dict) -> tuple[float, float] | None:
    """Devuelve (x, y) normalizados 0-1 o None si la zona no es un punto válido."""
    x, y = _numero(zona.get("x")), _numero(zona.get("y"))
    if x is None or y is None or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return x, y


def _caja(zona: dict) -> tuple[float, float, float, float] | None:
    """Devuelve (x1, y1, x2, y2) normalizados y ordenados, o None si la caja no es válida."""
    caja = zona.get("caja")
    if not isinstance(caja, (list, tuple)) or len(caja) != 4:
        return None
    valores = [_numero(v) for v in caja]
    if any(v is None or not (0.0 <= v <= 1.0) for v in valores):
        return None
    x1, x2 = sorted((valores[0], valores[2]))
    y1, y2 = sorted((valores[1], valores[3]))
    if x2 - x1 < 0.005 and y2 - y1 < 0.005:
        return None
    return x1, y1, x2, y2


def _esquina_mas_lejana(px: float, py: float, ancho: int, alto: int) -> tuple[float, float]:
    """Esquina de la imagen más alejada del punto (de ahí sale la flecha)."""
    esquinas = ((0, 0), (ancho, 0), (0, alto), (ancho, alto))
    return max(esquinas, key=lambda e: (e[0] - px) ** 2 + (e[1] - py) ** 2)


def _dibujar_flecha(dibujo: ImageDraw.ImageDraw, origen: tuple[float, float], punta: tuple[float, float],
                    trazo: float, halo: float) -> None:
    """Flecha recta de ``origen`` a ``punta`` con cabeza triangular, primero el halo y luego el color."""
    dx, dy = punta[0] - origen[0], punta[1] - origen[1]
    largo = math.hypot(dx, dy)
    if largo < 1:
        return
    ux, uy = dx / largo, dy / largo
    cabeza = max(4.0 * trazo, 10.0)
    base = (punta[0] - ux * cabeza, punta[1] - uy * cabeza)
    # perpendicular para las alas de la cabeza
    wx, wy = -uy * cabeza * 0.55, ux * cabeza * 0.55
    triangulo = [punta, (base[0] + wx, base[1] + wy), (base[0] - wx, base[1] - wy)]
    for color, ancho_linea in ((COLOR_HALO, trazo + 2 * halo), (COLOR_MARCA, trazo)):
        dibujo.line([origen, base], fill=color, width=int(round(ancho_linea)))
        if color == COLOR_HALO:
            dibujo.polygon(triangulo, fill=color, outline=color, width=int(round(halo)))
        else:
            dibujo.polygon(triangulo, fill=color)


def _dibujar_punto(dibujo: ImageDraw.ImageDraw, x: float, y: float, ancho: int, alto: int,
                   trazo: float, halo: float, estilo: str) -> None:
    """Círculo con halo (salvo estilo 'flecha') y flecha corta desde la esquina más lejana."""
    radio = _RADIO_FRAC * ancho
    if estilo == "flecha":
        radio = trazo  # la flecha apunta casi al punto exacto
    else:
        # Pillow dibuja el contorno hacia adentro de la caja: el halo se dibuja con la caja
        # ampliada en ``halo`` para que sobresalga por ambos lados del trazo
        for color, ancho_linea, extra in ((COLOR_HALO, trazo + 2 * halo, halo), (COLOR_MARCA, trazo, 0.0)):
            r = radio + extra
            dibujo.ellipse([x - r, y - r, x + r, y + r], outline=color, width=int(round(ancho_linea)))
    ex, ey = _esquina_mas_lejana(x, y, ancho, alto)
    dist = math.hypot(ex - x, ey - y) or 1.0
    ux, uy = (ex - x) / dist, (ey - y) / dist
    largo = _FLECHA_FRAC * ancho
    punta = (x + ux * (radio + halo + trazo), y + uy * (radio + halo + trazo))
    origen = (punta[0] + ux * largo, punta[1] + uy * largo)
    _dibujar_flecha(dibujo, origen, punta, trazo, halo)


def _dibujar_caja(dibujo: ImageDraw.ImageDraw, caja: tuple[float, float, float, float],
                  ancho: int, alto: int, trazo: float, halo: float) -> None:
    """Rectángulo redondeado con halo; una caja minúscula se ensancha para que se vea."""
    x1, y1, x2, y2 = caja[0] * ancho, caja[1] * alto, caja[2] * ancho, caja[3] * alto
    minimo = 0.04 * ancho
    if x2 - x1 < minimo:
        cx = (x1 + x2) / 2
        x1, x2 = cx - minimo / 2, cx + minimo / 2
    if y2 - y1 < minimo:
        cy = (y1 + y2) / 2
        y1, y2 = cy - minimo / 2, cy + minimo / 2
    radio = max(2.0 * trazo, 0.01 * ancho)
    for color, ancho_linea, extra in ((COLOR_HALO, trazo + 2 * halo, halo), (COLOR_MARCA, trazo, 0.0)):
        dibujo.rounded_rectangle([x1 - extra, y1 - extra, x2 + extra, y2 + extra], radius=radio + extra,
                                 outline=color, width=int(round(ancho_linea)))


def anotar_captura(ruta_jpg: Path, zona: dict, destino: Path, estilo: str = "circulo") -> Path | None:
    """Dibuja la zona señalada sobre la captura y guarda el resultado en ``destino`` (JPEG, calidad 90).

    ``zona`` es ``{"x": 0-1, "y": 0-1}`` (círculo rojo-naranja con halo blanco y una flecha
    corta que llega desde la esquina más lejana; con ``estilo="flecha"`` solo la flecha) o
    ``{"caja": [x1, y1, x2, y2]}`` 0-1 (rectángulo redondeado).  Devuelve ``destino`` o
    ``None`` si la zona es inválida o la imagen no se puede leer; nunca lanza por eso.
    """
    if not isinstance(zona, dict):
        return None
    punto = _punto(zona) if "x" in zona or "y" in zona else None
    caja = _caja(zona) if punto is None else None
    if punto is None and caja is None:
        return None
    estilo = estilo if estilo in _ESTILOS else "circulo"
    try:
        with Image.open(ruta_jpg) as origen:
            imagen = origen.convert("RGBA")
    except (OSError, ValueError):
        return None
    ancho, alto = imagen.size
    S = _SUPERMUESTREO
    capa = Image.new("RGBA", (ancho * S, alto * S), (0, 0, 0, 0))
    dibujo = ImageDraw.Draw(capa)
    trazo = max(_TRAZO_FRAC * ancho, 3.0) * S
    halo = max(trazo * 0.6, 2.0 * S)   # borde blanco a cada lado del trazo
    if punto is not None:
        _dibujar_punto(dibujo, punto[0] * ancho * S, punto[1] * alto * S, ancho * S, alto * S,
                       trazo, halo, estilo)
    else:
        _dibujar_caja(dibujo, caja, ancho * S, alto * S, trazo, halo)
    capa = capa.resize((ancho, alto), Image.LANCZOS)
    resultado = Image.alpha_composite(imagen, capa).convert("RGB")
    destino = Path(destino)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        resultado.save(destino, "JPEG", quality=CALIDAD_JPEG)
    except OSError:
        return None
    return destino
