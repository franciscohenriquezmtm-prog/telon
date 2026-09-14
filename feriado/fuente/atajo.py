#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genera el atajo de iPhone del tablero.

    python3 fuente/atajo.py

Escribe y firma con la herramienta de Apple que trae macOS:

    Día libre.shortcut

Un solo atajo para todo: pregunta qué te tomaste (bolsa y fracción), desde
qué día y hasta qué día. Un día suelto es desde = hasta; un período con el
18 de septiembre o un fin de semana de por medio se pide corrido y el tablero
marca solo los hábiles. Al final ofrece agendar el período en el calendario
del iPhone y pregunta si quieres marcar otro tramo: así se encadenan las
mezclas (4 de feriado + 1 administrativo, ½ AM, etc.) sin volver a abrirlo.
El medio día pregunta ¿mañana o tarde? solo para el título del evento; el
descuento es medio día igual.

El firmado queda listo para mandar por AirDrop al iPhone. Si la firma falla
(p. ej. sin sesión de iCloud), queda el archivo «(sin firmar)» y el mensaje
dice cómo firmarlo a mano:

    shortcuts sign --mode anyone --input entrada.shortcut --output salida.shortcut
"""
import os
import plistlib
import subprocess
import sys
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.dirname(AQUI)
BASE = "https://franciscohenriquezmtm-prog.github.io/telon/feriado/"

OBJ = "￼"  # marcador de posición de una variable dentro de un texto


def nuevo_uuid():
    return str(uuid.uuid4()).upper()


def texto_plano(s):
    """Un parámetro de texto sin variables."""
    return {"Value": {"string": s}, "WFSerializationType": "WFTextTokenString"}


def salida(accion_uuid, nombre):
    """Referencia a la salida de una acción anterior."""
    return {"OutputUUID": accion_uuid, "OutputName": nombre, "Type": "ActionOutput"}


def variable(nombre):
    return {"VariableName": nombre, "Type": "Variable"}


def texto_con(partes):
    """Texto interpolado: las cadenas van tal cual, los dicts son variables."""
    s, attachs = "", {}
    for p in partes:
        if isinstance(p, str):
            s += p
        else:
            attachs["{%d, 1}" % len(s)] = p
            s += OBJ
    return {"Value": {"string": s, "attachmentsByRange": attachs},
            "WFSerializationType": "WFTextTokenString"}


def adjunto(ref):
    return {"Value": ref, "WFSerializationType": "WFTextTokenAttachment"}


def a_texto(contenido, uid):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
            "WFWorkflowActionParameters": {"UUID": uid, "WFTextActionText": contenido}}


def a_variable(nombre, desde_uuid=None, salida_nombre="Text"):
    """Guarda en una variable. Sin desde_uuid toma la salida de la acción
    anterior (entrada automática, como lo hace la propia app)."""
    p = {"WFVariableName": nombre}
    if desde_uuid:
        p["WFInput"] = adjunto(salida(desde_uuid, salida_nombre))
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
            "WFWorkflowActionParameters": p}


def a_preguntar(prompt, tipo, uid):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.ask",
            "WFWorkflowActionParameters": {
                "UUID": uid, "WFAskActionPrompt": prompt, "WFInputType": tipo}}


def a_formatear_fecha(uid, desde_uuid):
    """Formatea a AAAA-MM-DD la fecha de una pregunta anterior.

    Ojo con el cableado: probado en la práctica, esta acción IGNORA tanto la
    entrada automática como la referencia cruda (WFTextTokenAttachment); solo
    funciona con la fecha incrustada en un texto (WFTextTokenString)."""
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.format.date",
            "WFWorkflowActionParameters": {
                "UUID": uid,
                "WFDateFormatStyle": "Custom",
                "WFDateFormat": "yyyy-MM-dd",
                "WFDate": texto_con([salida(desde_uuid, "Ask for Input")])}}


def a_abrir_url(desde_uuid):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
            "WFWorkflowActionParameters": {
                "WFInput": adjunto(salida(desde_uuid, "Text"))}}


def a_portapapeles(desde_uuid):
    """Copia el texto al portapapeles (para poder inspeccionar la URL)."""
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.setclipboard",
            "WFWorkflowActionParameters": {
                "WFInput": adjunto(salida(desde_uuid, "Text"))}}


# Calendario de Google (cuenta franciscohenriquezmtm@gmail.com) donde van los
# eventos. Probado: el selector solo funciona con el nombre como texto simple;
# como dict WFCalendarDescriptor se ignora y el evento cae al por defecto.
CALENDARIO = "Vacaciones, permisos, feriados, etc"


def a_evento(desde_uuid, hasta_uuid):
    """Evento de día completo, del primer al último día del período, creado
    directo (sin hoja de confirmación) en el calendario CALENDARIO."""
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.addnewevent",
            "WFWorkflowActionParameters": {
                "WFCalendarItemTitle": texto_con([variable("Titulo")]),
                "WFCalendarItemStartDate": texto_con([salida(desde_uuid, "Ask for Input")]),
                "WFCalendarItemEndDate": texto_con([salida(hasta_uuid, "Ask for Input")]),
                "WFCalendarItemAllDay": True,
                "WFCalendarDescriptor": CALENDARIO,
                "ShowWhenRun": False}}


def menu(prompt, ramas):
    """ramas: lista de (título, [acciones de esa rama])."""
    gid = nuevo_uuid()
    acciones = [{"WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                 "WFWorkflowActionParameters": {
                     "GroupingIdentifier": gid, "WFControlFlowMode": 0,
                     "WFMenuPrompt": prompt,
                     "WFMenuItems": [t for t, _ in ramas]}}]
    for titulo, dentro in ramas:
        acciones.append({"WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                         "WFWorkflowActionParameters": {
                             "GroupingIdentifier": gid, "WFControlFlowMode": 1,
                             "WFMenuItemTitle": titulo}})
        acciones.extend(dentro)
    acciones.append({"WFWorkflowActionIdentifier": "is.workflow.actions.choosefrommenu",
                     "WFWorkflowActionParameters": {
                         "GroupingIdentifier": gid, "WFControlFlowMode": 2}})
    return acciones


def rama_con_variables(parametros, titulo_evento=None):
    """Texto→variable Parametros y, si corresponde, Texto→variable Titulo."""
    acciones, u1 = [], nuevo_uuid()
    acciones.append(a_texto(texto_plano(parametros), u1))
    acciones.append(a_variable("Parametros", u1))
    if titulo_evento is not None:
        acciones.extend(rama_titulo(titulo_evento))
    return acciones


def rama_titulo(titulo_evento):
    """Solo Texto→variable Titulo (para menús anidados)."""
    u = nuevo_uuid()
    return [a_texto(texto_plano(titulo_evento), u), a_variable("Titulo", u)]


def repetir(veces, dentro):
    """Repite un bloque una cantidad fija de veces."""
    gid = nuevo_uuid()
    return [{"WFWorkflowActionIdentifier": "is.workflow.actions.repeat.count",
             "WFWorkflowActionParameters": {
                 "GroupingIdentifier": gid, "WFControlFlowMode": 0,
                 "WFRepeatCount": veces}}] + dentro + [
            {"WFWorkflowActionIdentifier": "is.workflow.actions.repeat.count",
             "WFWorkflowActionParameters": {
                 "GroupingIdentifier": gid, "WFControlFlowMode": 2}}]


def a_terminar():
    """Detiene el atajo (el «No, listo» del ciclo de tramos)."""
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.exit",
            "WFWorkflowActionParameters": {}}


def si_var_no_es(nombre_var, marca, dentro, sino=None):
    """Ejecuta un bloque si la variable no es la marca dada; si lo es,
    ejecuta el bloque «sino» (la rama De lo contrario)."""
    gid = nuevo_uuid()
    acciones = [{"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                 "WFWorkflowActionParameters": {
                     "GroupingIdentifier": gid, "WFControlFlowMode": 0,
                     "WFInput": {"Type": "Variable",
                                 "Variable": {"Value": {"Type": "Variable", "VariableName": nombre_var},
                                              "WFSerializationType": "WFTextTokenAttachment"}},
                     "WFCondition": 5,
                     "WFConditionalActionString": marca}}] + dentro
    if sino:
        acciones.append({"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                         "WFWorkflowActionParameters": {
                             "GroupingIdentifier": gid, "WFControlFlowMode": 1}})
        acciones.extend(sino)
    acciones.append({"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                     "WFWorkflowActionParameters": {
                         "GroupingIdentifier": gid, "WFControlFlowMode": 2}})
    return acciones


def envuelve(acciones, color, glifo):
    return {
        "WFWorkflowClientVersion": "1230.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": color,
                           "WFWorkflowIconGlyphNumber": glifo},
        "WFWorkflowTypes": [],
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowActions": acciones,
    }


# ---------- el atajo · Día libre ----------
def dia_libre():
    # el medio permiso pregunta mañana o tarde; el descuento es ½ día igual,
    # la diferencia queda solo en el título del evento del calendario
    # Todos los eventos de calendario se crean ANTES de abrir Safari: probado
    # en el iPhone, un evento creado después de un cambio de app pierde el
    # calendario destino y cae al por defecto. Por eso cada tramo solo junta
    # su pedazo de URL (&t=bolsa,frac,desde,hasta) y la página se abre UNA
    # vez al final con todos los tramos.
    u_si, u_hq0, u_base, u_hq, u_desde, u_fmt_d, u_hasta, u_fmt_h, \
        u_acc, u_no, u_fin, u_cal = (nuevo_uuid() for _ in range(12))

    medio = rama_con_variables("permiso,0.5") + menu("¿Mañana o tarde?", [
        ("Mañana (½ AM)", rama_titulo("Permiso administrativo (½ AM)")),
        ("Tarde (½ PM)", rama_titulo("Permiso administrativo (½ PM)")),
    ])
    ramas = [
        ("Permiso administrativo completo", rama_con_variables("permiso,1", "Permiso administrativo")),
        ("Medio permiso administrativo", medio),
        ("Feriado 2026", rama_con_variables("vigente,1", "Feriado legal")),
        ("Acumulado 2025", rama_con_variables("acum25,1", "Feriado legal (acumulado)")),
        ("Bolsa 2022", rama_con_variables("b2022,1", "Feriado legal (bolsa 2022)")),
        ("Cancelar días", rama_con_variables("quitar,1", "×") + [
            a_texto(texto_plano("si"), u_hq), a_variable("HuboQuitar", u_hq)]),
    ]

    tramo = menu("¿Qué te tomaste?", ramas) + [
        a_preguntar("¿Desde qué día?", "Date", u_desde),
        a_formatear_fecha(u_fmt_d, u_desde),
        a_variable("FechaDesde", u_fmt_d, "Formatted Date"),
        a_preguntar("¿Hasta qué día? (si es uno solo, la misma fecha)", "Date", u_hasta),
        a_formatear_fecha(u_fmt_h, u_hasta),
        a_variable("FechaHasta", u_fmt_h, "Formatted Date"),
        a_texto(texto_con([variable("URL"), "&t=", variable("Parametros"),
                           ",", variable("FechaDesde"), ",", variable("FechaHasta")]), u_acc),
        a_variable("URL", u_acc),
    ] + si_var_no_es("Titulo", "×", [a_evento(u_desde, u_hasta)]) \
      + menu("¿Marcar otro tramo?", [
        ("Sí, otro tramo", []),
        ("No, listo", [a_texto(texto_plano("no"), u_no), a_variable("Seguir", u_no)]),
    ])

    acciones = [
        a_texto(texto_plano("si"), u_si), a_variable("Seguir", u_si),
        a_texto(texto_plano("no"), u_hq0), a_variable("HuboQuitar", u_hq0),
        a_texto(texto_plano(BASE + "?m=1"), u_base), a_variable("URL", u_base),
    ] + repetir(10, si_var_no_es("Seguir", "no", tramo)) + [
        a_texto(texto_con([variable("URL")]), u_fin),
        a_portapapeles(u_fin),
        a_abrir_url(u_fin),
    ] + si_var_no_es("HuboQuitar", "no", [
        a_texto(texto_plano("calshow:"), u_cal), a_abrir_url(u_cal),
    ])
    return envuelve(acciones, 431817727, 61553)


def escribe_y_firma(nombre, flujo):
    # la herramienta de firma exige que la entrada también termine en .shortcut
    plano = os.path.join(DESTINO, nombre + " (sin firmar).shortcut")
    firmado = os.path.join(DESTINO, nombre + ".shortcut")
    with open(plano, "wb") as f:
        plistlib.dump(flujo, f)
    r = subprocess.run(
        ["shortcuts", "sign", "--mode", "anyone",
         "--input", plano, "--output", firmado],
        capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(firmado):
        os.remove(plano)
        print("✓", nombre + ".shortcut  (firmado, listo para AirDrop)")
        return True
    print("✗ no se pudo firmar", nombre, "—", (r.stderr or r.stdout).strip())
    print("  Quedó", plano, "· fírmalo con:")
    print("  shortcuts sign --mode anyone --input '%s' --output '%s'" % (plano, firmado))
    return False


if __name__ == "__main__":
    sys.exit(0 if escribe_y_firma("Día libre", dia_libre()) else 1)
