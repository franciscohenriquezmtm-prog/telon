/* Utilidades compartidas por los adaptadores de cada teatro. */

export const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';

/** fetch con reintentos y timeout. */
export async function bajar(url, { intentos = 3, timeout = 25000, binario = false } = {}) {
  let ultimo;
  for (let i = 0; i < intentos; i++) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), timeout);
      const res = await fetch(url, { headers: { 'User-Agent': UA, 'Accept-Language': 'es-CL,es;q=0.9' }, signal: ctrl.signal, redirect: 'follow' });
      clearTimeout(t);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return binario ? Buffer.from(await res.arrayBuffer()) : await res.text();
    } catch (e) {
      ultimo = e;
      await new Promise((r) => setTimeout(r, 800 * (i + 1)));
    }
  }
  throw new Error(`${url} → ${ultimo.message}`);
}

/** Todos los bloques JSON-LD de una página, ya parseados. */
export function jsonLD(html) {
  const salida = [];
  for (const m of html.matchAll(/<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)) {
    try {
      const j = JSON.parse(m[1].trim());
      salida.push(...(Array.isArray(j) ? j : j['@graph'] || [j]));
    } catch { /* bloque roto: lo ignoramos */ }
  }
  return salida;
}

/** HTML → texto plano, conservando saltos entre bloques. */
export function aTexto(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|li|h\d|tr|section)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&').replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"').replace(/&#39;|&#039;/gi, "'")
    .replace(/[ \t ]+/g, ' ')
    .split('\n').map((l) => l.trim()).filter(Boolean).join('\n');
}

/* Ojo con el orden: algunos sitios (el GAM, en el nombre de la sala) mandan las
   etiquetas escapadas — "&lt;p&gt;&lt;b&gt;Sala N1&lt;/b&gt;&lt;/p&gt;". Hay que
   desescaparlas ANTES de quitar etiquetas; si no, sobreviven como texto visible. */
export const limpiar = (s) => String(s || '')
  .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
  .replace(/<[^>]+>/g, ' ').replace(/&nbsp;/gi, ' ')
  .replace(/&amp;/gi, '&').replace(/&#8217;|&#039;|&#39;/gi, "'").replace(/&quot;/gi, '"')
  .replace(/&#8211;|&#8212;/gi, '—').replace(/&hellip;/gi, '…')
  .replace(/\s+/g, ' ').trim();

const sinTildes = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

export const slug = (s) => sinTildes(s).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);

/* ── Fechas y días en español ─────────────────────────────────────────────── */

const MESES = {
  ene: 0, enero: 0, feb: 1, febrero: 1, mar: 2, marzo: 2, abr: 3, abril: 3,
  may: 4, mayo: 4, jun: 5, junio: 5, jul: 6, julio: 6, ago: 7, agosto: 7,
  sep: 8, sept: 8, septiembre: 8, oct: 9, octubre: 9, nov: 10, noviembre: 10, dic: 11, diciembre: 11,
};

/** Día de la semana → número (0 domingo). Acepta abreviaturas del tipo "Ju", "Sá", "Mié". */
const DIAS = [
  [0, /^(do|dom|domingo|domingos)$/],
  [1, /^(lu|lun|lunes)$/],
  [2, /^(ma|mar|martes)$/],
  [3, /^(mi|mie|mier|miercoles)$/],
  [4, /^(ju|jue|jueves)$/],
  [5, /^(vi|vie|viernes)$/],
  [6, /^(sa|sab|sabado|sabados)$/],
];

export function numeroDia(txt) {
  const t = sinTildes(String(txt).trim().replace(/\.$/, ''));
  for (const [n, re] of DIAS) if (re.test(t)) return n;
  return null;
}

/** "12 de agosto de 2026", "7 ago 2026", "9 de julio" → Date (año opcional). */
export function parseFecha(txt, anioPorDefecto) {
  const t = sinTildes(txt);
  const m = t.match(/(\d{1,2})\s*(?:de\s+)?([a-z]{3,10})\.?\s*(?:de\s+)?(\d{4})?/);
  if (!m) return null;
  const mes = MESES[m[2]] ?? MESES[m[2].slice(0, 3)];
  if (mes === undefined) return null;
  return new Date(Number(m[3]) || anioPorDefecto || new Date().getFullYear(), mes, Number(m[1]));
}

/**
 * Detecta días de función y horarios en frases como:
 *   "Ju a Sá— 19.30 h / Do— 18.30 h"   → {4,5,6}=19:30 y {0}=18:30
 *   "Vi— 19 h / Sá y Do— 17 h"         → {5}=19:00 y {6,0}=17:00
 *   "De jueves a sábado a las 20:00 horas"
 * Cada hora se asigna a los días que la anteceden (desde la hora anterior).
 * Devuelve { dias, horas } o null si no reconoce ningún día: nunca inventa horarios.
 */
export function parseHorarios(texto) {
  const horas = {};
  const dias = new Set();
  const t = String(texto).replace(/ /g, ' ');

  // "19.30 h", "20:00 horas", o "19 h" (sin minutos, el sufijo es obligatorio)
  const RE_HORA = /(\d{1,2})[.:](\d{2})\s*(?:h\b|hrs?\b|horas\b)?|(\d{1,2})\s*(?:h\b|hrs?\b|horas\b)/gi;

  /* Un precio no es una hora. "$7.500" encaja igual de bien en "H.MM" que "19.30 h",
     así que se descartan los montos: los que vienen tras un "$" y los que siguen con
     más dígitos ("5.000" tiene un 0 de sobra que una hora no tendría). */
  const esMonto = (m) => {
    const antes = t.slice(0, m.index).replace(/\s+$/, '');
    if (antes.endsWith('$')) return true;
    return /\d/.test(t[m.index + m[0].length] || '');
  };

  const encontradas = [...t.matchAll(RE_HORA)]
    .filter((m) => !esMonto(m))
    .filter((m) => Number(m[1] ?? m[3]) <= 23)
    .filter((m) => m[2] === undefined || Number(m[2]) <= 59);

  let corte = 0;
  for (const m of encontradas) {
    const contexto = t.slice(corte, m.index);
    corte = m.index + m[0].length;

    const hh = String(Number(m[1] ?? m[3])).padStart(2, '0');
    const hora = `${hh}:${m[2] ?? '00'}`;

    // rango "Ju a Sá" / "de jueves a sábado"
    const rango = contexto.match(/([a-záéíóúü]{2,10})\.?\s*(?:a|al|hasta)\s+([a-záéíóúü]{2,10})/i);
    let encontrados = [];
    if (rango && numeroDia(rango[1]) !== null && numeroDia(rango[2]) !== null) {
      const a = numeroDia(rango[1]), b = numeroDia(rango[2]);
      for (let i = 0; i < 7; i++) { const d = (a + i) % 7; encontrados.push(d); if (d === b) break; }
    } else {
      // enumeración "Sá y Do", "viernes, sábado y domingo"
      encontrados = [...contexto.matchAll(/[a-záéíóúü]{2,10}/gi)].map((x) => numeroDia(x[0])).filter((d) => d !== null);
    }

    for (const d of encontrados) { dias.add(d); horas[d] = hora; }
  }

  if (!dias.size) return null;
  return { dias: [...dias].sort((a, b) => a - b), horas };
}

/**
 * Cuando el teatro escribe la fecha sin año ("Del 06 al 16/08") se asume el año en
 * curso; si eso dejara la temporada más de dos meses en el pasado, se entiende que
 * habla del año siguiente (una cartelera anuncia funciones por venir, no pasadas).
 * Solo se aplica si el texto NO traía año: un año explícito manda siempre.
 */
function ajustarAnio(rango, teniaAnio) {
  if (teniaAnio) return rango;
  const limite = new Date(); limite.setDate(limite.getDate() - 60);
  if (new Date(rango.hasta + 'T00:00:00') >= limite) return rango;
  const mas = (f) => { const d = new Date(f + 'T00:00:00'); d.setFullYear(d.getFullYear() + 1); return iso(d); };
  return { desde: mas(rango.desde), hasta: mas(rango.hasta) };
}

/**
 * "Del 9 de julio al 1 de agosto [de 2026]" / "7 al 23 Ago 2026" /
 * "27 de agosto al 11 de octubre" / "Del 06 al 16/08" → { desde, hasta } ISO
 */
export function parseRangoFechas(texto, anio = new Date().getFullYear()) {
  const t = sinTildes(texto);

  // "del 9 de julio al 1 de agosto"
  let m = t.match(/del?\s+(\d{1,2})\s*(?:de\s+)?([a-z]{3,10})?\.?\s*(?:de\s+(\d{4}))?\s+(?:al|hasta el|a)\s+(\d{1,2})\s*(?:de\s+)?([a-z]{3,10})\.?\s*(?:de\s+)?(\d{4})?/);
  if (m) {
    const mes2 = MESES[m[5]] ?? MESES[m[5].slice(0, 3)];
    const mes1 = m[2] ? (MESES[m[2]] ?? MESES[m[2].slice(0, 3)]) : mes2;
    if (mes1 === undefined || mes2 === undefined) return null;
    const a2 = Number(m[6]) || anio;
    const a1 = Number(m[3]) || (mes1 > mes2 ? a2 - 1 : a2);
    return ajustarAnio({ desde: iso(new Date(a1, mes1, Number(m[1]))), hasta: iso(new Date(a2, mes2, Number(m[4]))) }, Boolean(m[3] || m[6]));
  }

  // "27 de agosto al 11 de octubre" (sin el "del" inicial) y
  // "desde el viernes 16 de mayo al domingo 15 de junio" (con el día de la semana en medio)
  m = t.match(/(\d{1,2})\s+de\s+([a-z]{3,10})\.?\s*(?:de\s+(\d{4}))?\s*(?:al|hasta el|a)\s+(?:el\s+)?(?:(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)\s+)?(\d{1,2})\s+de\s+([a-z]{3,10})\.?\s*(?:de\s+)?(\d{4})?/);
  if (m) {
    const mes1 = MESES[m[2]] ?? MESES[m[2].slice(0, 3)];
    const mes2 = MESES[m[5]] ?? MESES[m[5].slice(0, 3)];
    if (mes1 === undefined || mes2 === undefined) return null;
    const a2 = Number(m[6]) || anio;
    const a1 = Number(m[3]) || (mes1 > mes2 ? a2 - 1 : a2);
    return ajustarAnio({ desde: iso(new Date(a1, mes1, Number(m[1]))), hasta: iso(new Date(a2, mes2, Number(m[4]))) }, Boolean(m[3] || m[6]));
  }

  // "Del 06 al 16/08" y "06/08 al 16/08" (Matucana 100)
  m = t.match(/(?:del\s+)?(\d{1,2})(?:\/(\d{1,2}))?\s*(?:al|-|—|a)\s*(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?/);
  if (m) {
    const mes2 = Number(m[4]) - 1;
    const mes1 = m[2] ? Number(m[2]) - 1 : mes2;
    if (mes1 < 0 || mes1 > 11 || mes2 < 0 || mes2 > 11) return null;
    let a2 = m[5] ? Number(m[5].length === 2 ? '20' + m[5] : m[5]) : anio;
    const a1 = mes1 > mes2 ? a2 - 1 : a2;
    return ajustarAnio({ desde: iso(new Date(a1, mes1, Number(m[1]))), hasta: iso(new Date(a2, mes2, Number(m[3]))) }, Boolean(m[5]));
  }

  // "7 al 23 Ago 2026"
  m = t.match(/(\d{1,2})\s*(?:al|-|—|a)\s*(\d{1,2})\s+([a-z]{3,10})\.?\s*(\d{4})?/);
  if (m) {
    const mes = MESES[m[3]] ?? MESES[m[3].slice(0, 3)];
    if (mes === undefined) return null;
    const a = Number(m[4]) || anio;
    return ajustarAnio({ desde: iso(new Date(a, mes, Number(m[1]))), hasta: iso(new Date(a, mes, Number(m[2]))) }, Boolean(m[4]));
  }
  return null;
}

/**
 * Funciones sueltas enumeradas: "25 y 26 de agosto", "8, 9 y 10 de agosto",
 * "19 de agosto". Devuelve las fechas ISO que el teatro nombró, sin rellenar
 * los días intermedios. null si no reconoce ninguna.
 */
export function parseFechasEnumeradas(texto, anio = new Date().getFullYear()) {
  const t = sinTildes(texto);
  const m = t.match(/((?:\d{1,2}\s*(?:,|y|-)\s*)*\d{1,2})\s+de\s+([a-z]{3,10})\.?\s*(?:de\s+(\d{4}))?/);
  if (!m) return null;
  const mes = MESES[m[2]] ?? MESES[m[2].slice(0, 3)];
  if (mes === undefined) return null;
  const dias = [...new Set(m[1].split(/[,y\-\s]+/).map(Number).filter((d) => d >= 1 && d <= 31))]
    .sort((x, y) => x - y);
  if (!dias.length) return null;

  let a = Number(m[3]) || anio;
  if (!m[3]) {
    const limite = new Date(); limite.setDate(limite.getDate() - 60);
    if (new Date(a, mes, dias[dias.length - 1]) < limite) a++;
  }
  const fechas = dias.map((d) => iso(new Date(a, mes, d)));
  return { desde: fechas[0], hasta: fechas[fechas.length - 1], fechas };
}

export const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** Precios en pesos dentro de un texto → lista ordenada y sin repetir. */
export function parsePrecios(texto) {
  const valores = [...String(texto).matchAll(/\$\s?(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})/g)]
    .map((m) => Number(m[1].replace(/[.\s]/g, '')))
    .filter((v) => v >= 1000 && v <= 200000);
  const liberado = /\b(liberada|gratis|gratuita|a la gorra)\b/i.test(texto);
  const unicos = [...new Set(valores)].sort((a, b) => a - b);
  if (!unicos.length) return liberado ? [{ tipo: 'Entrada liberada', valor: 0 }] : [];
  const precios = [];
  if (liberado) precios.push({ tipo: 'Función liberada', valor: 0 });
  if (unicos.length === 1) precios.push({ tipo: 'General', valor: unicos[0] });
  else {
    precios.push({ tipo: 'General', valor: unicos[unicos.length - 1] });
    unicos.slice(0, -1).reverse().forEach((v, i) => precios.push({ tipo: i === 0 ? 'Descuento (estudiantes / 3ª edad)' : 'Convenio', valor: v }));
  }
  return precios;
}

/** Género a partir del texto de la obra (heurística conservadora). */
export function inferirGenero(txt = '') {
  const t = sinTildes(txt);
  if (/\b(infantil|familiar|ninos|ninas|primera infancia|titeres|marioneta)\b/.test(t)) {
    return /titere|marioneta/.test(t) ? 'titeres' : 'infantil';
  }
  if (/\b(danza|coreograf|ballet)\b/.test(t)) return 'danza';
  if (/stand[- ]?up|comedia en vivo|humorista/.test(t)) return 'humor';
  if (/\b(musical|opera)\b|comedia musical/.test(t)) return 'musical';
  if (/documental|testimoni|verbatim/.test(t)) return 'documental';
  if (/\bcomedia\b|humor/.test(t)) return 'comedia';
  if (/shakespeare|chejov|lorca|beckett|clasico|moliere|sofocles|tragedia griega/.test(t)) return 'clasico';
  if (/experimental|performance|instalacion|multimedia|videoteatro/.test(t)) return 'experimental';
  return 'drama';
}

/** Duración en minutos: "60 min", "1 hora 40 minutos", "90'" */
export function parseDuracion(txt) {
  const t = sinTildes(txt);
  // El lookbehind evita leer "20:00 hrs" (una hora de función) como "0 horas de duración"
  let m = t.match(/(?<![:.\d])(\d{1,2})\s*h(?:ora)?s?\s*(?:y\s*)?(\d{1,2})?\s*(?:min)?/);
  if (m && Number(m[1]) >= 1 && Number(m[1]) <= 5) return Number(m[1]) * 60 + Number(m[2] || 0);
  m = t.match(/(\d{2,3})\s*(?:min|minutos|')/);
  if (m) return Number(m[1]);
  return null;
}

/**
 * Edad mínima: "+14", "mayores de 12 años", "todo público".
 * Se toma la primera que sea una edad plausible: "+56 9 8255 3149" es el teléfono
 * del teatro, no una recomendación por edad.
 */
export function parseEdad(txt) {
  const t = sinTildes(txt);
  for (const m of t.matchAll(/(?:\+|mayores de\s*|desde los?\s*|apta para mayores de\s*)(\d{1,2})\s*(?:anos)?/g)) {
    const edad = Number(m[1]);
    if (edad <= 18) return edad;
  }
  if (/todo publico|todas las edades|toda la familia/.test(t)) return 0;
  return null;
}
