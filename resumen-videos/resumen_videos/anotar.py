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
from typing import Callable

from PIL import Image, ImageDraw

COLOR_MARCA = (255, 59, 48)        # rojo-naranja (#FF3B30)
COLOR_HALO = (255, 255, 255)
CALIDAD_JPEG = 95
AREA_MAXIMA_CAJA = 0.8             # una caja que cubre más de esto es "toda la pantalla": no se anota
_SUPERMUESTREO = 3                 # factor de la capa de dibujo (suaviza los bordes)
_RADIO_FRAC = 0.07                 # radio del círculo: 7 % del ancho
_TRAZO_FRAC = 0.006                # grosor del trazo: 0.6 % del ancho (mínimo 3 px)
_FLECHA_FRAC = 0.18                # longitud de la flecha: ~18 % del ancho
_LADO_MINIMO_CAJA = 0.005          # con los dos lados por debajo, la caja es un punto: círculo con flecha
_ESTILOS = ("circulo", "flecha")
# Lupa: recuadro en la esquina más lejana con la zona señalada ampliada a resolución completa, para que los
# iconos y textos pequeños (un candado, un valor en pantalla) se lean en el manual.  Medidas relativas al lado
# menor de la captura: se recorta un cuadrado de LUPA_RECORTE_FRAC y se muestra a LUPA_TAMANO_FRAC (x2,5).
LUPA_RECORTE_FRAC = 0.20
LUPA_TAMANO_FRAC = 0.50
_LUPA_MARGEN_FRAC = 0.02           # separación del recuadro con los bordes de la captura
_LUPA_CAJA_MAXIMA = 0.30           # una caja más ancha que esto (del lado menor) ya se ve: sin lupa


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
    return x1, y1, x2, y2


def _caja_es_punto(caja: tuple[float, float, float, float]) -> bool:
    """True si los dos lados son minúsculos: se anota como punto (centro de la caja)."""
    return caja[2] - caja[0] < _LADO_MINIMO_CAJA and caja[3] - caja[1] < _LADO_MINIMO_CAJA


def _caja_demasiado_amplia(caja: tuple[float, float, float, float]) -> bool:
    """True si la caja cubre casi toda la captura (el modelo la devuelve así cuando no está seguro)."""
    return (caja[2] - caja[0]) * (caja[3] - caja[1]) > AREA_MAXIMA_CAJA


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


def _rect_lupa(px: float, py: float, ancho: int, alto: int) -> tuple[int, int, int, int]:
    """Rectángulo (x1, y1, x2, y2) del recuadro de la lupa, en la esquina más lejana del punto señalado."""
    base = min(ancho, alto)
    lado = int(round(LUPA_TAMANO_FRAC * base))
    margen = int(round(_LUPA_MARGEN_FRAC * base))
    ex, ey = _esquina_mas_lejana(px, py, ancho, alto)
    x1 = margen if ex == 0 else ancho - margen - lado
    y1 = margen if ey == 0 else alto - margen - lado
    return x1, y1, x1 + lado, y1 + lado


def _recorte_lupa(cx: float, cy: float, ancho: int, alto: int, lado: float) -> tuple[int, int, int, int]:
    """Cuadrado de ``lado`` centrado en (cx, cy) y desplazado para no salirse de la captura."""
    lado = int(round(min(lado, ancho, alto)))
    x1 = int(round(min(max(cx - lado / 2, 0), ancho - lado)))
    y1 = int(round(min(max(cy - lado / 2, 0), alto - lado)))
    return x1, y1, x1 + lado, y1 + lado


def _pegar_lupa(imagen: Image.Image, recorte: tuple[int, int, int, int], rect: tuple[int, int, int, int],
                marca: tuple[float, float] | None) -> None:
    """Pega en ``rect`` la zona ``recorte`` ampliada (sobre ``imagen``, en su sitio) con borde blanco y rojo.

    ``marca`` (x, y en píxeles de la captura) dibuja dentro de la lupa un círculo fino sobre el punto señalado.
    """
    x1, y1, x2, y2 = rect
    lado = x2 - x1
    ampliada = imagen.crop(recorte).resize((lado, lado), Image.LANCZOS)
    dibujo = ImageDraw.Draw(ampliada)
    if marca is not None:
        escala = lado / (recorte[2] - recorte[0])
        mx, my = (marca[0] - recorte[0]) * escala, (marca[1] - recorte[1]) * escala
        r = 0.22 * lado
        grosor = max(2, int(round(lado * 0.012)))
        dibujo.ellipse([mx - r - grosor, my - r - grosor, mx + r + grosor, my + r + grosor],
                       outline=COLOR_HALO, width=grosor * 3)
        dibujo.ellipse([mx - r, my - r, mx + r, my + r], outline=COLOR_MARCA, width=grosor)
    imagen.paste(ampliada.convert("RGBA"), (x1, y1))
    borde = max(3, int(round(lado * 0.02)))
    marco = ImageDraw.Draw(imagen)
    marco.rectangle([x1 - borde, y1 - borde, x2 + borde - 1, y2 + borde - 1], outline=COLOR_HALO, width=borde)
    marco.rectangle([x1 - 1, y1 - 1, x2, y2], outline=COLOR_MARCA, width=max(2, borde // 2))


def _borde_hacia(rect: tuple[int, int, int, int], destino: tuple[float, float]) -> tuple[float, float]:
    """Punto del borde de ``rect`` por donde sale la recta desde su centro hacia ``destino``."""
    x1, y1, x2, y2 = rect
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    dx, dy = destino[0] - cx, destino[1] - cy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return cx, cy
    tx = (x2 - cx) / abs(dx) if dx else math.inf
    ty = (y2 - cy) / abs(dy) if dy else math.inf
    t = min(tx, ty)
    return cx + dx * t, cy + dy * t


def _dibujar_punto(dibujo: ImageDraw.ImageDraw, x: float, y: float, ancho: int, alto: int,
                   trazo: float, halo: float, estilo: str,
                   origen_flecha: tuple[float, float] | None = None) -> None:
    """Círculo con halo (salvo estilo 'flecha') y flecha corta desde la esquina más lejana.

    Con ``origen_flecha`` (píxeles), la flecha sale de ese punto (el borde de la lupa) en vez de la esquina.
    """
    radio = _RADIO_FRAC * ancho
    if estilo == "flecha":
        radio = trazo  # la flecha apunta casi al punto exacto
    else:
        # Pillow dibuja el contorno hacia adentro de la caja: el halo se dibuja con la caja
        # ampliada en ``halo`` para que sobresalga por ambos lados del trazo
        for color, ancho_linea, extra in ((COLOR_HALO, trazo + 2 * halo, halo), (COLOR_MARCA, trazo, 0.0)):
            r = radio + extra
            dibujo.ellipse([x - r, y - r, x + r, y + r], outline=color, width=int(round(ancho_linea)))
    ex, ey = origen_flecha if origen_flecha is not None else _esquina_mas_lejana(x, y, ancho, alto)
    dist = math.hypot(ex - x, ey - y) or 1.0
    ux, uy = (ex - x) / dist, (ey - y) / dist
    punta = (x + ux * (radio + halo + trazo), y + uy * (radio + halo + trazo))
    if origen_flecha is not None:
        origen = (ex - ux * halo, ey - uy * halo)
        if math.hypot(origen[0] - punta[0], origen[1] - punta[1]) < 2 * trazo:
            return
    else:
        largo = _FLECHA_FRAC * ancho
        origen = (punta[0] + ux * largo, punta[1] + uy * largo)
    _dibujar_flecha(dibujo, origen, punta, trazo, halo)


def _dibujar_caja(dibujo: ImageDraw.ImageDraw, caja: tuple[float, float, float, float],
                  ancho: int, alto: int, trazo: float, halo: float) -> None:
    """Rectángulo redondeado con halo; una caja minúscula se ensancha para que se vea, sin salirse de la imagen."""
    x1, y1, x2, y2 = caja[0] * ancho, caja[1] * alto, caja[2] * ancho, caja[3] * alto
    minimo = 0.04 * ancho
    borde = halo + trazo / 2   # lo que sobresale el halo por fuera del rectángulo
    if x2 - x1 < minimo:
        cx = (x1 + x2) / 2
        x1, x2 = cx - minimo / 2, cx + minimo / 2
    if y2 - y1 < minimo:
        cy = (y1 + y2) / 2
        y1, y2 = cy - minimo / 2, cy + minimo / 2
    # una caja pegada a un borde o esquina se desplaza hacia adentro: si no, la marca queda casi fuera
    dx = max(0.0, borde - x1) - max(0.0, x2 + borde - ancho)
    dy = max(0.0, borde - y1) - max(0.0, y2 + borde - alto)
    x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
    radio = max(2.0 * trazo, 0.01 * ancho)
    for color, ancho_linea, extra in ((COLOR_HALO, trazo + 2 * halo, halo), (COLOR_MARCA, trazo, 0.0)):
        dibujo.rounded_rectangle([x1 - extra, y1 - extra, x2 + extra, y2 + extra], radius=radio + extra,
                                 outline=color, width=int(round(ancho_linea)))


def anotar_captura(ruta_jpg: Path, zona: dict, destino: Path, estilo: str = "circulo", *, lupa: bool = True,
                   log: Callable[[str], None] | None = None) -> Path | None:
    """Dibuja la zona señalada sobre la captura y guarda el resultado en ``destino`` (JPEG, calidad 90).

    Con ``lupa`` (por defecto) se añade en la esquina más lejana un recuadro con la zona señalada ampliada x2,5
    a partir de la captura a resolución completa, y la flecha sale de ese recuadro hacia el punto: así un icono
    o un valor de pantalla pequeño se lee en el manual.  Una caja ancha (más de ``_LUPA_CAJA_MAXIMA`` del lado
    menor) no lleva lupa: ya se ve.

    ``zona`` es ``{"x": 0-1, "y": 0-1}`` (círculo rojo-naranja con halo blanco y una flecha
    corta que llega desde la esquina más lejana; con ``estilo="flecha"`` solo la flecha) o
    ``{"caja": [x1, y1, x2, y2]}`` 0-1 (rectángulo redondeado).  Una caja minúscula se anota como
    punto (su centro); una que cubre más de ``AREA_MAXIMA_CAJA`` de la imagen no se anota (sería un marco
    alrededor de toda la captura: ruido) y se explica por ``log`` si se pasa.  Devuelve ``destino`` o
    ``None`` si la zona es inválida o la imagen no se puede leer; nunca lanza por eso.
    """
    if not isinstance(zona, dict):
        return None
    punto = _punto(zona) if "x" in zona or "y" in zona else None
    caja = _caja(zona) if punto is None else None
    if caja is not None and _caja_es_punto(caja):
        punto, caja = ((caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2), None
    if caja is not None and _caja_demasiado_amplia(caja):
        if log is not None:
            log(f"  aviso: la zona señalada cubre casi toda la captura ({Path(ruta_jpg).name}): no se anota")
        return None
    if punto is None and caja is None:
        return None
    estilo = estilo if estilo in _ESTILOS else "circulo"
    try:
        with Image.open(ruta_jpg) as origen:
            imagen = origen.convert("RGBA")
    except (OSError, ValueError):
        return None
    ancho, alto = imagen.size
    base = min(ancho, alto)
    origen_flecha = None
    if lupa:
        if punto is not None:
            px, py = punto[0] * ancho, punto[1] * alto
            rect = _rect_lupa(px, py, ancho, alto)
            recorte = _recorte_lupa(px, py, ancho, alto, LUPA_RECORTE_FRAC * base)
            _pegar_lupa(imagen, recorte, rect, (px, py))
            origen_flecha = _borde_hacia(rect, (px, py))
        elif max((caja[2] - caja[0]) * ancho, (caja[3] - caja[1]) * alto) <= _LUPA_CAJA_MAXIMA * base:
            cx, cy = (caja[0] + caja[2]) / 2 * ancho, (caja[1] + caja[3]) / 2 * alto
            lado_caja = max((caja[2] - caja[0]) * ancho, (caja[3] - caja[1]) * alto)
            rect = _rect_lupa(cx, cy, ancho, alto)
            recorte = _recorte_lupa(cx, cy, ancho, alto, max(LUPA_RECORTE_FRAC * base, lado_caja * 1.3))
            _pegar_lupa(imagen, recorte, rect, None)
    S = _SUPERMUESTREO
    capa = Image.new("RGBA", (ancho * S, alto * S), (0, 0, 0, 0))
    dibujo = ImageDraw.Draw(capa)
    trazo = max(_TRAZO_FRAC * ancho, 3.0) * S
    halo = max(trazo * 0.6, 2.0 * S)   # borde blanco a cada lado del trazo
    if punto is not None:
        _dibujar_punto(dibujo, punto[0] * ancho * S, punto[1] * alto * S, ancho * S, alto * S,
                       trazo, halo, estilo,
                       None if origen_flecha is None else (origen_flecha[0] * S, origen_flecha[1] * S))
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
