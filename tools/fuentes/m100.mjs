/* Fuente: Matucana 100 — https://www.m100.cl

   WordPress sin datos estructurados, pero la ficha de cada obra es muy regular:
   un bloque final con el rango de fechas ("Del 20 al 30/08"), los horarios por día,
   los tramos de precio, el espacio, la duración y la edad; y una línea de
   "Ficha Artística" con los cargos separados por "|". */

import { bajar, aTexto, limpiar, slug, parseHorarios, parseRangoFechas, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';
import { listaDeNombres } from '../lib/tribe.mjs';

export const meta = { id: 'm100', nombre: 'Matucana 100', sitio: 'https://www.m100.cl' };

// Solo artes escénicas: el sitio programa además cine, música y artes visuales.
const SECCIONES = ['teatro', 'danza-circo'];

/** "Dirección: Ana | Elenco: Juan, Luz | Producción: …" → { direccion, elenco, … } */
function fichaArtistica(texto) {
  const linea = texto.split('\n').find((l) => /\|/.test(l) && /(direcci[oó]n|elenco|dramaturgia)/i.test(l));
  const campos = {};
  for (const trozo of (linea || '').split('|')) {
    const m = trozo.match(/^\s*([^:]{3,40}):\s*(.+)$/);
    if (m) campos[limpiar(m[1]).toLowerCase()] = limpiar(m[2]);
  }
  const buscar = (...claves) => {
    for (const c of claves) {
      const k = Object.keys(campos).find((x) => x.includes(c));
      if (k) return campos[k];
    }
    return null;
  };
  return {
    director: buscar('direcci'),
    dramaturgo: buscar('dramaturgia', 'texto', 'autor'),
    elenco: listaDeNombres(buscar('elenco', 'reparto')),
    compania: buscar('compañ', 'colectivo'),
  };
}

/** Las fichas enlazadas desde el listado de cada sección. */
async function fichasDe(seccion) {
  const html = await bajar(`https://www.m100.cl/programacion/${seccion}/`);
  return [...new Set([...html.matchAll(
    new RegExp(`https://www\\.m100\\.cl/programacion/${seccion}/[a-z0-9-]+/`, 'gi'),
  )].map((m) => m[0]))];
}

export async function obtener({ log }) {
  const obras = [];

  for (const seccion of SECCIONES) {
    let fichas = [];
    try {
      fichas = await fichasDe(seccion);
    } catch (e) {
      log(`  ! no se pudo leer la sección ${seccion}: ${e.message}`);
      continue;
    }

    for (const url of fichas) {
      try {
        const html = await bajar(url);
        const texto = aTexto(html);

        const titulo = limpiar((html.match(/<title[^>]*>([\s\S]*?)(?:&#8211;|—|\|)\s*Matucana/i) || [])[1] || '');
        if (!titulo) continue;

        // El rango va en la línea "Teatro · Del 20 al 30/08"
        const lineaFecha = texto.split('\n').find((l) => /del?\s+\d{1,2}\s+al\s+\d{1,2}\/\d{1,2}/i.test(l));
        const rango = parseRangoFechas(lineaFecha || '');
        if (!rango) { log(`  ! ${titulo}: sin rango de fechas legible`); continue; }

        // Bloque de datos prácticos: va después del segundo "Del X al Y"
        const lineas = texto.split('\n');
        const iBloque = lineas.findIndex((l, i) => i > 5 && /^del?\s+\d{1,2}\s+al\s+\d{1,2}\/\d{1,2}$/i.test(l.trim()));
        const bloque = iBloque >= 0 ? lineas.slice(iBloque, iBloque + 14).join('\n') : texto;

        const horarios = parseHorarios(bloque);
        const horarioTexto = lineas.slice(Math.max(iBloque, 0) + 1, Math.max(iBloque, 0) + 4)
          .filter((l) => /\d{1,2}[.:]\d{2}/.test(l) && !/\$/.test(l)).join(' · ') || null;

        const espacio = lineas.slice(Math.max(iBloque, 0), Math.max(iBloque, 0) + 14)
          .find((l) => /^(sala|espacio|patio|galer[ií]a)\b/i.test(l.trim()));

        const sinopsis = lineas.find((l) => l.length > 110) || '';
        const artistica = fichaArtistica(texto);

        obras.push({
          id: `m100-${slug(titulo)}`,
          titulo,
          subtitulo: null,
          sinopsis: limpiar(sinopsis).slice(0, 900),
          genero: inferirGenero(`${seccion.replace('-', ' ')} ${titulo} ${sinopsis}`),
          ...artistica,
          duracion: parseDuracion(bloque),
          edad: parseEdad(bloque),
          sala: resolverSala('matucana-100', espacio ? limpiar(espacio) : null),
          temporada: { ...rango, ...(horarios || {}) },
          horarioTexto,
          precios: parsePrecios(bloque),
          imagen: (html.match(/property=["']og:image["'][^>]*content=["']([^"']+)["']/i) || [])[1] || null,
          entradas: url,
          url,
        });
        log(`  · ${titulo}`);
      } catch (e) {
        log(`  ! ${url}: ${e.message}`);
      }
    }
  }
  return obras;
}
