/* Fuente: Centro GAM — https://www.gam.cl
   Cada ficha publica un JSON-LD schema.org/Event (fechas, imagen, sala, venta de entradas)
   y en el cuerpo trae horarios, precios y ficha artística. */

import { bajar, jsonLD, aTexto, limpiar, slug, parseHorarios, parsePrecios, parseDuracion, parseEdad, inferirGenero } from '../lib/util.mjs';
import { resolverSala } from '../lib/salas.mjs';

export const meta = { id: 'gam', nombre: 'Centro GAM', sitio: 'https://www.gam.cl' };

const LISTADOS = [
  { url: 'https://www.gam.cl/teatro/', generoBase: null },
  { url: 'https://www.gam.cl/familiar/', generoBase: 'infantil' },
  { url: 'https://www.gam.cl/danza/', generoBase: 'danza' },
];

/**
 * Etiqueta → valor de la ficha artística. El GAM la escribe de dos maneras: la
 * etiqueta y el valor en líneas separadas, o toda la ficha en una sola línea con
 * los cargos separados por "|" ("Dirección: Ana | Elenco: Juan, Luz"). El segundo
 * caso se parte por la barra, si no la dirección se lleva puesta la ficha entera.
 */
function fichaTecnica(texto) {
  const ficha = {};
  const anotar = (etiqueta, valor) => {
    const k = String(etiqueta).toLowerCase().trim();
    if (k && valor && !ficha[k]) ficha[k] = limpiar(valor).replace(/\.$/, '');
  };

  const lineas = texto.split('\n');
  for (let i = 0; i < lineas.length; i++) {
    const l = lineas[i];

    // Etiqueta sola, con el valor en la línea siguiente
    if (!/:/.test(l) && /^:\s*.+/.test(lineas[i + 1] || '') && l.length < 50) {
      anotar(l, lineas[i + 1].replace(/^:\s*/, ''));
      continue;
    }
    for (const trozo of l.split('|')) {
      const m = trozo.match(/^\s*([A-Za-zÁÉÍÓÚÑáéíóúñ .y/]{3,45})\s*:\s*(.+)$/);
      if (m) anotar(m[1], m[2]);
    }
  }
  return ficha;
}

const buscarFicha = (ficha, ...claves) => {
  for (const c of claves) {
    const hit = Object.keys(ficha).find((k) => k.includes(c));
    if (hit) return ficha[hit];
  }
  return null;
};

async function leerObra(url, generoBase) {
  const html = await bajar(url);
  const evento = jsonLD(html).find((x) => x['@type'] === 'Event');
  if (!evento || !evento.startDate) return null;

  const texto = aTexto(html);
  const ficha = fichaTecnica(texto);

  // Sala interior: viene dentro del nombre del lugar, con HTML escapado
  const salaInterior = limpiar((evento.location?.name || '').split(' - ').slice(2).join(' - '))
    .replace(/\(.*?\)/g, '').trim();

  // Horarios: el bloque de fechas del pie ("Ju a Sá— 19.30 h", "Do— 18.30 h")
  const bloqueHorario = (texto.match(/((?:Lu|Ma|Mi|Ju|Vi|S[áa]|Do)[^\n]{0,40}\d{1,2}[.:]\d{2}\s*h[^\n]{0,20}\n?){1,6}/g) || []).join('\n');
  const horarios = parseHorarios(bloqueHorario || texto);

  const desde = evento.startDate.slice(0, 10);
  const hasta = (evento.endDate || evento.startDate).slice(0, 10);

  const elenco = buscarFicha(ficha, 'elenco', 'reparto', 'actúan');
  const director = buscarFicha(ficha, 'dirección', 'directora', 'director');
  const dramaturgo = buscarFicha(ficha, 'dramaturgia', 'dramaturga', 'dramaturgo', 'texto', 'autor');
  const compania = buscarFicha(ficha, 'compañía', 'colectivo', 'producción general') || evento.performer?.name || null;

  const descripcion = limpiar(evento.description || '');
  const cuerpo = texto.slice(0, 4000);

  return {
    titulo: limpiar(evento.name).replace(/\s*:\s*Coproducción.*/i, ''),
    subtitulo: limpiar(evento.alternateName || '') || null,
    sinopsis: descripcion,
    genero: generoBase || inferirGenero(`${evento.name} ${descripcion} ${evento.genre || ''}`),
    compania: compania ? limpiar(compania) : null,
    director: director ? limpiar(director) : null,
    dramaturgo: dramaturgo ? limpiar(dramaturgo) : null,
    elenco: elenco ? limpiar(elenco).split(/,| y (?=[A-ZÁÉÍÓÚ])/).map((s) => s.trim()).filter((s) => s.length > 2).slice(0, 14) : [],
    duracion: parseDuracion(buscarFicha(ficha, 'duración') || cuerpo),
    edad: parseEdad(buscarFicha(ficha, 'recomendada', 'edad', 'público') || cuerpo),
    sala: resolverSala('gam', salaInterior),
    temporada: { desde, hasta, ...(horarios || {}) },
    horarioTexto: limpiar(bloqueHorario).replace(/\s*\n\s*/g, ' · ') || null,
    precios: parsePrecios(cuerpo),
    imagen: Array.isArray(evento.image) ? evento.image[0] : evento.image || null,
    entradas: evento.offers?.url || null,
    url,
  };
}

export async function obtener({ log }) {
  const obras = [];
  const vistos = new Set();

  for (const { url, generoBase } of LISTADOS) {
    let html;
    try { html = await bajar(url); } catch (e) { log(`  ! no se pudo leer ${url}: ${e.message}`); continue; }

    // Los enlaces van absolutos (https://gam.cl/...), pero aceptamos ambas formas
    const enlaces = [...html.matchAll(/href=["']((?:https?:\/\/(?:www\.)?gam\.cl)?\/es\/que-hacer-en-gam\/(?:teatro|familiar|danza)\/[^"'#?]+\/)["']/gi)]
      .map((m) => (m[1].startsWith('http') ? m[1] : 'https://www.gam.cl' + m[1]))
      .filter((u) => !/\/(historico|archivo)\/$/.test(u))
      // la propia sección ("…/teatro/") no es una obra
      .filter((u) => u.split('/').filter(Boolean).length > 5);

    for (const enlace of [...new Set(enlaces)]) {
      if (vistos.has(enlace)) continue;
      vistos.add(enlace);
      try {
        const obra = await leerObra(enlace, generoBase);
        if (obra) { obras.push({ ...obra, id: `gam-${slug(obra.titulo)}` }); log(`  · ${obra.titulo}`); }
      } catch (e) { log(`  ! ${enlace}: ${e.message}`); }
    }
  }
  return obras;
}
