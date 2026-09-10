#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Construye la página publicada a partir de la fuente.

    python3 fuente/construir.py

Lee  fuente/tablero.html  (el tablero: <title>, <style>, marcado y <script>)
y escribe, en la carpeta de arriba:

    index.html            el documento completo, con <head> y datos dentro
    manifest.webmanifest  para instalarla en el teléfono
    icon.svg              el ícono
    sw.js                 para que funcione sin señal

Todo lo que edites va en fuente/tablero.html; index.html se regenera y no
conviene tocarlo a mano. Cada construcción estampa la fecha: si un teléfono
tiene guardada una versión más vieja, gana la página, así una corrección llega
sola a todos los aparatos.

Publicar: hacer commit y push. El workflow .github/workflows/actualizar.yml
copia esta carpeta al sitio y GitHub Pages la sirve en pocos minutos.
"""
import io
import json
import os
import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.dirname(AQUI)
FUENTE = os.path.join(AQUI, 'tablero.html')
SELLO = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

s = io.open(FUENTE, encoding='utf-8').read()


def rep(a, b):
    global s
    assert a in s, 'No se encontró en tablero.html: ' + a[:80]
    s = s.replace(a, b, 1)


# ---------- sello de publicación ----------
rep("""    techo:30, nuevo:15, bloque:10, margen:15, postergar:null,
    reg: SEMILLA.slice()
  };""",
    """    techo:30, nuevo:15, bloque:10, margen:15, postergar:null,
    sello:"%s",
    reg: SEMILLA.slice()
  };""" % SELLO)

# ---------- la página manda cuando trae una publicación más nueva ----------
rep('''  function loadLocal(){
    try{ var raw=localStorage.getItem("dias2026v3"); if(raw) return Object.assign(clone(DEFAULTS), JSON.parse(raw)); }catch(e){}
    return clone(DEFAULTS);
  }
  function saveLocal(){ try{ localStorage.setItem("dias2026v3", JSON.stringify(state)); }catch(e){} }''',
    '''  /* Lo que marcas queda guardado en este aparato. Pero si la página trae una
     publicación más nueva que la guardada aquí, gana la página: así una
     corrección llega sola a todos los aparatos, sin cargar nada a mano. */
  function loadLocal(){
    try{
      var raw=localStorage.getItem("dias2026v4");
      if(raw){
        var g=JSON.parse(raw);
        if(g && g.sello===DEFAULTS.sello) return Object.assign(clone(DEFAULTS), g);
      }
    }catch(e){}
    return clone(DEFAULTS);
  }
  function saveLocal(){ try{ localStorage.setItem("dias2026v4", JSON.stringify(state)); }catch(e){} }''')

# ---------- fuera el bloque que solo servía dentro de Claude ----------
i = s.index('  /* ---------- sincronización entre dispositivos ---------- */')
j = s.rindex('})();')
s = s[:i] + '''  /* ---------- funcionar sin señal ---------- */
  if("serviceWorker" in navigator){
    window.addEventListener("load", function(){
      navigator.serviceWorker.register("./sw.js").catch(function(){});
    });
  }

  $("sello").textContent = DEFAULTS.sello;

''' + s[j:]

# ---------- pie de página ----------
rep('<span id="sync-t">Guardado en este dispositivo</span>',
    '<span id="sync-t">Publicado el <span class="mono" id="sello">—</span> · se actualiza solo</span>')
rep('.sync .d2{width:7px; height:7px; border-radius:50%; background:var(--line-strong); flex:none}',
    '.sync .d2{width:7px; height:7px; border-radius:50%; background:var(--safe); flex:none}')
rep('<div>Los saldos salen del registro de días. Los cinco movimientos iniciales vienen de tus pantallas de Autoatención del 10-09-2026.</div>',
    '<div>Este mismo enlace se mantiene al día: lo que corrija aparece la próxima vez que lo abras, en el computador y en el teléfono. Lo que marques queda guardado en el aparato donde lo marcaste.</div>')

# ---------- documento completo ----------
doc = '''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta name="description" content="Control de feriado legal y permisos administrativos segun el Estatuto Administrativo.">
<meta name="theme-color" content="#0B6B62" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#091211" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Feriado">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon.svg">
<style>
  html{color-scheme:light dark}
  body{margin:0; font:14px system-ui, sans-serif}
  img{max-width:100%}
  [hidden]{display:none!important}
</style>
''' + s + '''
</body>
</html>
'''
doc = doc.replace('</style>\n<title>', '</style>\n</head>\n<body>\n<title>', 1)
doc = doc.replace('</head>\n<body>\n<title>Feriado y Permisos 2026</title>',
                  '<title>Feriado y permisos · control de días</title>\n</head>\n<body>')

# Las hojas de estilo de las fuentes van en el <head>, no sueltas en el <body>.
_fuentes = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700'
    '&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">\n'
)
assert _fuentes in doc, 'no se encontraron los links de fuentes'
doc = doc.replace(_fuentes, '', 1)
doc = doc.replace('<link rel="manifest"', _fuentes + '<link rel="manifest"', 1)

io.open(os.path.join(DESTINO, 'index.html'), 'w', encoding='utf-8').write(doc)

# ---------- manifest ----------
manifest = {
    "name": "Feriado y permisos",
    "short_name": "Feriado",
    "description": "Control de feriado legal y permisos administrativos.",
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "background_color": "#EDF1F0",
    "theme_color": "#0B6B62",
    "lang": "es-CL",
    "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}]
}
io.open(os.path.join(DESTINO, 'manifest.webmanifest'), 'w', encoding='utf-8').write(
    json.dumps(manifest, ensure_ascii=False, indent=2))

# ---------- ícono ----------
icon = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <rect width="192" height="192" rx="42" fill="#0B6B62"/>
  <rect x="38" y="46" width="116" height="108" rx="12" fill="#EDF1F0"/>
  <rect x="38" y="46" width="116" height="26" rx="12" fill="#08514A"/>
  <rect x="62" y="34" width="12" height="26" rx="6" fill="#08514A"/>
  <rect x="118" y="34" width="12" height="26" rx="6" fill="#08514A"/>
  <rect x="56" y="88" width="20" height="18" rx="4" fill="#B6C6C3"/>
  <rect x="86" y="88" width="20" height="18" rx="4" fill="#B6C6C3"/>
  <rect x="116" y="88" width="20" height="18" rx="4" fill="#A93A14"/>
  <rect x="56" y="116" width="20" height="18" rx="4" fill="#B6C6C3"/>
  <rect x="86" y="116" width="20" height="18" rx="4" fill="#0B6B62"/>
  <rect x="116" y="116" width="20" height="18" rx="4" fill="#B6C6C3"/>
</svg>
'''
io.open(os.path.join(DESTINO, 'icon.svg'), 'w', encoding='utf-8').write(icon)

# ---------- service worker ----------
sw = '''/* Deja la página funcionando sin señal, pero sin quedarse pegada en una
   versión vieja: la página se pide siempre al servidor y el caché solo entra
   cuando no hay red. */
const CACHE = "feriado-%s";
const BASE = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(BASE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const esPagina = e.request.mode === "navigate";
  e.respondWith(
    fetch(esPagina ? new Request(e.request, { cache: "reload" }) : e.request)
      .then((r) => {
        const copia = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copia)).catch(() => {});
        return r;
      })
      .catch(() => caches.match(e.request).then((m) => m || caches.match("./index.html")))
  );
});
''' % SELLO.replace(' ', '-').replace(':', '')
io.open(os.path.join(DESTINO, 'sw.js'), 'w', encoding='utf-8').write(sw)

print('Construido con sello', SELLO)
print('index.html:', len(doc), 'caracteres')
print('Ahora: git add feriado && git commit && git push')
