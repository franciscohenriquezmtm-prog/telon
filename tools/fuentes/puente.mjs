/* Fuente: Teatro del Puente — https://teatrodelpuente.cl

   No tiene ficha por obra: publica un calendario mensual donde cada día lista sus
   funciones con la hora. Eso es más preciso que un rango de temporada, así que la
   temporada se reconstruye desde las funciones reales: se leen los próximos meses,
   se agrupan las funciones por obra y de ahí salen el rango, los días y las horas.

   Como el calendario no trae sinopsis ni elenco, esas obras quedan sin esos datos:
   la app lo muestra tal cual antes que rellenarlo. */

import { bajar, aTexto, iso, slug, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';

export const meta = { id: 'puente', nombre: 'Teatro del Puente', sitio: 'https://teatrodelpuente.cl' };

const MESES_URL = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const MESES_A_LEER = 4;

const desescapar = (s) => String(s)
  .replace(/&aacute;/gi, 'á').replace(/&eacute;/gi, 'é').replace(/&iacute;/gi, 'í')
  .replace(/&oacute;/gi, 'ó').replace(/&uacute;/gi, 'ú').replace(/&ntilde;/gi, 'ñ')
  .replace(/&amp;/gi, '&').replace(/&nbsp;/gi, ' ').replace(/&#8217;|&#039;|&#39;/gi, "'")
  .replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();

/** Funciones de un mes: [{ fecha ISO, titulo, hora }] */
function funcionesDelMes(html, anio, mes) {
  const funciones = [];
  for (const celda of html.matchAll(/<td[^>]*class="[^"]*day-with-date[^"]*"[^>]*>([\s\S]*?)<\/td>/gi)) {
    const cuerpo = celda[1];
    const dia = Number((cuerpo.match(/<span[^>]*>\s*(\d{1,2})\s*<\/span>/) || [])[1]);
    if (!dia) continue;

    // Cada función es un <a> con su título y su hora
    for (const enlace of cuerpo.matchAll(/<a[^>]*>([\s\S]*?)<\/a>/gi)) {
      const titulo = desescapar((enlace[1].match(/class="event-title"[^>]*>([\s\S]*?)<\/span>/) || [])[1] || '');
      if (!titulo) continue;
      const hora = (enlace[1].match(/Hora:\s*<\/strong>\s*(\d{1,2}[:.]\d{2})/i) || [])[1];
      funciones.push({
        fecha: iso(new Date(anio, mes, dia)),
        titulo,
        hora: hora ? hora.replace('.', ':').padStart(5, '0') : null,
      });
    }
  }
  return funciones;
}

/**
 * El calendario corta los títulos a 30 caracteres ("Epistolario para una Futurolog").
 * La portada sí los publica enteros y con su categoría, así que se usa para
 * completarlos: se empareja cuando el título cortado es el comienzo del completo.
 */
async function tarjetasDePortada(log) {
  try {
    const lineas = aTexto(await bajar('https://teatrodelpuente.cl/')).split('\n');
    const tarjetas = [];
    lineas.forEach((linea, i) => {
      if (!/^cartelera\b/i.test(linea) || !linea.includes(' I ')) return;
      const titulo = (lineas[i + 1] || '').trim();
      if (titulo && titulo.length > 2) tarjetas.push({ titulo, categoria: linea });
    });
    return tarjetas;
  } catch (e) {
    log(`  ! no se pudo leer la portada para los títulos completos: ${e.message}`);
    return [];
  }
}

const normalizar = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '')
  .toLowerCase().replace(/\s+/g, ' ').trim();

export async function obtener({ log }) {
  const hoy = new Date();
  const tarjetas = await tarjetasDePortada(log);
  const todas = [];

  for (let i = 0; i < MESES_A_LEER; i++) {
    const d = new Date(hoy.getFullYear(), hoy.getMonth() + i, 1);
    const url = `https://teatrodelpuente.cl/cartelera/?calendar_month=${MESES_URL[d.getMonth()]}&calendar_yr=${d.getFullYear()}`;
    try {
      const encontradas = funcionesDelMes(await bajar(url), d.getFullYear(), d.getMonth());
      todas.push(...encontradas);
      log(`  · ${MESES_URL[d.getMonth()]} ${d.getFullYear()}: ${encontradas.length} funciones`);
    } catch (e) {
      log(`  ! no se pudo leer ${MESES_URL[d.getMonth()]} ${d.getFullYear()}: ${e.message}`);
    }
  }

  // Agrupamos por obra
  const porObra = new Map();
  for (const f of todas) {
    const clave = f.titulo.toLowerCase();
    if (!porObra.has(clave)) porObra.set(clave, { titulo: f.titulo, funciones: [] });
    porObra.get(clave).funciones.push(f);
  }

  const obras = [];
  for (const { titulo: tituloCalendario, funciones } of porObra.values()) {
    const tarjeta = tarjetas.find((t) => normalizar(t.titulo).startsWith(normalizar(tituloCalendario)));
    // El calendario corta a 30 caracteres exactos. Si la portada no lo tenía (solo
    // muestra lo de la semana), se marca con "…" en vez de darlo por completo.
    const cortado = !tarjeta && tituloCalendario.length === 30;
    const titulo = tarjeta ? tarjeta.titulo : tituloCalendario + (cortado ? '…' : '');
    if (tarjeta && tarjeta.titulo !== tituloCalendario) log(`  · título completado desde la portada: “${tituloCalendario}” → “${titulo}”`);
    if (cortado) log(`  · “${tituloCalendario}”: el calendario lo publica cortado`);

    const fechas = [...new Set(funciones.map((f) => f.fecha))].sort();
    const dias = new Set();
    const horas = {};
    for (const f of funciones) {
      const d = new Date(f.fecha + 'T00:00:00').getDay();
      dias.add(d);
      if (f.hora && !horas[d]) horas[d] = f.hora;
    }
    const conHora = [...new Set(funciones.map((f) => f.hora).filter(Boolean))];

    obras.push({
      id: `puente-${slug(titulo)}`,
      titulo,
      subtitulo: null,
      sinopsis: '',
      // La portada rotula cada tarjeta ("Cartelera I Música I Presencial"): esa etiqueta
      // manda sobre la heurística, que solo mira el título.
      genero: /\bm[úu]sica\b/i.test(tarjeta?.categoria || '') ? 'musical' : inferirGenero(titulo),
      compania: null, director: null, dramaturgo: null, elenco: [],
      duracion: null, edad: null,
      sala: resolverSala('teatro-del-puente'),
      temporada: {
        desde: fechas[0],
        hasta: fechas[fechas.length - 1],
        dias: [...dias].sort((a, b) => a - b),
        ...(Object.keys(horas).length ? { horas } : {}),
      },
      horarioTexto: `${fechas.length} ${fechas.length === 1 ? 'función' : 'funciones'}${conHora.length === 1 ? ` — ${conHora[0]} h` : ''}`,
      precios: [],
      imagen: null,
      entradas: 'https://teatrodelpuente.cl/venta-de-entradas/',
      url: 'https://teatrodelpuente.cl/cartelera/',
    });
    log(`  · ${titulo} (${fechas.length} funciones)`);
  }
  return obras;
}
