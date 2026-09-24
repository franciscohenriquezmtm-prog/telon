"""Tests de ``resumen_videos.video`` y de ``tools/crear_video_prueba.py``.

Los que necesitan ffmpeg usan las fixtures ``ffmpeg`` y ``video_prueba`` de ``conftest.py``
(se saltan si no hay ffmpeg).  Nada aquí llama a la red.
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import pytest
from PIL import Image, ImageFilter

from resumen_videos import config, video
from resumen_videos.modelos import InfoVideo

RAIZ = Path(__file__).resolve().parents[1]
CORTES = [12, 23, 35, 46, 58, 69, 80]          # cambios de escena del video de prueba (90 s, 8 escenas)
DURACION_PRUEBA = 90.0


def _info(ruta: Path, tamano_mb: float) -> InfoVideo:
    """InfoVideo mínimo para probar reglas que solo miran extensión y tamaño."""
    return InfoVideo(ruta=Path(ruta), nombre="x", duracion=10.0, fps=25.0, ancho=640, alto=360,
                     tamano_bytes=int(tamano_mb * 1024 * 1024))


@pytest.fixture
def registro():
    """Función ``log`` que acumula los mensajes en una lista."""
    lineas: list[str] = []

    def log(texto: str) -> None:
        lineas.append(texto)

    log.lineas = lineas  # type: ignore[attr-defined]
    return log


@pytest.fixture(scope="session")
def video_hdr(tmp_path_factory, ffmpeg) -> Path:
    """Clip HDR (HEVC 10 bits, PQ) de 3 s; se salta si este ffmpeg no puede codificarlo."""
    destino = tmp_path_factory.mktemp("hdr") / "hdr_prueba.mp4"
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=3",
                        "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
                        "-colorspace", "bt2020nc", "-c:v", "libx265", "-preset", "ultrafast",
                        "-x265-params", "log-level=error", "-tag:v", "hvc1", str(destino)],
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not destino.is_file():
        pytest.skip("este ffmpeg no puede generar un clip HDR (sin libx265)")
    return destino


# ----------------------------------------------------------------------------
# Nombres, listado, reglas puras
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("entrada, esperado", [
    ("arco:en*C?", "arco en C"),                      # caracteres prohibidos en Windows
    ('x<>|"y', "x y"),
    ("a/b\\c", "a b c"),
    ("  hola   mundo  ", "hola mundo"),               # colapsa espacios
    ("final...", "final"),                            # sin punto final
    ("informe. ", "informe"),
    ("", "video"),
    ("???", "video"),
    ("CON", "CON_video"),                             # nombre reservado de Windows
    ("nul", "nul_video"),
    ("áéíóúüñÁÉÍÓÚÜÑ_-9", "áéíóúüñÁÉÍÓÚÜÑ_-9"),      # acentos y ñ se conservan
    ("IMG_1234", "IMG_1234"),
    ("a" * 100, "a" * 80),                            # máximo 80
    ("a" * 79 + " b", "a" * 79),                      # el recorte no deja espacio final
])
def test_sanear_nombre(entrada, esperado):
    assert video.sanear_nombre(entrada) == esperado


def test_es_video():
    assert video.es_video(Path("clip.MP4"))
    assert video.es_video(Path("clip.mov"))
    assert not video.es_video(Path("notas.txt"))
    assert not video.es_video(Path("sin_extension"))


def test_listar_videos(tmp_path):
    for nombre in ("b.mp4", "A.MOV", "notas.txt", ".oculto.mp4", "c.webm"):
        (tmp_path / nombre).write_bytes(b"x")
    (tmp_path / "carpeta.mp4").mkdir()               # un directorio no es un video
    assert [p.name for p in video.listar_videos(tmp_path)] == ["A.MOV", "b.mp4", "c.webm"]
    with pytest.raises(FileNotFoundError):
        video.listar_videos(tmp_path / "no_existe")


def test_necesita_transcodificar():
    motivo = video.necesita_transcodificar(_info("clip.MOV", 10))
    assert motivo and "extensión .mov" in motivo
    assert video.necesita_transcodificar(_info("clip.mp4", 10)) is None
    assert video.necesita_transcodificar(_info("clip.webm", 10)) is None
    motivo = video.necesita_transcodificar(_info("clip.mp4", 2300))
    assert motivo == "tamaño 2300 MB > 300 MB"
    assert video.necesita_transcodificar(_info("clip.mp4", 50), umbral_mb=40) == "tamaño 50 MB > 40 MB"
    assert config.UMBRAL_TRANSCODIFICAR_MB == 300


def test_es_hdr():
    assert video.es_hdr({"color_transfer": "smpte2084"})
    assert video.es_hdr({"color_transfer": "arib-std-b67"})
    assert video.es_hdr({"color_transfer": " SMPTE2084 "})
    assert not video.es_hdr({"color_transfer": "bt709"})
    assert not video.es_hdr({})
    assert not video.es_hdr(None)


# ----------------------------------------------------------------------------
# Binarios
# ----------------------------------------------------------------------------
def test_localizar_ffmpeg(ffmpeg, monkeypatch):
    assert video.localizar_ffmpeg(ffmpeg) == ffmpeg
    with pytest.raises(RuntimeError, match="No existe el ffmpeg"):
        video.localizar_ffmpeg(str(Path(ffmpeg).parent / "no_existe_ffmpeg"))
    monkeypatch.setenv("FFMPEG_BIN", ffmpeg)
    assert video.localizar_ffmpeg() == ffmpeg


def test_localizar_ffprobe(ffmpeg, monkeypatch):
    assert video.localizar_ffprobe(ffmpeg) == ffmpeg               # el argumento manda (aquí usamos ffmpeg como doble)
    monkeypatch.setenv("FFPROBE_BIN", ffmpeg)
    assert video.localizar_ffprobe() == ffmpeg
    monkeypatch.setenv("FFPROBE_BIN", str(Path(ffmpeg).parent / "no_existe_ffprobe"))
    resultado = video.localizar_ffprobe()
    assert resultado is None or Path(resultado).name != "no_existe_ffprobe"


def test_filtros_disponibles(ffmpeg):
    filtros = video._filtros_disponibles(ffmpeg)
    assert {"scale", "fps", "scdet"} <= set(filtros)
    assert isinstance(video._tonemap_disponible(ffmpeg), bool)


# ----------------------------------------------------------------------------
# obtener_info por los tres caminos
# ----------------------------------------------------------------------------
def _comprobar_info(info: InfoVideo, ruta: Path, origen: str) -> None:
    assert info.ruta == ruta.resolve() and info.ruta.is_absolute()
    assert info.nombre == "prueba_maquina"
    assert abs(info.duracion - DURACION_PRUEBA) < 1.0
    assert abs(info.fps - 25.0) < 0.01
    assert (info.ancho, info.alto) == (640, 360)
    assert info.tamano_bytes == ruta.stat().st_size
    assert info.extra["origen_info"] == origen
    assert info.extra["codec"] == "h264"
    assert info.extra["pix_fmt"] == "yuv420p"
    assert not video.es_hdr(info.extra)


def test_obtener_info_ffprobe(ffmpeg, video_prueba):
    ffprobe = video.localizar_ffprobe()
    if not ffprobe:
        pytest.skip("no hay ffprobe (exporta FFPROBE_BIN para probar este camino)")
    _comprobar_info(video.obtener_info(video_prueba, ffmpeg, ffprobe), video_prueba, "ffprobe")


def test_obtener_info_ffmpeg_stderr(ffmpeg, video_prueba):
    _comprobar_info(video.obtener_info(video_prueba, ffmpeg, ffprobe=None), video_prueba, "ffmpeg")


def test_obtener_info_pyav(ffmpeg, video_prueba, monkeypatch):
    pytest.importorskip("av")

    def falla(*_args, **_kwargs):
        raise RuntimeError("ffmpeg forzado a fallar")

    monkeypatch.setattr(video, "_info_ffmpeg_stderr", falla)
    _comprobar_info(video.obtener_info(video_prueba, ffmpeg, ffprobe=None), video_prueba, "pyav")


def test_obtener_info_ffprobe_roto_cae_a_ffmpeg(ffmpeg, video_prueba):
    """Un ffprobe que no funciona no impide obtener la información."""
    info = video.obtener_info(video_prueba, ffmpeg, ffprobe=str(Path(ffmpeg).parent / "ffprobe_inexistente"))
    _comprobar_info(info, video_prueba, "ffmpeg")


def test_obtener_info_errores(ffmpeg, tmp_path):
    with pytest.raises(FileNotFoundError):
        video.obtener_info(tmp_path / "no_existe.mp4", ffmpeg)
    falso = tmp_path / "falso.mp4"
    falso.write_text("esto no es un video", encoding="utf-8")
    with pytest.raises(RuntimeError, match="No se pudo leer"):
        video.obtener_info(falso, ffmpeg)


def test_obtener_info_hdr(ffmpeg, video_hdr):
    """Con ffprobe y sin él se detecta la transferencia HDR."""
    info = video.obtener_info(video_hdr, ffmpeg, ffprobe=None)
    assert info.extra["codec"] == "hevc"
    assert info.extra["color_transfer"] == "smpte2084"
    assert video.es_hdr(info.extra)
    ffprobe = video.localizar_ffprobe()
    if ffprobe:
        extra = video.obtener_info(video_hdr, ffmpeg, ffprobe).extra
        assert extra["color_transfer"] == "smpte2084" and extra["pix_fmt"] == "yuv420p10le"


# ----------------------------------------------------------------------------
# Fotogramas y nitidez
# ----------------------------------------------------------------------------
def test_extraer_fotograma(ffmpeg, video_prueba, tmp_path):
    assert video.extraer_fotograma(video_prueba, 500.0, tmp_path / "fuera.jpg", ffmpeg) is None
    assert not (tmp_path / "fuera.jpg").exists()
    ruta = video.extraer_fotograma(video_prueba, 40.0, tmp_path / "sub" / "f40.jpg", ffmpeg)
    assert ruta == tmp_path / "sub" / "f40.jpg" and ruta.stat().st_size > 1000
    with Image.open(ruta) as img:
        assert img.size == (640, 360)
    ruta = video.extraer_fotograma(video_prueba, 40.0, tmp_path / "chico.jpg", ffmpeg, ancho_max=320)
    with Image.open(ruta) as img:
        assert img.size == (320, 180)
    with pytest.raises(FileNotFoundError):
        video.extraer_fotograma(tmp_path / "no.mp4", 1.0, tmp_path / "x.jpg", ffmpeg)


def test_nitidez(ffmpeg, video_prueba, tmp_path):
    nitido = video.extraer_fotograma(video_prueba, 50.0, tmp_path / "nitido.jpg", ffmpeg)
    with Image.open(nitido) as img:
        img.filter(ImageFilter.GaussianBlur(3)).save(tmp_path / "borroso.jpg", quality=95)
    assert video.nitidez(tmp_path / "borroso.jpg") < video.nitidez(nitido) / 5
    Image.new("L", (2, 2)).save(tmp_path / "mini.png")
    assert video.nitidez(tmp_path / "mini.png") == 0.0


def test_extraer_mejor_fotograma_elige_nitido(ffmpeg, video_prueba, tmp_path, monkeypatch, registro):
    """A los 50 s (escena con movimiento) se evalúa una ráfaga y se elige el fotograma de mayor nitidez."""
    puntajes: dict[str, float] = {}
    original = video.nitidez

    def espia(ruta):
        valor = original(ruta)
        puntajes[Path(ruta).name] = valor
        return valor

    monkeypatch.setattr(video, "nitidez", espia)
    destino = tmp_path / "capturas" / "50.jpg"
    ruta, t_real = video.extraer_mejor_fotograma(video_prueba, 50.0, destino, ffmpeg, DURACION_PRUEBA, log=registro)
    assert ruta == destino and destino.stat().st_size > 1000
    assert 49.0 <= t_real <= 51.0
    assert len(puntajes) >= 4                                    # 2 s × 2.5 fps ≈ 5 fotogramas
    mejor = max(puntajes, key=puntajes.get)
    indice = int(Path(mejor).stem.split("_")[1])
    assert t_real == pytest.approx(49.0 + (indice - 1) / config.FPS_RAFAGA)
    assert registro.lineas == []                                 # sin avisos cuando todo va bien


def test_extraer_mejor_fotograma_acotado(ffmpeg, video_prueba, tmp_path, registro):
    ruta, t_real = video.extraer_mejor_fotograma(video_prueba, 0.0, tmp_path / "t0.jpg", ffmpeg,
                                                 DURACION_PRUEBA, log=registro)
    assert ruta and ruta.is_file() and 0.0 <= t_real <= 1.0
    ruta, t_real = video.extraer_mejor_fotograma(video_prueba, DURACION_PRUEBA + 5, tmp_path / "fin.jpg", ffmpeg,
                                                 DURACION_PRUEBA, log=registro)
    assert ruta and ruta.is_file()
    assert DURACION_PRUEBA - 2.0 <= t_real <= DURACION_PRUEBA - 0.5
    ruta, t_real = video.extraer_mejor_fotograma(video_prueba, -3.0, tmp_path / "neg.jpg", ffmpeg,
                                                 DURACION_PRUEBA, log=registro)
    assert ruta and 0.0 <= t_real <= 1.0


def test_extraer_mejor_fotograma_nunca_lanza(ffmpeg, tmp_path, registro):
    assert video.extraer_mejor_fotograma(tmp_path / "no.mp4", 5.0, tmp_path / "x.jpg", ffmpeg, 90.0,
                                         log=registro) == (None, None)
    assert any("no existe" in l for l in registro.lineas)


def test_extraer_mejor_fotograma_rafaga_vacia(ffmpeg, video_prueba, tmp_path, monkeypatch, registro):
    """Si la ráfaga no produce archivos se recurre a un fotograma simple en t."""
    original = video._ejecutar

    def sin_rafaga(cmd):
        if any("f_%03d.jpg" in str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return original(cmd)

    monkeypatch.setattr(video, "_ejecutar", sin_rafaga)
    destino = tmp_path / "simple.jpg"
    assert video.extraer_mejor_fotograma(video_prueba, 30.0, destino, ffmpeg, DURACION_PRUEBA,
                                         log=registro) == (destino, 30.0)
    assert destino.is_file() and any("ráfaga" in l for l in registro.lineas)


# ----------------------------------------------------------------------------
# HDR: camino real (si hay libx265 y zscale/tonemap) y camino con dobles
# ----------------------------------------------------------------------------
def _comandos_ffmpeg(monkeypatch) -> list[list[str]]:
    """Espía ``_ejecutar`` que registra cada comando sin ejecutarlo ni crear archivos."""
    comandos: list[list[str]] = []

    def falso(cmd):
        comandos.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(video, "_ejecutar", falso)
    return comandos


def _filtro_vf(cmd: list[str]) -> str:
    return cmd[cmd.index("-vf") + 1]


def test_captura_hdr_real(ffmpeg, video_hdr, tmp_path, registro):
    if not video._tonemap_disponible(ffmpeg):
        pytest.skip("este ffmpeg no tiene zscale/tonemap")
    ruta, t_real = video.extraer_mejor_fotograma(video_hdr, 1.0, tmp_path / "hdr.jpg", ffmpeg, 3.0, log=registro)
    assert ruta and ruta.is_file() and 0.0 <= t_real <= 2.0
    with Image.open(ruta) as img:
        assert img.size == (320, 180)
    assert registro.lineas == []                                 # con tonemap no hay aviso


def test_transcodificar_hdr_real(ffmpeg, video_hdr, tmp_path, registro):
    if not video._tonemap_disponible(ffmpeg):
        pytest.skip("este ffmpeg no tiene zscale/tonemap")
    salida = video.transcodificar_para_subida(video_hdr, tmp_path / "hdr_ligero.mp4", ffmpeg, log=registro)
    extra = video.obtener_info(salida, ffmpeg, ffprobe=None).extra
    assert extra["color_transfer"] == "bt709" and not video.es_hdr(extra)


def test_hdr_con_filtros_usa_tonemap(ffmpeg, video_prueba, tmp_path, monkeypatch, registro):
    """Doble: video marcado como HDR y ffmpeg con zscale/tonemap → la cadena de filtros va antes de scale."""
    monkeypatch.setattr(video, "_es_hdr_archivo", lambda *_a: True)
    monkeypatch.setattr(video, "_filtros_disponibles", lambda _f: frozenset({"zscale", "tonemap", "scale"}))
    monkeypatch.setattr(video, "_avisos_hdr", set())
    comandos = _comandos_ffmpeg(monkeypatch)
    assert video.extraer_mejor_fotograma(video_prueba, 10.0, tmp_path / "x.jpg", ffmpeg, DURACION_PRUEBA,
                                         log=registro) == (None, None)
    rafaga = _filtro_vf(comandos[0])
    assert rafaga.startswith(video.FILTRO_TONEMAP + ",fps=") and rafaga.endswith("scale='min(1280,iw)':-2")
    assert _filtro_vf(comandos[1]) == video.FILTRO_TONEMAP + ",scale='min(1280,iw)':-2"   # fotograma simple
    assert not any("aviso" in l for l in registro.lineas)


def test_hdr_sin_filtros_avisa_una_vez(ffmpeg, video_prueba, tmp_path, monkeypatch, registro):
    """Doble: video HDR pero ffmpeg sin zscale/tonemap → captura tal cual y un único aviso."""
    monkeypatch.setattr(video, "_es_hdr_archivo", lambda *_a: True)
    monkeypatch.setattr(video, "_filtros_disponibles", lambda _f: frozenset({"scale", "fps"}))
    monkeypatch.setattr(video, "_avisos_hdr", set())
    for t in (10.0, 20.0):
        ruta, _ = video.extraer_mejor_fotograma(video_prueba, t, tmp_path / f"{t}.jpg", ffmpeg, DURACION_PRUEBA,
                                                log=registro)
        assert ruta and ruta.is_file()
    avisos = [l for l in registro.lineas if "zscale/tonemap" in l]
    assert len(avisos) == 1 and "lavadas" in avisos[0]


def test_transcodificar_hdr_sin_filtros(ffmpeg, video_prueba, tmp_path, monkeypatch, registro):
    monkeypatch.setattr(video, "_es_hdr_archivo", lambda *_a: True)
    monkeypatch.setattr(video, "_filtros_disponibles", lambda _f: frozenset())
    monkeypatch.setattr(video, "_avisos_hdr", set())
    salida = video.transcodificar_para_subida(video_prueba, tmp_path / "ligero.mp4", ffmpeg, log=registro)
    assert salida.is_file() and any("zscale/tonemap" in l for l in registro.lineas)


# ----------------------------------------------------------------------------
# Escenas, audio y copia ligera
# ----------------------------------------------------------------------------
def test_detectar_escenas(ffmpeg, video_prueba):
    escenas = video.detectar_escenas(video_prueba, ffmpeg)
    tiempos = [t for t, _ in escenas]
    assert tiempos == sorted(tiempos) and all(s > 0 for _, s in escenas)
    encontrados = sum(1 for corte in CORTES if any(abs(t - corte) <= 1.0 for t in tiempos))
    assert encontrados >= 6, f"cortes detectados: {tiempos}"
    assert video.detectar_escenas(video_prueba, ffmpeg, umbral=100.0) == []      # nada supera el máximo
    assert video.detectar_escenas(video_prueba, ffmpeg, umbral=1000.0) == []     # fuera de rango: se acota
    with pytest.raises(FileNotFoundError):
        video.detectar_escenas(video_prueba.parent / "no.mp4", ffmpeg)


def test_extraer_audio_16k(ffmpeg, video_prueba, tmp_path):
    ruta = video.extraer_audio_16k(video_prueba, tmp_path / "audio" / "a.wav", ffmpeg)
    assert ruta == tmp_path / "audio" / "a.wav"
    with wave.open(str(ruta)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        assert abs(w.getnframes() / w.getframerate() - DURACION_PRUEBA) < 1.0
    falso = tmp_path / "falso.mp4"
    falso.write_text("no es un video", encoding="utf-8")
    with pytest.raises(RuntimeError):
        video.extraer_audio_16k(falso, tmp_path / "b.wav", ffmpeg)


def test_transcodificar_para_subida(ffmpeg, video_prueba, tmp_path, registro):
    salida = video.transcodificar_para_subida(video_prueba, tmp_path / "sube" / "ligero.mp4", ffmpeg, log=registro)
    assert salida.is_file() and salida.stat().st_size < video_prueba.stat().st_size
    original = video.obtener_info(video_prueba, ffmpeg, ffprobe=None)
    ligero = video.obtener_info(salida, ffmpeg, ffprobe=None)
    assert abs(ligero.duracion - original.duracion) <= 0.5
    assert ligero.fps == pytest.approx(config.TRANSCODIFICAR_FPS, abs=0.1)
    assert ligero.alto == 360 and ligero.ancho == 640          # no se amplía un video más chico que 480p
    assert ligero.extra["codec"] == "h264"
    assert any("transcodificando" in l for l in registro.lineas) and any("%" in l for l in registro.lineas)
    falso = tmp_path / "falso.mp4"
    falso.write_text("no es un video", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no pudo transcodificar"):
        video.transcodificar_para_subida(falso, tmp_path / "x.mp4", ffmpeg, log=registro)


def test_transcodificar_comando(ffmpeg, video_prueba, tmp_path, monkeypatch, registro):
    """El comando lleva los parámetros de la especificación y solo mapea video y audio."""
    capturado: dict = {}

    def falso(cmd, duracion, log):
        capturado["cmd"] = [str(c) for c in cmd]
        Path(cmd[-1]).write_bytes(b"mp4")
        return 0, ""

    monkeypatch.setattr(video, "_ejecutar_con_progreso", falso)
    video.transcodificar_para_subida(video_prueba, tmp_path / "l.mp4", ffmpeg, alto=480, fps=2, log=registro)
    cmd = capturado["cmd"]
    assert _filtro_vf(cmd) == "scale=-2:'2*trunc(min(480,ih)/2)',fps=2"
    pares = list(zip(cmd, cmd[1:]))
    for esperado in (("-c:v", "libx264"), ("-preset", "veryfast"), ("-crf", "30"), ("-pix_fmt", "yuv420p"),
                     ("-c:a", "aac"), ("-b:a", f"{config.TRANSCODIFICAR_AUDIO_KBPS}k"), ("-ac", "1"),
                     ("-movflags", "+faststart"), ("-map", "0:v:0"), ("-map", "0:a:0?")):
        assert esperado in pares, f"falta {esperado} en {cmd}"
    assert "shell" not in cmd and cmd[-1] == str(tmp_path / "l.mp4")


# ----------------------------------------------------------------------------
# tools/crear_video_prueba.py
# ----------------------------------------------------------------------------
def test_crear_video_prueba(ffmpeg, video_prueba, tmp_path):
    from crear_video_prueba import crear_video_prueba, duraciones_escenas, limites_escenas

    assert video_prueba.stat().st_size < 3 * 1024 * 1024
    assert limites_escenas(90, 8) == CORTES
    assert sum(duraciones_escenas(90, 8)) == 90
    assert duraciones_escenas(10, 3) == [4, 3, 3] and limites_escenas(10, 3) == [4, 7]
    with pytest.raises(ValueError):
        duraciones_escenas(2, 5)
    corto = crear_video_prueba(tmp_path / "corto" / "c.mp4", duracion=6, escenas=2)
    info = video.obtener_info(corto, ffmpeg, ffprobe=None)
    assert abs(info.duracion - 6.0) < 0.5 and (info.ancho, info.alto) == (640, 360)


def test_crear_video_prueba_cli():
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "crear_video_prueba.py"), "--help"],
                       capture_output=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0 and "--duracion" in r.stdout and "--escenas" in r.stdout
