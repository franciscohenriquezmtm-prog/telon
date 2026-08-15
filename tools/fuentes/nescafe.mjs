/* Fuente: Teatro Nescafé de las Artes — https://www.teatro-nescafe-delasartes.cl

   WordPress con tipo propio "evento" en wp-json. Programa mucha música además de
   artes escénicas, así que se filtra por las categorías del propio sitio (se leen
   en caliente: sus ids cambian, sus slugs no).

   Las fechas van escritas en la ficha ("25 y 26 de agosto - 20:00 horas"), no en un
   campo. Suelen ser funciones sueltas, no temporadas largas. */

import { bajar, aTexto, limpiar, slug, parseHorarios, parseRangoFechas, parseFechasEnumeradas, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';
import { campo, listaDeNombres } from '../lib/tribe.mjs';

export const meta = {
  id: 'nescafe',
  nombre: 'Teatro Nescafé de las Artes',
  sitio: 'https://www.teatro-nescafe-delasartes.cl',
};

const API = 'https://www.teatro-nescafe-delasartes.cl/wp-json/wp/v2';

// Categorías del sitio que sí son artes escénicas.
const ESCENICAS = ['teatro', 'humor', 'stand-up-comedy', 'danza', 'unipersonal', 'cabaret', 'national-theatre-live', 'fitam'];

const MES = /\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b/i;

export async function obtener({ log }) {
  const categorias = JSON.parse(await bajar(`${API}/categories?per_page=100&_fields=id,slug`));
  const idsEscenicas = new Set(categorias.filter((c) => ESCENICAS.includes(c.slug)).map((c) => c.id));
  if (!idsEscenicas.size) throw new Error('no se reconoció ninguna categoría de artes escénicas');

  // _embed trae la imagen destacada en la misma respuesta: la ficha en HTML no
  // publica og:image y sus <img> son solo los logos del teatro.
  const eventos = JSON.parse(await bajar(`${API}/evento?per_page=100&status=publish&_embed=wp:featuredmedia`));
  const obras = [];

  for (const ev of eventos) {
    const titulo = limpiar(ev.title?.rendered || '');
    try {
      if (!titulo) continue;
      if (!(ev.categories || []).some((c) => idsEscenicas.has(c))) continue;

      const html = await bajar(ev.link);
      const texto = aTexto(html);

      // La primera línea con un mes escrito es la de funciones
      const lineaFecha = texto.split('\n').find((l) => MES.test(l) && /\d/.test(l) && l.length < 120);
      const rango = lineaFecha ? (parseRangoFechas(lineaFecha) || parseFechasEnumeradas(lineaFecha)) : null;
      if (!rango) { log(`  ! ${titulo}: no se entendió la fecha${lineaFecha ? ` “${lineaFecha}”` : ''}`); continue; }

      // Funciones sueltas: los días de la semana salen de las fechas mismas
      const dias = rango.fechas
        ? [...new Set(rango.fechas.map((f) => new Date(f + 'T00:00:00').getDay()))].sort((a, b) => a - b)
        : null;
      const horarios = parseHorarios(lineaFecha || '');

      // Los precios se buscan solo en el cuerpo de la ficha. La página entera lista
      // los montos de otras funciones en su barra lateral, y mezclarlos daba diez
      // tramos falsos por obra. Este teatro casi siempre remite a Ticketmaster.
      const cuerpo = aTexto(ev.content?.rendered || '');
      const sinopsis = cuerpo.split('\n').find((l) => l.length > 110) || '';

      obras.push({
        id: `nescafe-${slug(titulo)}`,
        titulo,
        subtitulo: null,
        sinopsis: limpiar(sinopsis).slice(0, 900),
        genero: inferirGenero(`${titulo} ${sinopsis}`),
        compania: campo(texto, 'compañía', 'colectivo'),
        director: campo(texto, 'dirección', 'director', 'directora'),
        dramaturgo: campo(texto, 'dramaturgia', 'autor', 'texto de'),
        elenco: listaDeNombres(campo(texto, 'elenco', 'reparto', 'protagonizada por')),
        duracion: parseDuracion(texto),
        edad: parseEdad(texto),
        sala: resolverSala('nescafe-de-las-artes'),
        temporada: {
          desde: rango.desde,
          hasta: rango.hasta,
          ...(dias ? { dias } : {}),
          ...(horarios ? { horas: horarios.horas } : {}),
        },
        horarioTexto: lineaFecha ? limpiar(lineaFecha) : null,
        precios: parsePrecios(cuerpo),
        imagen: ev._embedded?.['wp:featuredmedia']?.[0]?.source_url || null,
        entradas: (html.match(/href=["'](https:\/\/www\.ticketmaster\.cl\/[^"']+)["']/i) || [])[1] || ev.link,
        url: ev.link,
      });
      log(`  · ${titulo}`);
    } catch (e) {
      log(`  ! ${titulo || ev.link}: ${e.message}`);
    }
  }
  return obras;
}
