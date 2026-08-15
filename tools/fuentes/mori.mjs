/* Fuente: Teatro Mori — https://teatromori.cl
   No publica datos estructurados, pero su cartelera es regular: cada obra es un bloque
   .proyecto-item con título, sala, rango de fechas y horario. La ficha aporta sinopsis,
   equipo y precios. */

import { bajar, aTexto, limpiar, slug, parseHorarios, parseRangoFechas, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';

export const meta = { id: 'mori', nombre: 'Teatro Mori', sitio: 'https://teatromori.cl' };

const CARTELERA = 'https://teatromori.cl/cartelera';

const CLAVE_SALA = {
  'mori bellavista': 'mori-bellavista',
  'mori parque arauco': 'mori-parque-arauco',
  'mori recoleta': 'mori-recoleta',
  'mori vitacura': 'mori-vitacura',
};

/** Mapa "sala-3" → "mori-vitacura", leído de los filtros de la propia página. */
function mapaSalas(html) {
  const mapa = {};
  for (const m of html.matchAll(/data-filter=["']\.(sala-\d+)["'][^>]*>(?:<[^>]+>)*\s*([A-ZÁÉÍÓÚ ]{4,30})/g)) {
    const nombre = m[2].trim().toLowerCase();
    if (CLAVE_SALA[nombre]) mapa[m[1]] = CLAVE_SALA[nombre];
  }
  return mapa;
}

function campo(texto, ...etiquetas) {
  for (const e of etiquetas) {
    const m = texto.match(new RegExp(`^\\s*${e}[^:\\n]{0,25}:?\\s*\\n?\\s*([^\\n]{3,220})`, 'im'));
    // Se corta en "|": cuando la ficha va toda en una línea, el valor de un campo
    // termina donde empieza el siguiente.
    if (m) return limpiar(m[1].split('|')[0]).replace(/\.$/, '');
  }
  return null;
}

/**
 * Afiches de Mori: van en /mini/<ancho>x<alto>xS/<hash>.jpg.
 * Ojo con /mini/h79/ y /archivos/originales/: son los logos de la sala y de los
 * auspiciadores (PNG en negro sobre transparente), no la obra.
 */
function afichesDe(html) {
  return [...new Set([...html.matchAll(/[^"' ]*\/mini\/(\d+)x(\d+)x[A-Z]\/[^"' ]+\.(?:jpg|jpeg|png)/gi)]
    .map((m) => m[0]))]
    .map((u) => (u.startsWith('//') ? 'https:' + u : u))
    .filter((u) => !/logo|icon|menu/i.test(u));
}

async function leerFicha(url) {
  const html = await bajar(url);
  return { texto: aTexto(html), imagen: afichesDe(html)[0] || null };
}

export async function obtener({ log }) {
  const html = await bajar(CARTELERA);
  const salas = mapaSalas(html);
  const bloques = html.split(/<div class="proyecto-item/).slice(1);
  const obras = [];

  for (const bloque of bloques) {
    try {
      const claseSala = (bloque.match(/^\s+([\w- ]*sala-\d+)/) || [])[1] || '';
      const claveSala = salas[(claseSala.match(/sala-\d+/) || [])[0]] || 'mori-bellavista';

      const enlace = (bloque.match(/href=["']([^"']*\/obra\/[^"']+)["']/) || [])[1];
      if (!enlace) continue;
      const url = enlace.startsWith('//') ? 'https:' + enlace : enlace;

      const titulo = limpiar((bloque.match(/<h3 class="Title">([\s\S]*?)<\/h3>/) || [])[1] || '');
      if (!titulo) continue;

      const fechaTxt = limpiar((bloque.match(/class="Hover-Fecha">([\s\S]*?)<\//) || [])[1] || '');
      const horaTxt = limpiar((bloque.match(/class="Hover-Hora">([\s\S]*?)<\//) || [])[1] || '');
      const rango = parseRangoFechas(fechaTxt);
      if (!rango) { log(`  ! ${titulo}: no se entendió la fecha “${fechaTxt}”`); continue; }
      const horarios = parseHorarios(horaTxt);

      // El afiche vertical vive en el listado (formato póster); el de la ficha es el respaldo
      const aficheListado = afichesDe(bloque)[0] || null;
      const { texto, imagen } = await leerFicha(url);
      const sinopsis = texto.split('\n').filter((l) => l.length > 120)[0] || '';
      const elenco = campo(texto, 'elenco', 'reparto', 'actúan');

      obras.push({
        id: `mori-${slug(titulo)}`,
        titulo,
        subtitulo: null,
        sinopsis: limpiar(sinopsis).slice(0, 900),
        genero: inferirGenero(`${titulo} ${sinopsis}`),
        compania: campo(texto, 'obra de', 'compañía', 'colectivo'),
        director: campo(texto, 'dirección', 'director', 'directora'),
        dramaturgo: campo(texto, 'dramaturgia', 'texto', 'autor'),
        elenco: elenco ? elenco.split(/,| y (?=[A-ZÁÉÍÓÚ])/).map((s) => s.trim()).filter((s) => s.length > 2).slice(0, 14) : [],
        duracion: parseDuracion(texto),
        edad: parseEdad(texto),
        sala: resolverSala(claveSala),
        temporada: { ...rango, ...(horarios || {}) },
        horarioTexto: horaTxt || null,
        precios: parsePrecios(texto),
        imagen: aficheListado || imagen,
        entradas: url,
        url,
      });
      log(`  · ${titulo}`);
    } catch (e) {
      log(`  ! bloque omitido: ${e.message}`);
    }
  }
  return obras;
}
