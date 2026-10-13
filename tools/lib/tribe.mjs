/* Lector de The Events Calendar (WordPress).

   Varios teatros usan este plugin, que publica una API REST con los eventos ya
   estructurados. Como el formato es idéntico en todos, el trabajo se hace una vez
   acá y cada fuente solo aporta su dirección y su sala.

   Ojo: la API resume la ficha. Los horarios, el elenco y los precios suelen estar
   completos únicamente en la página de la obra, así que se lee también. */

import { bajar, aTexto, limpiar, slug, parseHorarios, parsePrecios, parseDuracion, parseEdad, inferirGenero } from './util.mjs';
import { resolverSala } from './salas.mjs';

/**
 * Busca "Dirección: X" / "Elenco: X" dentro del texto de la ficha.
 * Varias fichas ponen todos los cargos en una línea separados por "|"
 * ("Dirección: Ana | Música: Juan"), así que el valor se corta en la barra:
 * si no, el director se lleva puesto al músico.
 */
export function campo(texto, ...etiquetas) {
  for (const e of etiquetas) {
    const m = texto.match(new RegExp(`${e}[^:\\n]{0,20}:\\s*([^\\n]{3,220})`, 'i'));
    if (m) return limpiar(m[1].split('|')[0]).replace(/\.$/, '');
  }
  return null;
}

/** "Ana Pérez, Juan Soto y Luz Díaz" → lista de nombres. */
export const listaDeNombres = (txt) => (txt
  ? txt.split(/,| y (?=[A-ZÁÉÍÓÚ])/).map((s) => s.trim()).filter((s) => s.length > 2).slice(0, 14)
  : []);

/**
 * @param {object} opts
 * @param {string} opts.api      URL base de la API (…/wp-json/tribe/events/v1/events)
 * @param {string} opts.idFuente prefijo de los id de obra
 * @param {string} opts.sala     clave en SALAS
 * @param {(ev:object)=>boolean} [opts.filtrar] descarta eventos que no son función
 */
export async function leerEventsCalendar({ api, idFuente, sala, filtrar, log }) {
  const hoy = new Date().toISOString().slice(0, 10);
  const datos = JSON.parse(await bajar(`${api}?per_page=50&status=publish&start_date=${hoy}`));
  const eventos = [...(datos.events || [])];

  // El listado de esta API a veces omite eventos vigentes: Finis Terrae tenía
  // "Un enemigo del pueblo" en cartelera del 1 al 30 de octubre y el listado
  // devolvía cero. El detalle por id sí lo entrega bien. Por eso se cruzan con los
  // eventos publicados según WordPress (wp/v2), y se piden por id los que falten.
  const vistos = new Set(eventos.map((e) => e.id));
  try {
    const publicados = JSON.parse(await bajar(`${api.replace('/tribe/events/v1/events', '/wp/v2/tribe_events')}?per_page=50&status=publish`));
    for (const p of Array.isArray(publicados) ? publicados : []) {
      if (vistos.has(p.id)) continue;
      try {
        const ev = JSON.parse(await bajar(`${api}/${p.id}`));
        if ((ev.end_date || ev.start_date || '').slice(0, 10) >= hoy) {
          eventos.push(ev);
          log(`  + ${limpiar(ev.title)} (no venía en el listado; recuperado por id)`);
        }
      } catch { /* evento sin detalle: se omite */ }
    }
  } catch { /* sitio sin wp/v2 público: queda solo el listado */ }

  const obras = [];
  for (const ev of eventos) {
    try {
      if (filtrar && !filtrar(ev)) continue;

      let texto = aTexto(ev.description || '');
      try { texto += '\n' + aTexto(await bajar(ev.url)); } catch { /* seguimos con lo que hay */ }

      const horarios = parseHorarios(texto);
      const desde = ev.start_date.slice(0, 10);
      const hasta = (ev.end_date || ev.start_date).slice(0, 10);
      // Un evento "de todo el día" trae 00:00 como hora: eso no es la hora de la función
      const horaInicio = ev.all_day || ev.start_date.slice(11, 16) === '00:00' ? null : ev.start_date.slice(11, 16);
      const categorias = (ev.categories || []).map((c) => c.name).join(' ');
      const sinopsis = limpiar(ev.excerpt || ev.description || '').slice(0, 900);

      obras.push({
        id: `${idFuente}-${slug(ev.title)}`,
        titulo: limpiar(ev.title),
        subtitulo: null,
        sinopsis,
        genero: inferirGenero(`${ev.title} ${sinopsis} ${categorias}`),
        compania: campo(texto, 'compañía', 'colectivo'),
        director: campo(texto, 'dirección', 'director', 'directora'),
        dramaturgo: campo(texto, 'dramaturgia', 'autor', 'texto de'),
        elenco: listaDeNombres(campo(texto, 'elenco', 'reparto')),
        duracion: parseDuracion(texto),
        edad: parseEdad(texto),
        sala: resolverSala(sala, ev.venue?.venue),
        // Solo damos por buenos los días que la ficha declara; si no los declara,
        // dejamos el rango de temporada y mostramos la hora tal como la publica el teatro.
        temporada: horarios ? { desde, hasta, ...horarios } : { desde, hasta },
        horarioTexto: horarios ? null : (horaInicio ? `Funciones desde las ${horaInicio} h — confirma los días en el sitio` : null),
        precios: parsePrecios(`${ev.cost || ''} ${texto}`),
        imagen: ev.image?.url || null,
        entradas: ev.website || null,
        url: ev.url,
      });
      log(`  · ${limpiar(ev.title)}`);
    } catch (e) {
      log(`  ! ${ev.title}: ${e.message}`);
    }
  }
  return obras;
}
