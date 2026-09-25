"""Tests de ``anotar.py``: círculo con halo y flecha, caja redondeada, zonas inválidas."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from resumen_videos.anotar import anotar_captura


def _imagen(carpeta: Path, ancho: int = 640, alto: int = 360, nombre: str = "captura.jpg") -> Path:
    """JPEG liso verde oscuro (sin rojos ni blancos) para detectar las marcas por color."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / nombre
    Image.new("RGB", (ancho, alto), (40, 90, 40)).save(ruta, "JPEG", quality=90)
    return ruta


def _rgb(ruta: Path) -> np.ndarray:
    with Image.open(ruta) as im:
        return np.asarray(im.convert("RGB")).astype(int)


def _mascara_roja(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] > 180) & (rgb[..., 1] < 120) & (rgb[..., 2] < 120)


def _contar(ruta: Path, condicion) -> int:
    return int(condicion(_rgb(ruta)).sum())


def _es_rojo(px) -> bool:
    return px[0] > 180 and px[1] < 120 and px[2] < 120


def _es_blanco(px) -> bool:
    return min(px) > 200


def _pixel(ruta: Path, x: int, y: int) -> tuple:
    return tuple(int(v) for v in _rgb(ruta)[y, x])


def _hay_blanco_cerca(ruta: Path, x: int, y: int, radio: int = 2) -> bool:
    """Algún píxel casi blanco en el cuadrado de ±radio alrededor de (x, y) (el halo es fino y suavizado)."""
    rgb = _rgb(ruta)[y - radio:y + radio + 1, x - radio:x + radio + 1]
    return bool((rgb.min(axis=2) > 170).any())


def test_punto_dibuja_circulo_halo_y_flecha(tmp_path):
    origen = _imagen(tmp_path)
    destino = tmp_path / "salida" / "anotada.jpg"
    assert anotar_captura(origen, {"x": 0.5, "y": 0.5}, destino) == destino
    assert destino.exists()
    with Image.open(destino) as im:
        assert im.size == (640, 360) and im.format == "JPEG"
    assert _contar(origen, _mascara_roja) == 0
    assert _contar(destino, _mascara_roja) > 300
    assert _contar(destino, lambda rgb: rgb.min(axis=2) > 200) > 300
    # el trazo rojo pasa por el radio (7 % del ancho = 44.8 px) sobre el centro (320, 180)
    assert _es_rojo(_pixel(destino, 320, 180 - 43))
    # el halo blanco sobresale por fuera y por dentro del trazo
    assert _hay_blanco_cerca(destino, 320, 180 - 47)
    assert _hay_blanco_cerca(destino, 320, 180 - 39)
    # el centro queda limpio (solo se marca el contorno)
    assert not _es_rojo(_pixel(destino, 320, 180))
    # el original no se toca
    assert _contar(origen, _mascara_roja) == 0


def test_flecha_sale_de_la_esquina_mas_lejana(tmp_path):
    origen = _imagen(tmp_path)
    destino = tmp_path / "flecha.jpg"
    # punto arriba a la izquierda: la flecha llega desde abajo a la derecha
    assert anotar_captura(origen, {"x": 0.15, "y": 0.2}, destino, estilo="flecha") is not None
    assert _contar(destino, _mascara_roja) > 50
    # sin círculo: no hay rojo justo sobre el radio del círculo en el lado opuesto a la flecha
    assert not _es_rojo(_pixel(destino, int(0.15 * 640) - 43, int(0.2 * 360)))
    # la flecha ocupa la diagonal hacia abajo-derecha, ~18 % del ancho
    ys, xs = np.nonzero(_mascara_roja(_rgb(destino)))
    assert xs.max() > 0.15 * 640 + 60 and ys.max() > 0.2 * 360 + 40


def test_caja_redondeada(tmp_path):
    origen = _imagen(tmp_path)
    destino = tmp_path / "caja.jpg"
    assert anotar_captura(origen, {"caja": [0.25, 0.25, 0.75, 0.75]}, destino) == destino
    assert _contar(destino, _mascara_roja) > 500
    # borde izquierdo (x = 160) a media altura, dentro del trazo
    assert _es_rojo(_pixel(destino, 160 + 1, 180))
    # el interior de la caja queda limpio
    assert not _es_rojo(_pixel(destino, 320, 180))
    # halo por fuera del borde
    assert _hay_blanco_cerca(destino, 160 - 2, 180)


def test_caja_con_esquinas_invertidas_o_minuscula(tmp_path):
    origen = _imagen(tmp_path)
    assert anotar_captura(origen, {"caja": [0.8, 0.7, 0.2, 0.3]}, tmp_path / "a.jpg") is not None
    # caja diminuta: se ensancha hasta un mínimo visible
    assert anotar_captura(origen, {"caja": [0.5, 0.5, 0.501, 0.7]}, tmp_path / "b.jpg") is not None
    assert _contar(tmp_path / "b.jpg", _mascara_roja) > 50


def test_caja_puntual_se_anota_como_punto(tmp_path):
    """Una caja con los dos lados minúsculos (o nulos) es un punto: círculo con flecha en su centro."""
    origen = _imagen(tmp_path)
    for nombre, caja in (("c", [0.5, 0.5, 0.503, 0.503]), ("d", [0.5, 0.5, 0.5, 0.5])):
        destino = tmp_path / f"{nombre}.jpg"
        assert anotar_captura(origen, {"caja": caja}, destino) == destino
        assert _es_rojo(_pixel(destino, 320, 180 - 43)) and not _es_rojo(_pixel(destino, 320, 180))


def test_caja_que_cubre_toda_la_captura_no_se_anota(tmp_path):
    origen = _imagen(tmp_path)
    mensajes: list = []
    for caja in ([0, 0, 1, 1], [0.05, 0.05, 0.95, 0.95]):
        destino = tmp_path / "marco.jpg"
        assert anotar_captura(origen, {"caja": caja}, destino, log=mensajes.append) is None
        assert not destino.exists()
    assert len(mensajes) == 2 and all("casi toda la captura" in m for m in mensajes)
    # una franja ancha pero de poca altura sí es una zona concreta
    assert anotar_captura(origen, {"caja": [0, 0.1, 1, 0.5]}, tmp_path / "franja.jpg") is not None


def test_caja_en_borde_o_esquina_queda_dentro(tmp_path):
    origen = _imagen(tmp_path)
    for nombre, caja in (("esquina", [0.99, 0.99, 1, 1]), ("borde", [0.998, 0.3, 1, 0.7]), ("origen", [0, 0, 0.005, 0.01])):
        destino = tmp_path / f"{nombre}.jpg"
        assert anotar_captura(origen, {"caja": caja}, destino) == destino
        mascara = _mascara_roja(_rgb(destino))
        assert mascara.sum() > 150, nombre                          # la marca completa (los 4 lados) está en la imagen
        ys, xs = np.nonzero(mascara)
        assert xs.min() >= 1 and xs.max() <= 638 and ys.min() >= 1 and ys.max() <= 358, nombre


@pytest.mark.parametrize("zona", [
    None, {}, [], "0.5,0.5", {"x": 0.5}, {"y": 0.5}, {"x": 1.5, "y": 0.2}, {"x": -0.1, "y": 0.2},
    {"x": "0.5", "y": 0.5}, {"x": True, "y": 0.5}, {"x": float("nan"), "y": 0.1},
    {"caja": [0.1, 0.2]}, {"caja": [0.1, 0.2, 1.5, 0.9]}, {"caja": ["a", 0, 1, 1]},
    {"caja": None},
])
def test_zona_invalida_devuelve_none(tmp_path, zona):
    origen = _imagen(tmp_path)
    destino = tmp_path / "no.jpg"
    assert anotar_captura(origen, zona, destino) is None
    assert not destino.exists()


def test_imagen_inexistente_devuelve_none(tmp_path):
    assert anotar_captura(tmp_path / "no_existe.jpg", {"x": 0.5, "y": 0.5}, tmp_path / "x.jpg") is None


def test_archivo_que_no_es_imagen_devuelve_none(tmp_path):
    falso = tmp_path / "falso.jpg"
    falso.write_text("esto no es un jpeg", encoding="utf-8")
    assert anotar_captura(falso, {"x": 0.5, "y": 0.5}, tmp_path / "x.jpg") is None


def test_estilo_desconocido_usa_circulo(tmp_path):
    origen = _imagen(tmp_path)
    destino = tmp_path / "e.jpg"
    assert anotar_captura(origen, {"x": 0.5, "y": 0.5}, destino, estilo="otro") == destino
    assert _es_rojo(_pixel(destino, 320, 180 - 43))


def test_captura_vertical_y_acepta_rutas_str(tmp_path):
    origen = _imagen(tmp_path, ancho=360, alto=640)
    destino = tmp_path / "v.jpg"
    assert anotar_captura(str(origen), {"x": 0.9, "y": 0.05}, str(destino)) == destino
    with Image.open(destino) as im:
        assert im.size == (360, 640)
    assert _contar(destino, _mascara_roja) > 100


# ----------------------------------------------------------------------------- lupa
def _imagen_con_detalle(carpeta: Path, ancho: int = 640, alto: int = 360) -> Path:
    """Fondo verde con un cuadrado azul pequeño en (0.2, 0.2): la lupa lo amplía en la esquina opuesta."""
    ruta = _imagen(carpeta, ancho, alto, "detalle.jpg")
    with Image.open(ruta) as im:
        im = im.convert("RGB")
        px = int(0.2 * ancho)
        py = int(0.2 * alto)
        for x in range(px - 6, px + 7):
            for y in range(py - 6, py + 7):
                im.putpixel((x, y), (30, 30, 220))
        im.save(ruta, "JPEG", quality=95)
    return ruta


def _mascara_azul(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 2] > 150) & (rgb[..., 0] < 100) & (rgb[..., 1] < 100)


def test_lupa_amplia_la_zona_en_la_esquina_mas_lejana(tmp_path):
    origen = _imagen_con_detalle(tmp_path)
    con = tmp_path / "con_lupa.jpg"
    sin = tmp_path / "sin_lupa.jpg"
    assert anotar_captura(origen, {"x": 0.2, "y": 0.2}, con) == con
    assert anotar_captura(origen, {"x": 0.2, "y": 0.2}, sin, lupa=False) == sin
    rgb_con, rgb_sin = _rgb(con), _rgb(sin)
    # el cuadrado azul de 13 px ampliado x2,5 ocupa muchos más píxeles azules en la esquina inferior derecha
    esquina = (slice(int(360 * 0.5), 360), slice(int(640 * 0.5), 640))
    assert _mascara_azul(rgb_con[esquina]).sum() > 400
    assert _mascara_azul(rgb_sin[esquina]).sum() == 0
    # el recuadro lleva borde rojo y halo blanco en esa esquina; sin lupa no hay rojo allí (la flecha es corta)
    assert _mascara_roja(rgb_con[esquina]).sum() > _mascara_roja(rgb_sin[esquina]).sum() + 200
    # la lupa no tapa el punto señalado ni el círculo
    assert _es_rojo(_pixel(con, int(0.2 * 640), int(0.2 * 360) - 43))


def test_lupa_en_caja_pequena_y_no_en_caja_grande(tmp_path):
    origen = _imagen_con_detalle(tmp_path)
    pequena, grande = tmp_path / "caja_p.jpg", tmp_path / "caja_g.jpg"
    assert anotar_captura(origen, {"caja": [0.18, 0.17, 0.22, 0.23]}, pequena) == pequena
    assert anotar_captura(origen, {"caja": [0.1, 0.1, 0.6, 0.6]}, grande) == grande
    esquina = (slice(180, 360), slice(320, 640))
    assert _mascara_azul(_rgb(pequena)[esquina]).sum() > 400
    assert _mascara_azul(_rgb(grande)[esquina]).sum() == 0


def test_lupa_con_punto_cerca_del_borde_no_falla(tmp_path):
    origen = _imagen(tmp_path)
    for zona in ({"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.99, "y": 0.01}):
        destino = tmp_path / f"borde_{zona['x']}_{zona['y']}.jpg"
        assert anotar_captura(origen, zona, destino) == destino
        with Image.open(destino) as im:
            assert im.size == (640, 360)
