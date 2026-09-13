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


def a_variable(nombre, desde_uuid):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
            "WFWorkflowActionParameters": {
                "WFVariableName": nombre,
                "WFInput": adjunto(salida(desde_uuid, "Text"))}}


def a_preguntar(prompt, tipo, uid):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.ask",
            "WFWorkflowActionParameters": {
                "UUID": uid, "WFAskActionPrompt": prompt, "WFInputType": tipo}}


def a_formatear_fecha(desde_uuid, uid):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.format.date",
            "WFWorkflowActionParameters": {
                "UUID": uid,
                "WFDateFormatStyle": "Custom",
                "WFDateFormat": "yyyy-MM-dd",
                "WFDate": adjunto(salida(desde_uuid, "Provided Input"))}}


def a_abrir_url(desde_uuid):
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
            "WFWorkflowActionParameters": {
                "WFInput": adjunto(salida(desde_uuid, "Text"))}}


def a_evento(desde_uuid, hasta_uuid):
    """Evento de día completo en el calendario del iPhone, del primer al
    último día del período. Se crea directo en el calendario por defecto,
    sin hoja de confirmación."""
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.addnewevent",
            "WFWorkflowActionParameters": {
                "WFCalendarItemTitle": texto_con([variable("Titulo")]),
                "WFCalendarItemStartDate": texto_con([salida(desde_uuid, "Provided Input")]),
                "WFCalendarItemEndDate": texto_con([salida(hasta_uuid, "Provided Input")]),
                "WFCalendarItemAllDay": True,
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


def si_titulo_no_es(marca, dentro):
    """Ejecuta un bloque solo si la variable Titulo no es la marca dada."""
    gid = nuevo_uuid()
    return [{"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
             "WFWorkflowActionParameters": {
                 "GroupingIdentifier": gid, "WFControlFlowMode": 0,
                 "WFInput": {"Type": "Variable",
                             "Variable": {"Value": {"Type": "Variable", "VariableName": "Titulo"},
                                          "WFSerializationType": "WFTextTokenAttachment"}},
                 "WFCondition": 5,
                 "WFConditionalActionString": marca}}] + dentro + [
            {"WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
             "WFWorkflowActionParameters": {
                 "GroupingIdentifier": gid, "WFControlFlowMode": 2}}]


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
    medio = rama_con_variables("bolsa=permiso&frac=0.5") + menu("¿Mañana o tarde?", [
        ("Mañana (½ AM)", rama_titulo("Permiso administrativo (½ AM)")),
        ("Tarde (½ PM)", rama_titulo("Permiso administrativo (½ PM)")),
    ])
    ramas = [
        ("Permiso administrativo completo", rama_con_variables("bolsa=permiso&frac=1", "Permiso administrativo")),
        ("Medio permiso administrativo", medio),
        ("Feriado 2026", rama_con_variables("bolsa=vigente&frac=1", "Feriado legal")),
        ("Acumulado 2025", rama_con_variables("bolsa=acum25&frac=1", "Feriado legal (acumulado)")),
        ("Bolsa 2022", rama_con_variables("bolsa=b2022&frac=1", "Feriado legal (bolsa 2022)")),
    ]
    ramas.append(("Borrar días marcados", rama_con_variables("quitar=1", "×")))
    u_desde, u_fmt_d, u_hasta, u_fmt_h, u_url = (nuevo_uuid() for _ in range(5))
    tramo = menu("¿Qué te tomaste?", ramas) + [
        a_preguntar("¿Desde qué día?", "Date", u_desde),
        a_formatear_fecha(u_desde, u_fmt_d),
        a_preguntar("¿Hasta qué día? (si es uno solo, la misma fecha)", "Date", u_hasta),
        a_formatear_fecha(u_hasta, u_fmt_h),
        a_texto(texto_con([BASE + "?desde=", salida(u_fmt_d, "Formatted Date"),
                           "&hasta=", salida(u_fmt_h, "Formatted Date"),
                           "&", variable("Parametros")]), u_url),
        a_abrir_url(u_url),
    ] + si_titulo_no_es("×", [a_evento(u_desde, u_hasta)]) + menu("¿Marcar otro tramo?", [
        ("Sí, otro tramo", []),
        ("No, listo", [a_terminar()]),
    ])
    # tope de 10 tramos por ejecución; el «No, listo» corta antes
    return envuelve(repetir(10, tramo), 431817727, 61553)


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
