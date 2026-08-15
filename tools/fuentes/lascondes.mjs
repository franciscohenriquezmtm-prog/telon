/* Fuente: Teatro Municipal de Las Condes — https://www.tmlascondes.cl

   Cada obra tiene su página en la raíz del sitio, con un bloque JSON-LD
   schema.org/Event bien completo: nombre, fecha, afiche, precio, elenco y género.
   No hay listado en /cartelera (devuelve 404), así que las fichas se descubren
   desde los enlaces de la portada.

   Ojo con las fechas: el JSON-LD declara solo startDate, y no siempre es el estreno
   —en "Close To Me" apuntaba al 14 de agosto cuando la temporada partió el 6—. Tomarlo
   como temporada completa hacía desaparecer la obra al día siguiente.

   La buena noticia es que el cuerpo de la página lista las funciones una por una
   ("Jueves 6 de agosto • 19:30 horas"). De ahí sale la temporada de verdad, con sus
   días y sus horas. El startDate del JSON-LD queda solo como respaldo y como ancla
   para el año, que las líneas no declaran. */

import { bajar, aTexto, limpiar, slug, jsonLD, iso, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
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

const MESES = {
  enero: 0, febrero: 1, marzo: 2, abril: 3, mayo: 4, junio: 5,
  julio: 6, agosto: 7, septiembre: 8, octubre: 9, noviembre: 10, diciembre: 11,
};

/* "Jueves 6 de agosto • 19:30 horas". Exige el número de día, y por eso no confunde
   estas líneas con el horario de boletería ("Lunes a viernes: 10:00 a 19:30 h"). */
const RE_FUNCION = /^(?:lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\s+(\d{1,2})\s+de\s+([a-záéíóúñ]+)(?:[^\d\n]{1,8}(\d{1,2})[:.](\d{2}))?/i;

// Ninguna temporada de este teatro dura más que esto. Si el resultado se pasa, es
// que se colaron líneas de otra cosa y no se puede confiar en lo leído.
const MAX_DIAS_TEMPORADA = 200;

/**
 * Temporada reconstruida desde las funciones que lista la ficha.
 *
 * Las líneas no declaran el año ("Jueves 6 de agosto"), y el startDate del JSON-LD
 * no sirve de ancla directa: en "Close To Me" apuntaba al 14 de agosto cuando las
 * funciones parten el 6, y en las obras de Teatro a Mil apunta a junio para una
 * temporada de enero. Así que para cada función se prueban el año anterior, el del
 * startDate y el siguiente, y se toma el que caiga más cerca de esa fecha declarada.
 * Es la lectura más conservadora: acerca cada función al único dato con año que el
 * teatro sí publica, en vez de suponer una dirección.
 */
function funcionesPublicadas(texto, inicioJsonLD, log, titulo) {
  const ancla = new Date(inicioJsonLD + 'T00:00:00');
  const fechas = [];
  const horas = {};

  for (const linea of texto.split('\n')) {
    const m = linea.trim().match(RE_FUNCION);
    if (!m) continue;
    const mes = MESES[m[2].toLowerCase()];
    if (mes === undefined) continue;

    const candidatas = [-1, 0, 1]
      .map((salto) => new Date(ancla.getFullYear() + salto, mes, Number(m[1])))
      .filter((f) => !Number.isNaN(f.getTime()));
    const fecha = candidatas.sort((a, b) => Math.abs(a - ancla) - Math.abs(b - ancla))[0];
    if (!fecha) continue;

    fechas.push(fecha);
    if (m[3] && horas[fecha.getDay()] === undefined) {
      horas[fecha.getDay()] = `${String(Number(m[3])).padStart(2, '0')}:${m[4]}`;
    }
  }

  if (!fechas.length) return null;
  fechas.sort((a, b) => a - b);

  const span = (fechas[fechas.length - 1] - fechas[0]) / 86400000;
  if (span > MAX_DIAS_TEMPORADA) {
    log(`  ! ${titulo}: las funciones leídas abarcan ${Math.round(span)} días; no se usan y queda la fecha del JSON-LD`);
    return null;
  }

  const dias = [...new Set(fechas.map((f) => f.getDay()))].sort((a, b) => a - b);

  return {
    desde: iso(fechas[0]),
    hasta: iso(fechas[fechas.length - 1]),
    dias,
    ...(Object.keys(horas).length ? { horas } : {}),
    total: fechas.length,
  };
}

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

      // La lista de funciones de la ficha manda; el JSON-LD es el respaldo
      const funciones = funcionesPublicadas(texto, evento.startDate.slice(0, 10), log, limpiar(evento.name));
      const temporada = funciones
        ? { desde: funciones.desde, hasta: funciones.hasta, dias: funciones.dias, ...(funciones.horas ? { horas: funciones.horas } : {}) }
        : { desde: evento.startDate.slice(0, 10), hasta: (evento.endDate || evento.startDate).slice(0, 10) };

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
        temporada,
        horarioTexto: funciones
          ? `${funciones.total} ${funciones.total === 1 ? 'función publicada' : 'funciones publicadas'} por el teatro`
          : 'Consulta los horarios de cada función en el sitio del teatro',
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
