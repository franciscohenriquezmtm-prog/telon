/* =============================================================================
   Actualizador de cartelera — Telón
   -----------------------------------------------------------------------------
   Lee los sitios oficiales de los teatros, normaliza las obras, descarga los
   afiches y escribe assets/cartelera.json (lo que consume la app).

   Uso:
     node tools/actualizar.mjs                → actualiza todo
     node tools/actualizar.mjs --fuente gam   → solo una fuente
     node tools/actualizar.mjs --sin-imagenes → no descarga afiches

   Si una fuente falla, se conservan sus obras de la última actualización buena:
   nunca se deja la app sin cartelera por un sitio caído.
   ========================================================================== */

import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, unlinkSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bajar, iso, slug } from './lib/util.mjs';
import { generarMiniaturas, pesoMiniaturas } from './lib/miniaturas.mjs';

import * as gam from './fuentes/gam.mjs';
import * as teatrouc from './fuentes/teatrouc.mjs';
import * as mori from './fuentes/mori.mjs';
import * as m100 from './fuentes/m100.mjs';
import * as lascondes from './fuentes/lascondes.mjs';
import * as nescafe from './fuentes/nescafe.mjs';
import * as finisterrae from './fuentes/finisterrae.mjs';
import * as puente from './fuentes/puente.mjs';
import * as ictus from './fuentes/ictus.mjs';
import * as zoco from './fuentes/zoco.mjs';

const FUENTES = [gam, teatrouc, mori, m100, lascondes, nescafe, finisterrae, puente, ictus, zoco];

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), '..');
const SALIDA = join(RAIZ, 'assets', 'cartelera.json');
const DIR_IMG = join(RAIZ, 'assets', 'img');

const args = process.argv.slice(2);
const soloFuente = args.includes('--fuente') ? args[args.indexOf('--fuente') + 1] : null;
const sinImagenes = args.includes('--sin-imagenes');
const sinBuild = args.includes('--sin-build');

const log = (m) => console.log(m);

/* ── Normalización ────────────────────────────────────────────────────────── */

const HOY = new Date(); HOY.setHours(0, 0, 0, 0);

/** Descarta lo que ya terminó y arregla rangos al revés o sin fin. */
function normalizarTemporada(t) {
  if (!t?.desde) return null;
  const desde = new Date(t.desde + 'T00:00:00');
  let hasta = t.hasta ? new Date(t.hasta + 'T00:00:00') : null;
  if (!hasta || hasta < desde) hasta = desde;
  if (hasta < HOY) return null; // temporada terminada
  const salida = { desde: iso(desde), hasta: iso(hasta) };
  if (t.dias?.length) salida.dias = t.dias;
  if (t.horas && Object.keys(t.horas).length) salida.horas = t.horas;
  if (t.hora) salida.hora = t.hora;

  // Función única: el día de la semana se deduce sin inventar nada
  if (!salida.dias && salida.hora && salida.desde === salida.hasta) salida.dias = [desde.getDay()];

  // Si el teatro no publicó los días, NO los inventamos: la app mostrará el rango
  // de temporada y el horario tal como lo escribió el teatro.
  if (!salida.dias && !salida.hora && !salida.horas) salida.sinHorario = true;
  return salida;
}

function normalizar(obra, fuente) {
  const temporada = normalizarTemporada(obra.temporada);
  if (!temporada || !obra.titulo) return null;
  return {
    id: obra.id || `${fuente.meta.id}-${slug(obra.titulo)}`,
    titulo: obra.titulo,
    subtitulo: obra.subtitulo || null,
    sinopsis: obra.sinopsis || '',
    genero: obra.genero || 'drama',
    compania: obra.compania || null,
    director: obra.director || null,
    dramaturgo: obra.dramaturgo || null,
    elenco: obra.elenco || [],
    duracion: obra.duracion || null,
    edad: obra.edad ?? null,
    sala: obra.sala,
    temporada,
    // Texto de horarios tal como lo publica el teatro (se muestra cuando no se
    // pudieron deducir los días exactos, y como respaldo siempre visible)
    horarioTexto: obra.horarioTexto || null,
    precios: obra.precios || [],
    imagen: obra.imagen || null,
    imagenLocal: null,
    huellaImagen: null,
    entradas: obra.entradas || null,
    url: obra.url,
    fuente: { id: fuente.meta.id, nombre: fuente.meta.nombre, sitio: fuente.meta.sitio },
  };
}

/* ── Afiches ──────────────────────────────────────────────────────────────── */

async function descargarAfiche(obra) {
  if (!obra.imagen) return null;
  try {
    const datos = await bajar(obra.imagen, { binario: true, intentos: 2 });
    if (datos.length < 2000) return null; // placeholder o error disfrazado
    let ext = extname(new URL(obra.imagen).pathname).toLowerCase();
    if (!['.jpg', '.jpeg', '.png', '.webp'].includes(ext)) ext = '.jpg';
    const archivo = `${obra.id}${ext}`;
    mkdirSync(DIR_IMG, { recursive: true });
    writeFileSync(join(DIR_IMG, archivo), datos);
    return { ruta: `assets/img/${archivo}`, huella: createHash('sha1').update(datos).digest('hex') };
  } catch {
    return null;
  }
}

/**
 * Un mismo archivo repetido en varias obras no es un afiche: es el logo del teatro
 * o un placeholder. Se descarta para no mostrar la misma imagen en toda la cartelera.
 */
function descartarImagenesRepetidas(obras, log) {
  const porHuella = new Map();
  obras.forEach((o) => {
    if (!o.huellaImagen) return;
    if (!porHuella.has(o.huellaImagen)) porHuella.set(o.huellaImagen, []);
    porHuella.get(o.huellaImagen).push(o);
  });

  let descartadas = 0;
  for (const [, grupo] of porHuella) {
    if (grupo.length < 2) continue;
    log(`  ! la misma imagen aparece en ${grupo.length} obras (${grupo.map((o) => o.titulo).slice(0, 3).join(', ')}${grupo.length > 3 ? '…' : ''}): parece un logo, se descarta`);
    grupo.forEach((o) => {
      const archivo = o.imagenLocal && join(RAIZ, o.imagenLocal);
      if (archivo && existsSync(archivo)) unlinkSync(archivo);
      o.imagenLocal = null;
      o.imagen = null;
      descartadas++;
    });
  }
  return descartadas;
}

/* ── Ejecución ────────────────────────────────────────────────────────────── */

const previo = existsSync(SALIDA) ? JSON.parse(readFileSync(SALIDA, 'utf8')) : { obras: [], fuentes: [] };
const informe = [];
let obras = [];

for (const fuente of FUENTES) {
  if (soloFuente && fuente.meta.id !== soloFuente) {
    // conservamos lo que ya había de las fuentes que no se piden
    obras.push(...previo.obras.filter((o) => o.fuente.id === fuente.meta.id));
    continue;
  }
  log(`\n▸ ${fuente.meta.nombre} (${fuente.meta.sitio})`);
  const t0 = Date.now();
  try {
    const crudas = await fuente.obtener({ log });
    const limpias = crudas.map((o) => normalizar(o, fuente)).filter(Boolean);
    obras.push(...limpias);
    informe.push({ id: fuente.meta.id, nombre: fuente.meta.nombre, sitio: fuente.meta.sitio, obras: limpias.length, error: null });
    log(`  ✓ ${limpias.length} obras en cartelera (${crudas.length - limpias.length} descartadas por temporada terminada) · ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  } catch (e) {
    const rescatadas = previo.obras.filter((o) => o.fuente.id === fuente.meta.id);
    obras.push(...rescatadas);
    informe.push({ id: fuente.meta.id, nombre: fuente.meta.nombre, sitio: fuente.meta.sitio, obras: rescatadas.length, error: e.message });
    log(`  ✗ falló (${e.message}). Se conservan ${rescatadas.length} obras de la última actualización.`);
  }
}

// Afiches
if (!sinImagenes) {
  log('\n▸ Afiches');
  for (const obra of obras) {
    const yaEsta = previo.obras.find((p) => p.id === obra.id && p.imagen === obra.imagen && p.imagenLocal && p.huellaImagen);
    if (yaEsta && existsSync(join(RAIZ, yaEsta.imagenLocal))) {
      obra.imagenLocal = yaEsta.imagenLocal;
      obra.huellaImagen = yaEsta.huellaImagen;
      continue;
    }
    const bajado = await descargarAfiche(obra);
    obra.imagenLocal = bajado ? bajado.ruta : null;
    obra.huellaImagen = bajado ? bajado.huella : null;
  }
  descartarImagenesRepetidas(obras, log);
  const ok = obras.filter((o) => o.imagenLocal).length;
  log(`  ✓ ${ok}/${obras.length} afiches disponibles`);

  // limpieza de afiches huérfanos
  if (existsSync(DIR_IMG)) {
    const usados = new Set(obras.map((o) => o.imagenLocal && o.imagenLocal.split('/').pop()).filter(Boolean));
    for (const archivo of readdirSync(DIR_IMG)) {
      if (archivo === 'mini') continue;
      if (!usados.has(archivo)) { unlinkSync(join(DIR_IMG, archivo)); log(`  · afiche eliminado: ${archivo}`); }
    }
  }

  // Miniaturas: son las que se incrustan en la versión de un solo archivo,
  // para que las fotos se vean incluso donde no se pueden cargar imágenes externas.
  const mini = await generarMiniaturas(RAIZ, { log });
  if (mini.total) log(`  ✓ ${mini.total} miniaturas (${pesoMiniaturas(RAIZ).toFixed(0)} KB) · ${mini.herramienta}`);
}

obras.sort((a, b) => a.temporada.desde.localeCompare(b.temporada.desde) || a.titulo.localeCompare(b.titulo, 'es'));

const salida = {
  actualizado: new Date().toISOString(),
  zona: 'America/Santiago',
  fuentes: informe.length ? informe : previo.fuentes,
  obras,
};

mkdirSync(dirname(SALIDA), { recursive: true });
writeFileSync(SALIDA, JSON.stringify(salida, null, 1));

log(`\n═══ ${obras.length} obras escritas en assets/cartelera.json`);
const sinDatos = obras.filter((o) => !o.sinopsis || !o.precios.length || !o.imagenLocal);
if (sinDatos.length) {
  log(`    ${sinDatos.length} con datos incompletos:`);
  sinDatos.slice(0, 12).forEach((o) => log(`      - ${o.titulo}: falta ${[!o.sinopsis && 'sinopsis', !o.precios.length && 'precios', !o.imagenLocal && 'afiche'].filter(Boolean).join(', ')}`));
}
// Deja lista la versión de un solo archivo, con las fotos ya incrustadas
if (!sinBuild) {
  log('');
  await import('./build-single.mjs');
}

const fallidas = informe.filter((f) => f.error);
if (fallidas.length) process.exitCode = 1;
