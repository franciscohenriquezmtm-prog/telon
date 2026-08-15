/* Fuente: Teatro Ictus — https://teatroictus.cl

   Cada obra tiene ficha en /cartelera/<slug>/. Las fechas vienen escritas de tres
   formas distintas según la obra: una fecha suelta ("19 DE AGOSTO"), un cierre
   ("HASTA EL 22 DE AGOSTO") o un rango. El horario va en la línea siguiente
   ("Vie y sáb | 20:00 H").

   La sala también programa música en vivo; esas fichas listan "Músicos" en vez de
   elenco, y se marcan como musical para no venderlas como teatro. */

import { bajar, aTexto, limpiar, slug, iso, parseHorarios, parseRangoFechas, parseFechasEnumeradas, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';
import { campo, listaDeNombres } from '../lib/tribe.mjs';

export const meta = { id: 'ictus', nombre: 'Teatro Ictus', sitio: 'https://teatroictus.cl' };

const MESES = { enero: 0, febrero: 1, marzo: 2, abril: 3, mayo: 4, junio: 5, julio: 6, agosto: 7, septiembre: 8, octubre: 9, noviembre: 10, diciembre: 11 };
const RE_MES = /\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b/i;

/** "HASTA EL 22 DE AGOSTO" → temporada que corre desde hoy hasta esa fecha. */
function hastaEl(texto) {
  const m = texto.match(/hasta\s+(?:el\s+)?(\d{1,2})\s+de\s+([a-záéíóú]+)/i);
  if (!m) return null;
  const mes = MESES[m[2].toLowerCase()];
  if (mes === undefined) return null;
  const hoy = new Date();
  let fin = new Date(hoy.getFullYear(), mes, Number(m[1]));
  if (fin < hoy) fin = new Date(hoy.getFullYear() + 1, mes, Number(m[1]));
  return { desde: iso(hoy), hasta: iso(fin) };
}

/**
 * La ficha no trae og:image. El afiche es la primera foto subida al gestor que no
 * sea del sitio: los logos de la sala llevan "Teatro-Ictus" o "logo" en el nombre.
 */
function aficheDe(html) {
  return [...html.matchAll(/<img[^>]+src=["']([^"']*\/wp-content\/uploads\/[^"']+)["']/gi)]
    .map((m) => m[1])
    .find((u) => /\.(jpe?g|png|webp)$/i.test(u) && !/logo|icon|teatro-ictus|banner|footer/i.test(u)) || null;
}

async function fichas() {
  const encontradas = new Set();
  for (const pagina of ['https://teatroictus.cl/', 'https://teatroictus.cl/cartelera/']) {
    try {
      const html = await bajar(pagina);
      for (const m of html.matchAll(/https:\/\/teatroictus\.cl\/cartelera\/[a-z0-9-]+\//gi)) encontradas.add(m[0]);
    } catch { /* con una de las dos basta */ }
  }
  return [...encontradas];
}

export async function obtener({ log }) {
  const obras = [];

  for (const url of await fichas()) {
    try {
      const html = await bajar(url);
      const texto = aTexto(html);
      const lineas = texto.split('\n');

      // Las obras que aún no estrenan van rotuladas "PRÓXIMAMENTE:" en el título
      const titulo = limpiar((html.match(/<title[^>]*>([\s\S]*?)(?:&#8211;|—|\|)\s*Teatro Ictus/i) || [])[1] || '')
        .replace(/^pr[óo]ximamente\s*:?\s*/i, '');
      if (!titulo) continue;

      // El bloque de fechas es lo primero que aparece con un mes escrito
      const iFecha = lineas.findIndex((l) => RE_MES.test(l) && /\d/.test(l) && l.length < 90);
      if (iFecha < 0) { log(`  ! ${titulo}: sin fecha legible`); continue; }
      const bloque = lineas.slice(iFecha, iFecha + 3).join('\n');

      const rango = parseRangoFechas(bloque) || hastaEl(bloque) || parseFechasEnumeradas(bloque);
      if (!rango) { log(`  ! ${titulo}: no se entendió “${lineas[iFecha]}”`); continue; }

      const horarios = parseHorarios(bloque);
      const dias = !horarios && rango.fechas
        ? [...new Set(rango.fechas.map((f) => new Date(f + 'T00:00:00').getDay()))].sort((a, b) => a - b)
        : null;

      const musicos = campo(texto, 'músicos', 'musicos');
      const elenco = campo(texto, 'elenco', 'reparto', 'actúan');
      const sinopsis = lineas.find((l) => l.length > 110) || '';

      obras.push({
        id: `ictus-${slug(titulo)}`,
        titulo,
        subtitulo: null,
        sinopsis: limpiar(sinopsis).slice(0, 900),
        genero: musicos && !elenco ? 'musical' : inferirGenero(`${titulo} ${sinopsis}`),
        compania: campo(texto, 'compañía', 'colectivo'),
        director: campo(texto, 'dirección', 'director', 'directora'),
        dramaturgo: campo(texto, 'dramaturgia', 'autor', 'texto de'),
        elenco: listaDeNombres(elenco || musicos),
        duracion: parseDuracion(texto),
        edad: parseEdad(texto),
        sala: resolverSala('teatro-ictus'),
        temporada: { desde: rango.desde, hasta: rango.hasta, ...(horarios || {}), ...(dias ? { dias } : {}) },
        horarioTexto: limpiar(bloque.replace(/\n/g, ' · ')) || null,
        precios: parsePrecios(texto),
        imagen: aficheDe(html),
        entradas: (html.match(/href=["'](https:\/\/ticketplus\.cl\/[^"']+)["']/i) || [])[1] || url,
        url,
      });
      log(`  · ${titulo}`);
    } catch (e) {
      log(`  ! ${url}: ${e.message}`);
    }
  }
  return obras;
}
