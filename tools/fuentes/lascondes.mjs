/* Fuente: Teatro Municipal de Las Condes — https://www.tmlascondes.cl

   Cada obra tiene su página en la raíz del sitio, con un bloque JSON-LD
   schema.org/Event bien completo: nombre, fecha, afiche, precio, elenco y género.
   No hay listado en /cartelera (devuelve 404), así que las fichas se descubren
   desde los enlaces de la portada.

   El JSON-LD trae solo la fecha de inicio. Cuando el texto de la página declara un
   rango que empieza ese mismo día, se toma su fecha de término; si no coinciden, se
   deja la fecha del JSON-LD antes que estirar la temporada por nuestra cuenta. */

import { bajar, aTexto, limpiar, slug, jsonLD, parseRangoFechas, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';
import { campo, listaDeNombres } from '../lib/tribe.mjs';

export const meta = {
  id: 'lascondes',
  nombre: 'Teatro Municipal de Las Condes',
  sitio: 'https://www.tmlascondes.cl',
};

// Páginas institucionales y rutas internas de WordPress que no son obras.
const NO_ES_OBRA = /\/(wp-[a-z]+|author|category|tag|feed|conoce-el-teatro|estrenos|descuentos-y-beneficios|reglas-generales|preguntas-frecuentes|presenta-tu-proyecto|mejora-tu-experiencia|palco-educativo|programa-umayor|dia-del-patrimonio|convocatoria[^/]*|cartelera|exposiciones|contacto|el-teatro)\/?$/i;

// El teatro programa sobre todo conciertos; nos quedamos con las artes escénicas.
const ESCENICAS = /teatro|danza|familiar|infantil|circo|humor|ópera|opera|musical/i;

/**
 * Cada obra es un post con URL en la raíz. La portada solo enlaza las de la semana,
 * así que el listado completo se saca del sitemap; las temporadas ya terminadas las
 * descarta después el actualizador al normalizar las fechas.
 */
async function fichas(log) {
  const encontradas = new Set();
  try {
    const xml = await bajar('https://www.tmlascondes.cl/post-sitemap.xml');
    for (const m of xml.matchAll(/<loc>([^<]+)<\/loc>/g)) encontradas.add(m[1]);
  } catch (e) {
    log(`  ! sitemap no disponible (${e.message}); se usan solo los enlaces de la portada`);
  }
  try {
    const portada = await bajar('https://www.tmlascondes.cl/');
    for (const m of portada.matchAll(/https:\/\/www\.tmlascondes\.cl\/[a-z0-9-]{4,}\//gi)) encontradas.add(m[0]);
  } catch { /* con el sitemap basta */ }
  return [...encontradas].filter((u) => !NO_ES_OBRA.test(u));
}

export async function obtener({ log }) {
  const obras = [];
  let descartadas = 0;

  // El JSON-LD suele traer solo la fecha de inicio, aunque la temporada dure semanas.
  // Por eso el descarte por antigüedad usa un margen amplio: quien decide si la
  // temporada terminada se cae es el actualizador, ya con el rango reconciliado.
  const margen = new Date(); margen.setDate(margen.getDate() - 90);
  const limite = margen.toISOString().slice(0, 10);

  const encontradas = await fichas(log);
  log(`  · ${encontradas.length} fichas por revisar`);

  for (const url of encontradas) {
    try {
      const html = await bajar(url);
      const evento = jsonLD(html).find((x) => String(x['@type']).includes('Event'));
      if (!evento?.startDate) continue;
      if ((evento.endDate || evento.startDate).slice(0, 10) < limite) continue;

      const genero = String(evento.genre || '');
      if (!ESCENICAS.test(genero)) { descartadas++; continue; }

      const texto = aTexto(html);
      const desde = evento.startDate.slice(0, 10);
      let hasta = (evento.endDate || evento.startDate).slice(0, 10);

      // El sitio a veces escribe el rango completo en el cuerpo
      const rango = parseRangoFechas(texto.split('\n').find((l) => /\bal\b/.test(l) && /\d/.test(l) && l.length < 120) || '');
      if (rango && rango.desde === desde && rango.hasta > hasta) hasta = rango.hasta;

      const oferta = evento.offers || {};
      const precio = Number(oferta.price);
      const sinopsis = limpiar(evento.description || '').slice(0, 900);

      obras.push({
        id: `lascondes-${slug(evento.name)}`,
        titulo: limpiar(evento.name),
        subtitulo: null,
        sinopsis,
        genero: inferirGenero(`${genero} ${evento.name} ${sinopsis}`),
        compania: campo(texto, 'compañía', 'colectivo'),
        director: campo(texto, 'dirección', 'director', 'directora'),
        dramaturgo: campo(texto, 'dramaturgia', 'autor', 'texto de'),
        elenco: evento.performer?.name
          ? listaDeNombres(evento.performer.name)
          : listaDeNombres(campo(texto, 'elenco', 'reparto')),
        duracion: parseDuracion(texto),
        edad: parseEdad(texto),
        sala: resolverSala('tm-las-condes'),
        // No se deducen los días de función: lo único con horas en la página es el
        // horario de boletería, y tomarlo por horario de función sería inventar.
        temporada: { desde, hasta },
        horarioTexto: 'Consulta los horarios de cada función en el sitio del teatro',
        precios: precio >= 1000 ? [{ tipo: 'General', valor: precio }] : parsePrecios(texto),
        imagen: typeof evento.image === 'string' ? evento.image : evento.image?.url || null,
        entradas: oferta.url ? limpiar(oferta.url) : url,
        url,
      });
      log(`  · ${limpiar(evento.name)} (${genero})`);
    } catch (e) {
      log(`  ! ${url}: ${e.message}`);
    }
  }
  if (descartadas) log(`  · ${descartadas} eventos omitidos por no ser artes escénicas (conciertos, etc.)`);
  return obras;
}
