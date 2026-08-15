/* Fuente: Centro para las Artes Zoco — https://teatrozoco.cl

   WordPress con un tipo propio "evento" accesible por wp-json. Las fechas no están
   en un campo: van escritas en el bloque "Datos Generales" de la ficha
   ("27 de agosto al 11 de octubre / Jueves a sábado 19:30 hrs, domingo 18:00 hrs"),
   así que la ficha se lee igual.

   Ese bloque a veces trae recesos ("con receso los días 17 al 20 de septiembre").
   No se pueden modelar como excepciones, así que el texto se conserva entero en
   horarioTexto para que la app lo muestre tal como lo escribió el teatro. */

import { bajar, aTexto, limpiar, slug, parseHorarios, parseRangoFechas, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';
import { campo, listaDeNombres } from '../lib/tribe.mjs';

export const meta = { id: 'zoco', nombre: 'Centro para las Artes Zoco', sitio: 'https://teatrozoco.cl' };

const API = 'https://teatrozoco.cl/wp-json/wp/v2';

// El mismo tipo "evento" cubre funciones, talleres y abonos: estos últimos no son cartelera.
const NO_ES_FUNCION = /\b(taller|curso|abono|convocatoria|clase)\b/i;

/** El afiche va dentro del cuerpo; del srcset se toma la variante más ancha. */
function aficheDe(html) {
  const img = html.match(/<img[^>]+>/i);
  if (!img) return null;
  const srcset = (img[0].match(/srcset=["']([^"']+)["']/i) || [])[1];
  if (srcset) {
    const mejor = srcset.split(',')
      .map((p) => p.trim().match(/^(\S+)\s+(\d+)w$/))
      .filter(Boolean)
      .sort((a, b) => Number(b[2]) - Number(a[2]))[0];
    if (mejor) return mejor[1];
  }
  return (img[0].match(/src=["']([^"']+)["']/i) || [])[1] || null;
}

export async function obtener({ log }) {
  const eventos = JSON.parse(await bajar(`${API}/evento?per_page=50&status=publish`));
  const obras = [];

  for (const ev of eventos) {
    const titulo = limpiar(ev.title?.rendered || '');
    try {
      if (!titulo || NO_ES_FUNCION.test(titulo)) continue;

      const texto = aTexto(await bajar(ev.link));

      // "Datos Generales" reúne fechas, horarios y venta de entradas
      const iDatos = texto.search(/datos\s+generales/i);
      const bloque = iDatos >= 0 ? texto.slice(iDatos, iDatos + 400) : texto;

      const rango = parseRangoFechas(bloque);
      if (!rango) { log(`  ! ${titulo}: no se entendió la fecha`); continue; }
      const horarios = parseHorarios(bloque);

      const lineasDatos = bloque.split('\n').slice(1, 6)
        .filter((l) => /\d|receso/i.test(l) && !/comprar|entradas en/i.test(l));

      const sinopsis = aTexto(ev.content?.rendered || '').split('\n').find((l) => l.length > 110) || '';

      obras.push({
        id: `zoco-${slug(titulo)}`,
        titulo,
        subtitulo: null,
        sinopsis: limpiar(sinopsis).slice(0, 900),
        genero: inferirGenero(`${titulo} ${sinopsis}`),
        compania: campo(texto, 'compañía', 'colectivo'),
        director: campo(texto, 'dirección', 'director', 'directora'),
        dramaturgo: campo(texto, 'dramaturgia', 'versión de', 'texto', 'autor'),
        elenco: listaDeNombres(campo(texto, 'elenco', 'reparto')),
        duracion: parseDuracion(texto),
        edad: parseEdad(texto),
        sala: resolverSala('teatro-zoco'),
        temporada: { ...rango, ...(horarios || {}) },
        horarioTexto: lineasDatos.join(' · ') || null,
        precios: parsePrecios(texto),
        imagen: aficheDe(ev.content?.rendered || ''),
        entradas: ev.link,
        url: ev.link,
      });
      log(`  · ${titulo}`);
    } catch (e) {
      log(`  ! ${titulo || ev.link}: ${e.message}`);
    }
  }
  return obras;
}
