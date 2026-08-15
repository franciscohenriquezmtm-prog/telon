# 🎭 Telón — cartelera de teatro de Santiago

Web app (PWA) con la cartelera **real** de teatro en Santiago de Chile: se alimenta de los
sitios oficiales de los teatros, con sus afiches, elencos, horarios y precios, y cada obra
enlaza a su ficha original. Pensada para el iPhone, funciona sin internet una vez cargada.

Sin frameworks ni dependencias: HTML + CSS + JavaScript y unos scripts de Node.

---

## Empezar

```bash
npm run actualizar   # baja la cartelera desde los teatros
npm start            # levanta el servidor local
```

`npm run actualizar` hace todo de una pasada:

1. lee los sitios de los teatros y arma `assets/cartelera.json`;
2. descarga los afiches nuevos a `assets/img/`;
3. genera sus miniaturas en `assets/img/mini/` (~12 KB cada una);
4. reconstruye `dist/telon.html` con **las fotos incrustadas dentro del archivo**.

Ese último punto importa: el archivo único se ve con fotos aunque se abra donde no se
permiten imágenes externas. Para saltarte el paso 4: `node tools/actualizar.mjs --sin-build`.

El servidor imprime dos direcciones: la de tu PC y la de tu iPhone (`http://192.168.x.x:5173`).
Abre esa segunda en **Safari** → **Compartir** → **Agregar a pantalla de inicio**.

---

## Que se actualice sola

Una página web no puede leer otros sitios desde el navegador (lo impide CORS), así que
el trabajo lo hace un script de Node que regenera `assets/cartelera.json`.

Para dejarlo automático en Windows:

```bash
powershell -ExecutionPolicy Bypass -File tools\programar-actualizacion.ps1
```

Queda una tarea programada que corre **todos los días a las 08:00** (y al iniciar sesión, si
el equipo estaba apagado). Para otra hora: `-Hora 09:30`. Para quitarla: `-Quitar`.

Si publicas la carpeta en un hosting (Netlify, GitHub Pages, Vercel), sube el
`assets/` regenerado y la app tomará la cartelera nueva sola: el service worker pide primero
a la red y usa la copia guardada solo si estás sin señal.

---

## De dónde salen los datos

Diez teatros, en ocho comunas:

| Fuente | Comuna | Cómo se lee | Qué entrega |
|---|---|---|---|
| [Centro GAM](https://www.gam.cl) | Santiago Centro | JSON-LD `schema.org/Event` de cada ficha + su cuerpo | Teatro, familiar y danza: fechas, sala, afiche, sinopsis, elenco, precios, link de venta |
| [Teatro UC](https://teatrouc.uc.cl) | Ñuñoa | API REST de *The Events Calendar* | Temporada, sala, afiche, extracto |
| [Teatro Mori](https://teatromori.cl) | Providencia, Recoleta, Las Condes, Vitacura | Cartelera y fichas de sus cuatro salas | Rango de fechas, días y horas, ficha artística, precios |
| [Matucana 100](https://www.m100.cl) | Estación Central | Fichas de `/programacion/teatro` y `/danza-circo` | Fechas, horarios por día, precios por tramo, espacio, duración, edad, ficha artística |
| [Teatro Nescafé de las Artes](https://www.teatro-nescafe-delasartes.cl) | Providencia | Tipo `evento` por `wp-json`, filtrado por sus categorías escénicas | Funciones, afiche destacado, sinopsis, link a Ticketmaster |
| [Centro para las Artes Zoco](https://teatrozoco.cl) | Lo Barnechea | Tipo `evento` por `wp-json` + bloque "Datos Generales" | Temporada, días y horas, ficha artística, recesos |
| [Teatro del Puente](https://teatrodelpuente.cl) | Santiago Centro | Calendario mensual, función por función | Fechas y horas exactas de cada función |
| [Teatro Ictus](https://teatroictus.cl) | Santiago Centro | Fichas de `/cartelera` | Fechas, horarios, ficha artística, afiche |
| [Teatro Finis Terrae](https://teatrofinisterrae.cl) | Providencia | API REST de *The Events Calendar* | Temporada, sala, afiche, sinopsis, precios |
| [Teatro Municipal de Las Condes](https://www.tmlascondes.cl) | Las Condes | JSON-LD `schema.org/Event` de cada post, descubiertos por el sitemap | Fecha, afiche, precio, elenco, género |

Varios de estos teatros programan además música y cine. Cuando el propio sitio rotula
la actividad (categorías en Nescafé, `genre` en Las Condes, la clave de color del
Puente), se usa ese rótulo para quedarse solo con las artes escénicas.

Los afiches se descargan a `assets/img/` para que la app funcione offline; en cada ficha se
acredita el teatro y se enlaza la página original. Son material de los teatros: la app los
muestra para orientar al público, no los reclama como propios.

**Importante:** los horarios cambian. La app siempre muestra el texto de horario tal como lo
publica el teatro y enlaza a la ficha oficial. Confirma antes de ir.

### Teatros revisados que quedaron fuera

Para no repetir el sondeo, esto es lo que se encontró al revisarlos (agosto de 2026):

| Teatro | Por qué no está |
|---|---|
| Teatro Nacional Chileno | `teatronacional.uchile.cl` no resuelve |
| Centro Arte Alameda | responde HTTP 403 a cualquier lectura automática |
| Espacio Diana | el dominio ya no es del teatro: `espaciodiana.cl` apunta a otro sitio |
| Teatro Ladrón de Bicicletas | el dominio se vendió; hoy es un sitio de casino |
| Teatro Mueller, NoLugar, Camilo Henríquez, Teatro Bellas Artes | sin dominio que responda |
| Teatro La Memoria | el sitio es una instalación de WordPress vacía |
| Teatro Cariola | la cartelera está delegada a un tercero, sin datos en el sitio |
| Teatro Oriente | usa The Events Calendar pero su API responde 401 |
| Corpartes (CA660) | la cartelera vive en Ticketplus, no en el sitio |
| Centro Cultural La Moneda, Municipal de Santiago | la cartelera se arma con JavaScript; no hay datos en el HTML |
| Teatro Camino, Estación Mapocho, Teatro Bellavista | quedan pendientes: son legibles, pero cada uno necesita su adaptador |

Los cuatro últimos son los candidatos más realistas para crecer.

### Cuidado con los afiches

No toda imagen de una ficha es el afiche: los sitios mezclan logos de la sala y de los
auspiciadores con la foto de la obra. En Mori, por ejemplo, el afiche está en
`/mini/<ancho>x<alto>xS/…` y los logos en `/archivos/originales/…`.

Por eso el actualizador tiene una salvaguarda: calcula la huella (SHA-1) de cada afiche
descargado y, **si el mismo archivo aparece en dos o más obras, lo descarta** — una imagen
repetida es un logo o un placeholder, no un afiche. Queda avisado en el informe y esas obras
muestran el póster de respaldo (degradado según género con el título).

### Qué se hace cuando un dato no está

- Si un teatro **no publica los días de función**, la app **no los inventa**: muestra el rango
  de temporada y el horario tal cual está escrito en el sitio.
- Si no hay precios publicados todavía, lo dice ("El teatro todavía no publica los precios").
- Si una fuente se cae, se conservan sus obras de la última actualización buena y queda
  anotado en la hoja de información de la app.

---

## Agregar otro teatro

Cada fuente es un archivo en `tools/fuentes/` que exporta `meta` y `obtener()`:

```js
export const meta = { id: 'sidarte', nombre: 'Teatro Sidarte', sitio: 'https://sidarte.cl' };

export async function obtener({ log }) {
  return [{
    titulo: 'Nombre de la obra',
    sinopsis: '…',
    genero: 'drama',                 // ver GENEROS en assets/app.js
    director: '…', elenco: ['…'],
    duracion: 90, edad: 14,          // null si el sitio no lo dice
    sala: resolverSala('sidarte'),   // ficha de tools/lib/salas.mjs
    temporada: { desde: '2026-08-07', hasta: '2026-09-20', dias: [4,5,6], horas: { 4: '19:30' } },
    horarioTexto: 'Ju a Sá — 19.30 h',
    precios: [{ tipo: 'General', valor: 12000 }],
    imagen: 'https://…/afiche.jpg',
    entradas: 'https://…', url: 'https://…',
  }];
}
```

Después agrégalo a la lista `FUENTES` de `tools/actualizar.mjs` y su sala a `tools/lib/salas.mjs`.

**Si el teatro usa WordPress con *The Events Calendar*** (pruébalo pidiendo
`/wp-json/tribe/events/v1/events`), no escribas nada: el lector ya está hecho.

```js
import { leerEventsCalendar } from '../lib/tribe.mjs';

export const meta = { id: 'sidarte', nombre: 'Teatro Sidarte', sitio: 'https://sidarte.cl' };

export const obtener = ({ log }) => leerEventsCalendar({
  api: 'https://sidarte.cl/wp-json/tribe/events/v1/events',
  idFuente: 'sidarte', sala: 'sidarte', log,
});
```

`tools/lib/util.mjs` trae lo pesado ya resuelto: `parseHorarios()` entiende "Ju a Sá— 19.30 h" o
"De jueves a sábado a las 20:00 horas"; `parseRangoFechas()` entiende "Del 9 de julio al 1 de agosto",
"27 de agosto al 11 de octubre" y "Del 06 al 16/08"; `parseFechasEnumeradas()` entiende
"25 y 26 de agosto"; además hay `parsePrecios()`, `parseDuracion()`, `parseEdad()`, `jsonLD()`
y `bajar()` con reintentos.

Un par de trampas que ya están cubiertas y conviene no reintroducir: **un precio parece una
hora** (`$7.500` encaja igual que `19.30 h`), **un teléfono parece una edad** (`+56 9…` no es
"+56 años"), y **el horario de boletería no es el horario de función**.

Al agregar una fuente, corre `node tools/actualizar.mjs --fuente <id>` para probar solo esa.

---

## Qué trae la app

| | |
|---|---|
| **Prioridad por fecha** | Todo se ordena por la próxima función. Chips de Hoy / Mañana / Este finde / 7 días / Gratis. |
| **Agenda** | Los próximos 21 días, hora por hora. |
| **Ficha** | Afiche del teatro, sinopsis, género, duración, edad, dirección, dramaturgia, elenco y compañía. |
| **Precios** | Todos los tramos publicados por el teatro. |
| **Filtros** | Género, comuna, precio máximo, duración, gratis, apta para niños. |
| **Búsqueda** | Por obra, actriz o actor, director, compañía, sala o comuna. |
| **Guardadas** | Corazón en cada obra; persiste en el teléfono. |
| **Al calendario** | Cada función genera un `.ics` con sala, dirección y alarma 2 h antes. |
| **Cómo llegar** | Abre la sala en Mapas. |
| **Sin internet** | Service worker: la cartelera y los afiches quedan guardados. |

---

## Estructura

```
index.html                     estructura y hojas modales
assets/styles.css              diseño (rojo telón + oro, modo oscuro)
assets/app.js                  lógica: temporadas, filtros, favoritos, .ics
assets/cartelera.json          ← la cartelera real (la genera el actualizador)
assets/img/                    ← afiches descargados de cada teatro
manifest.webmanifest, sw.js    instalación e funcionamiento sin internet

assets/img/mini/               miniaturas para incrustar en el archivo único

tools/actualizar.mjs           orquesta todo: fuentes → afiches → miniaturas → build
tools/fuentes/*.mjs            un adaptador por teatro
tools/lib/util.mjs             parseo de fechas, horarios, precios; descarga con reintentos
tools/lib/tribe.mjs            lector compartido de The Events Calendar (Teatro UC, Finis Terrae)
tools/lib/salas.mjs            direcciones, comunas y metro de cada sala
tools/lib/miniaturas.mjs       reduce los afiches (Windows/macOS/Linux)
tools/miniaturas.ps1           el redimensionado en Windows, con System.Drawing
tools/server.mjs               servidor local para probar en el teléfono
tools/build-single.mjs         empaqueta todo —fotos incluidas— en dist/telon.html
tools/make-icons.mjs           regenera los íconos PNG
tools/programar-actualizacion.ps1   tarea diaria en Windows
```

## Comandos

```bash
npm run actualizar                        # cartelera + afiches + miniaturas + build
node tools/actualizar.mjs --fuente gam    # solo una fuente
node tools/actualizar.mjs --sin-imagenes  # sin descargar afiches (más rápido)
node tools/actualizar.mjs --sin-build     # sin regenerar dist/telon.html
npm run build                             # solo el archivo único
npm start                                 # servidor local
```

### Ajustar el peso de las fotos

Las miniaturas salen a 480 px de ancho y calidad 68 (~25 KB cada una; 83 afiches ≈ 2,1 MB
incrustados, y `dist/telon.html` pesa unos 3 MB). Si quieres el archivo único más liviano,
baja el ancho y la calidad:

```bash
powershell -ExecutionPolicy Bypass -File tools\miniaturas.ps1 -Ancho 640 -Calidad 78
npm run build
```
