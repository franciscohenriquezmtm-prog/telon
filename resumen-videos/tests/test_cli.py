"""Tests de ``resumir_videos.py`` sin subproceso: análisis de argumentos, carga de ``.env`` y comprobaciones previas."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import resumir_videos as cli
from resumen_videos import pipeline, video


def _args(*argv):
    return cli.crear_parser().parse_args(list(argv))


# ----------------------------------------------------------------------------- argumentos (H4)
def test_solo_no_se_traga_la_carpeta_posicional():
    a = _args("--solo", "arco parte 1", r"D:\grabaciones")
    assert a.carpeta == r"D:\grabaciones" and a.solo == ["arco parte 1"]
    a = _args("--solo", "a", "--solo", "b.mp4", "carpeta")
    assert a.carpeta == "carpeta" and a.solo == ["a", "b.mp4"]
    assert _args().solo is None and _args().carpeta == "videos"
    assert cli.opciones_desde_args(_args("--solo", "x")).solo == ["x"]


def test_batch_recoger_exige_id_y_batch_pendiente_es_bandera():
    a = _args("--batch-recoger", "1a2b3c", r"D:\grabaciones")
    assert a.carpeta == r"D:\grabaciones" and a.batch_recoger == "1a2b3c" and cli._modo(a) == "batch-recoger"
    assert cli.opciones_desde_args(a).lote_id == "1a2b3c"
    a = _args("--batch-pendiente", "--esperar")
    assert a.batch_recoger is None and a.batch_pendiente and cli._modo(a) == "batch-recoger"
    op = cli.opciones_desde_args(a)
    assert op.modo == "batch-recoger" and op.lote_id is None and op.esperar_lote
    with pytest.raises(SystemExit):
        _args("--batch-recoger")                        # sin ID ya no vale: antes se tragaba la carpeta
    with pytest.raises(SystemExit):
        _args("--batch-recoger", "x", "--batch-pendiente")
    with pytest.raises(SystemExit):
        _args("--batch-pendiente", "--simular")
    assert cli._modo(_args("--batch")) == "batch" and cli._modo(_args()) == "gemini"


def test_sin_tonemap_y_copia_alto():
    assert not _args().sin_tonemap and _args("--sin-tonemap").sin_tonemap
    assert _args("--copia-alto", "480").copia_alto == 480
    with pytest.raises(SystemExit):
        _args("--copia-alto", "600")


def test_ayuda_menciona_las_opciones_nuevas(capsys):
    with pytest.raises(SystemExit):
        _args("--help")
    ayuda = capsys.readouterr().out
    for opcion in ("--batch-recoger ID", "--batch-pendiente", "--solo NOMBRE", "--sin-tonemap", "lado menor",
                   "imageio-ffmpeg", "--solo A --solo B"):
        assert opcion in ayuda, opcion


# ----------------------------------------------------------------------------- comprobaciones previas
def test_comprobar_avisa_si_solo_o_el_lote_es_una_carpeta(tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "a.mp4").write_bytes(b"x")
    op = pipeline.Opciones(carpeta_videos=videos, carpeta_salida=tmp_path / "s", modo="simulado",
                           solo=[str(tmp_path)])
    mensaje = cli.comprobar(op, log=lambda _m: None)
    assert mensaje and "es una carpeta" in mensaje and "primer argumento" in mensaje
    op = pipeline.Opciones(carpeta_videos=videos, carpeta_salida=tmp_path / "s", modo="batch-recoger",
                           lote_id=str(tmp_path))
    mensaje = cli.comprobar(op, log=lambda _m: None)
    assert mensaje and "es una carpeta" in mensaje and "ID del lote" in mensaje


def test_comprobar_informa_ffmpeg_con_version_y_ffprobe_a_su_lado(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "a.mp4").write_bytes(b"x")
    try:
        ffmpeg = video.localizar_ffmpeg()
    except RuntimeError:
        pytest.skip("sin ffmpeg")
    monkeypatch.delenv("FFPROBE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "vacia"))
    carpeta = tmp_path / "ff con espacio"
    carpeta.mkdir()
    (carpeta / "ffprobe").write_text("", encoding="utf-8")
    enlace = carpeta / "ffmpeg"
    try:
        enlace.symlink_to(ffmpeg)
    except OSError:
        pytest.skip("no se pueden crear enlaces simbólicos")
    lineas: list = []
    op = pipeline.Opciones(carpeta_videos=videos, carpeta_salida=tmp_path / "s", modo="simulado", ffmpeg=str(enlace))
    assert cli.comprobar(op, log=lineas.append) is None
    linea = next(l for l in lineas if l.startswith("ffmpeg: "))
    assert str(enlace) in linea and "versión" in linea and f"ffprobe: {carpeta / 'ffprobe'}" in linea
    monkeypatch.setattr(video, "_ffmpeg_resuelto", None)


def test_sin_tonemap_desactiva_la_conversion(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(video, "TONEMAP_HDR", True)
    monkeypatch.setattr(cli, "_cargar_env", lambda: [])
    codigo = cli.main([str(tmp_path / "no-existe"), "--simular", "--sin-tonemap"])
    assert codigo == 2 and not video.TONEMAP_HDR
    salida = capsys.readouterr()
    assert "No existe la carpeta" in salida.err and "->" in salida.out and "→" not in salida.out


# ----------------------------------------------------------------------------- .env (H9)
def test_env_junto_al_script_manda_y_no_se_leen_carpetas_padre(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    raiz = tmp_path / "proyecto"
    trabajo = tmp_path / "trabajo" / "sub"
    raiz.mkdir()
    trabajo.mkdir(parents=True)
    (raiz / ".env").write_text("PRUEBA_RV_MODELO=del_script\nPRUEBA_RV_SOLO_SCRIPT=1\n", encoding="utf-8")
    (trabajo / ".env").write_text("PRUEBA_RV_MODELO=del_cwd\nPRUEBA_RV_SOLO_CWD=1\n", encoding="utf-8")
    (trabajo.parent / ".env").write_text("PRUEBA_RV_PADRE=1\n", encoding="utf-8")
    for variable in ("PRUEBA_RV_MODELO", "PRUEBA_RV_SOLO_SCRIPT", "PRUEBA_RV_SOLO_CWD", "PRUEBA_RV_PADRE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(cli, "RAIZ", raiz)
    monkeypatch.chdir(trabajo)
    leidos = cli._cargar_env()
    assert leidos == [(raiz / ".env").resolve(), (trabajo / ".env").resolve()]
    assert os.environ["PRUEBA_RV_MODELO"] == "del_script"          # el del script tiene prioridad
    assert os.environ["PRUEBA_RV_SOLO_SCRIPT"] == "1" and os.environ["PRUEBA_RV_SOLO_CWD"] == "1"
    assert "PRUEBA_RV_PADRE" not in os.environ                       # las carpetas padre no se recorren
    assert cli._avisos_env(leidos) == []
    for variable in ("PRUEBA_RV_MODELO", "PRUEBA_RV_SOLO_SCRIPT", "PRUEBA_RV_SOLO_CWD"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(raiz)
    assert cli._cargar_env() == [(raiz / ".env").resolve()]         # el mismo archivo no se lee dos veces


def test_avisos_env_txt_y_ejemplo(tmp_path, monkeypatch):
    raiz = tmp_path / "proyecto"
    raiz.mkdir()
    monkeypatch.setattr(cli, "RAIZ", raiz)
    monkeypatch.chdir(raiz)
    assert cli._avisos_env([]) == []
    (raiz / ".env.ejemplo").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    avisos = cli._avisos_env([])
    assert len(avisos) == 1 and ".env.ejemplo" in avisos[0] and "--simular" in avisos[0]
    (raiz / ".env.txt").write_text("GEMINI_API_KEY=x\n", encoding="utf-8")
    avisos = cli._avisos_env([])
    assert len(avisos) == 1 and ".env.txt" in avisos[0] and "sin .txt" in avisos[0]
    assert cli._avisos_env([Path("/algo/.env")]) == []
