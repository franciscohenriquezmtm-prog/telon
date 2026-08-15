/* =============================================================================
   TELÓN — lógica de la app
   Consume assets/cartelera.json, que genera tools/actualizar.mjs desde los
   sitios oficiales de los teatros. Sin dependencias.
   ========================================================================== */

/* Catálogo fijo de géneros (el actualizador clasifica cada obra en uno de estos). */
const GENEROS = [
  { id: 'drama', nombre: 'Drama', emoji: '🎭' },
  { id: 'comedia', nombre: 'Comedia', emoji: '😂' },
  { id: 'musical', nombre: 'Musical', emoji: '🎶' },
  { id: 'clasico', nombre: 'Clásico', emoji: '🏛️' },
  { id: 'infantil', nombre: 'Infantil', emoji: '🧸' },
  { id: 'experimental', nombre: 'Experimental', emoji: '🌀' },
  { id: 'documental', nombre: 'Documental', emoji: '📰' },
  { id: 'danza', nombre: 'Danza', emoji: '🩰' },
  { id: 'humor', nombre: 'Stand-up', emoji: '🎤' },
  { id: 'titeres', nombre: 'Títeres', emoji: '🪆' },
];

/* ─────────────────────────── Utilidades de fecha ─────────────────────────── */

const DIA_MS = 86400000;
const hoy0 = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
const sumarDias = (fecha, n) => { const d = new Date(fecha); d.setDate(d.getDate() + n); return d; };
const mismaFecha = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const claveDia = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const desdeISO = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };

const fmtDiaLargo = new Intl.DateTimeFormat('es-CL', { weekday: 'long', day: 'numeric', month: 'long' });
const fmtDiaCorto = new Intl.DateTimeFormat('es-CL', { weekday: 'short', day: 'numeric', month: 'short' });
const fmtMesCorto = new Intl.DateTimeFormat('es-CL', { month: 'short' });
const fmtFechaCorta = new Intl.DateTimeFormat('es-CL', { day: 'numeric', month: 'short' });
const fmtHora = new Intl.DateTimeFormat('es-CL', { hour: '2-digit', minute: '2-digit', hour12: false });

function etiquetaDia(fecha, largo = false) {
  const h = hoy0();
  if (mismaFecha(fecha, h)) return 'Hoy';
  if (mismaFecha(fecha, sumarDias(h, 1))) return 'Mañana';
  const txt = (largo ? fmtDiaLargo : fmtDiaCorto).format(fecha).replace(/\./g, '');
  return txt.charAt(0).toUpperCase() + txt.slice(1);
}

function haceCuanto(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 2) return 'recién';
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.round(h / 24);
  return d === 1 ? 'ayer' : `hace ${d} días`;
}

const precioCLP = (v) => (v === 0 ? 'Gratis' : '$' + v.toLocaleString('es-CL'));

function duracionTxt(min) {
  const h = Math.floor(min / 60), m = min % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** Texto corto de precio. */
function textoPrecio(o) {
  if (!o.precios.length) return 'Ver precios';
  if (o.precioDesde === 0) return 'Gratis';
  if (o.precioDesde === o.precioMax) return precioCLP(o.precioDesde);
  return `desde ${precioCLP(o.precioDesde)}`;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ──────────────── Expansión de la temporada a funciones ─────────────────── */

function generarFunciones(obra) {
  const t = obra.temporada;
  const inicio = desdeISO(t.desde);
  const fin = desdeISO(t.hasta); fin.setHours(23, 59, 59, 999);
  if (t.sinHorario) return [];

  const dias = t.dias || [0, 1, 2, 3, 4, 5, 6];
  const funciones = [];
  const tope = Math.min(Math.round((fin - inicio) / DIA_MS), 400);

  for (let i = 0; i <= tope; i++) {
    const d = sumarDias(inicio, i);
    const dow = d.getDay();
    if (!dias.includes(dow)) continue;
    const hora = (t.horas && t.horas[dow]) || t.hora || '20:00';
    const [hh, mm] = hora.split(':').map(Number);
    const f = new Date(d.getFullYear(), d.getMonth(), d.getDate(), hh, mm);
    if (f <= fin) funciones.push(f);
  }
  return funciones.sort((a, b) => a - b);
}

/* ──────────────────────────── Estado global ──────────────────────────────── */

let CARTELERA = { obras: [], fuentes: [], actualizado: null };
let CATALOGO = [];
let COMUNAS = [];

const LS_FAV = 'telon.favoritos.v1';
let favoritos = new Set(JSON.parse(localStorage.getItem(LS_FAV) || '[]'));

const estado = {
  q: '', rango: 'todo',
  generos: new Set(), comunas: new Set(),
  precioMax: 45000, duracionMax: 180,
  soloGratis: false, soloAccesible: false, soloNinos: false,
  orden: 'fecha', vista: 'cartelera',
};

function prepararCatalogo(datos) {
  const ahora = new Date();
  CATALOGO = datos.obras.map((o) => {
    const funciones = generarFunciones(o);
    const futuras = funciones.filter((f) => f >= ahora);
    const valores = o.precios.map((p) => p.valor);
    const pagadas = valores.filter((v) => v > 0);
    const fin = desdeISO(o.temporada.hasta);
    return {
      ...o,
      salaInfo: o.sala,
      generoInfo: GENEROS.find((g) => g.id === o.genero) || { nombre: o.genero, emoji: '🎭' },
      inicio: desdeISO(o.temporada.desde),
      fin,
      funciones, futuras,
      proxima: futuras[0] || null,
      precioMin: valores.length ? Math.min(...valores) : null,
      precioMax: valores.length ? Math.max(...valores) : null,
      precioDesde: pagadas.length ? Math.min(...pagadas) : (valores.length ? 0 : null),
      esGratis: valores.some((v) => v === 0),
      tieneAccesibilidad: false,
      aptaNinos: o.edad !== null && o.edad <= 12,
      buscable: [o.titulo, o.subtitulo, o.compania, o.director, o.dramaturgo, ...(o.elenco || []),
                 o.sala.nombre, o.sala.comuna, o.fuente.nombre].filter(Boolean).join(' ').toLowerCase(),
    };
  })
  // se muestra lo que aún tiene funciones por delante (o temporada vigente sin horario publicado)
  .filter((o) => o.futuras.length > 0 || (o.temporada.sinHorario && o.fin >= hoy0()));

  COMUNAS = [...new Set(CATALOGO.map((o) => o.salaInfo.comuna))].sort((a, b) => a.localeCompare(b, 'es'));
}

/* ─────────────────────────── Póster / afiche ─────────────────────────────── */

const PALETAS = {
  drama: ['#6d1029', '#2a0a1c'], comedia: ['#c2531a', '#5c1408'], musical: ['#8a1e56', '#2c0b3a'],
  clasico: ['#5b3a12', '#22140a'], infantil: ['#127a6e', '#0d2f3a'], experimental: ['#33307a', '#120c2c'],
  documental: ['#3d4a52', '#141b20'], danza: ['#155e75', '#0a2233'], humor: ['#a3611b', '#3b1a06'],
  titeres: ['#6b7a15', '#22280a'],
};

/** Afiche real del teatro; si no carga, queda el fondo con el título. */
function posterHTML(obra, { cinta = '', alto = false, prioritario = false } = {}) {
  const [g1, g2] = PALETAS[obra.genero] || PALETAS.drama;
  const giro = (obra.id.length * 37) % 40 - 20;
  const foto = obra.imagenLocal || obra.imagen;
  return `
    <div class="poster ${foto ? 'con-foto' : ''}" style="background:linear-gradient(${140 + giro}deg,${g1},${g2})">
      ${foto ? `<img class="poster-img" src="${esc(foto)}" alt="Afiche de ${esc(obra.titulo)}" loading="${prioritario || alto ? 'eager' : 'lazy'}" decoding="async"
                     onerror="this.remove(); this.closest('.poster').classList.remove('con-foto')">` : ''}
      ${cinta ? `<span class="poster-cinta">${esc(cinta)}</span>` : ''}
      <span class="poster-emoji" aria-hidden="true">${obra.generoInfo.emoji}</span>
      <span class="poster-titulo ${alto ? 'grande' : ''}">${esc(obra.titulo)}</span>
    </div>`;
}

/* ───────────────────────────── Filtros ───────────────────────────────────── */

const ORDENES = {
  fecha:  { label: 'Por fecha',  fn: (a, b) => (a.proxima || a.inicio) - (b.proxima || b.inicio) },
  precio: { label: 'Más barata', fn: (a, b) => (a.precioDesde ?? 1e9) - (b.precioDesde ?? 1e9) },
  titulo: { label: 'A-Z',        fn: (a, b) => a.titulo.localeCompare(b.titulo, 'es') },
  corta:  { label: 'Más corta',  fn: (a, b) => (a.duracion ?? 1e6) - (b.duracion ?? 1e6) },
};
const CICLO_ORDEN = ['fecha', 'precio', 'corta', 'titulo'];

function rangoLimites() {
  const h = hoy0(), ahora = new Date();
  switch (estado.rango) {
    case 'hoy': return [ahora, sumarDias(h, 1)];
    case 'manana': return [sumarDias(h, 1), sumarDias(h, 2)];
    case 'finde': {
      const dow = h.getDay();
      const haciaVie = dow === 0 ? -2 : (dow === 6 ? -1 : 5 - dow);
      const vie = sumarDias(h, haciaVie);
      return [vie < h ? ahora : vie, sumarDias(vie, 3)];
    }
    case 'semana': return [ahora, sumarDias(h, 8)];
    default: return [ahora, sumarDias(h, 400)];
  }
}

const funcionesEnRango = (obra) => {
  const [desde, hasta] = rangoLimites();
  return obra.futuras.filter((f) => f >= desde && f < hasta);
};

function filtrar() {
  const q = estado.q.trim().toLowerCase();
  return CATALOGO.filter((o) => {
    if (q && !o.buscable.includes(q)) return false;
    if (estado.generos.size && !estado.generos.has(o.genero)) return false;
    if (estado.comunas.size && !estado.comunas.has(o.salaInfo.comuna)) return false;
    if (estado.precioMax < 45000 && (o.precioDesde ?? 0) > estado.precioMax) return false;
    if (estado.duracionMax < 180 && o.duracion && o.duracion > estado.duracionMax) return false;
    if (estado.soloGratis && !o.esGratis) return false;
    if (estado.soloNinos && !o.aptaNinos) return false;
    if (estado.rango === 'gratis' && !o.esGratis) return false;
    if (estado.rango !== 'todo' && estado.rango !== 'gratis' && funcionesEnRango(o).length === 0) return false;
    return true;
  }).sort(ORDENES[estado.orden].fn);
}

function contarFiltrosActivos() {
  let n = estado.generos.size + estado.comunas.size;
  if (estado.precioMax < 45000) n++;
  if (estado.duracionMax < 180) n++;
  if (estado.soloGratis) n++;
  if (estado.soloNinos) n++;
  return n;
}

/* ─────────────────────────────── Render ──────────────────────────────────── */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const ICO = {
  sala: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11z"></path><circle cx="12" cy="10" r="2.4"></circle></svg>',
  reloj: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path></svg>',
};

function etiquetaEstado(o) {
  const h = hoy0();
  const paraEstreno = Math.round((o.inicio - h) / DIA_MS);
  const paraFin = Math.round((o.fin - h) / DIA_MS);
  if (paraEstreno > 0) return { txt: paraEstreno <= 7 ? 'Estrena esta semana' : 'Próximo estreno', clase: 'verde', cinta: 'Estreno' };
  if (paraFin <= 10) return { txt: 'Últimas funciones', clase: 'rojo', cinta: 'Últimas' };
  return null;
}

function temporadaTxt(o) {
  return `${fmtFechaCorta.format(o.inicio).replace('.', '')} – ${fmtFechaCorta.format(o.fin).replace('.', '')}`;
}

function tarjetaHTML(o, indice = 99) {
  const est = etiquetaEstado(o);
  const prox = o.proxima;
  const esHoy = prox && mismaFecha(prox, hoy0());
  return `
  <article class="tarjeta" data-obra="${o.id}" role="button" tabindex="0">
    ${posterHTML(o, { cinta: est ? est.cinta : '', prioritario: indice < 4 })}
    <div class="tarjeta-cuerpo">
      <div class="tarjeta-top">
        <div style="min-width:0">
          <h3>${esc(o.titulo)}</h3>
          ${o.subtitulo ? `<div class="tarjeta-sub">${esc(o.subtitulo)}</div>` : ''}
        </div>
        <button class="fav-btn ${favoritos.has(o.id) ? 'is-fav' : ''}" data-fav="${o.id}"
                aria-label="Guardar ${esc(o.titulo)}" aria-pressed="${favoritos.has(o.id)}">${favoritos.has(o.id) ? '♥' : '♡'}</button>
      </div>
      <div class="meta-linea">${ICO.sala}${esc(o.salaInfo.nombre)} · ${esc(o.salaInfo.comuna)}</div>
      <div class="meta-linea">${ICO.reloj}${o.duracion ? duracionTxt(o.duracion) + ' · ' : ''}${esc(o.generoInfo.nombre)}${o.edad ? ` · +${o.edad}` : ''}</div>
      <div class="tarjeta-pills">
        ${est ? `<span class="pill ${est.clase}">${est.txt}</span>` : ''}
        ${o.esGratis ? '<span class="pill verde">Función gratis</span>' : ''}
        <span class="pill gris">${esc(o.fuente.nombre)}</span>
      </div>
      <div class="proxima">
        ${o.temporada.sinHorario
          ? `<span style="color:var(--texto-3)">Temporada:</span><b>${temporadaTxt(o)}</b>`
          : `<span style="color:var(--texto-3)">Próxima:</span>
             <b style="${esHoy ? 'color:var(--verde)' : ''}">${prox ? `${etiquetaDia(prox)} ${fmtHora.format(prox)}` : '—'}</b>`}
        <span class="precio ${o.precioDesde === 0 ? 'gratis' : ''}">${textoPrecio(o)}</span>
      </div>
    </div>
  </article>`;
}

function destacadaHTML(o, indice = 99) {
  return `
  <article class="destacada" data-obra="${o.id}" role="button" tabindex="0">
    ${posterHTML(o, { prioritario: indice < 3 })}
    <div class="destacada-info">
      <h3>${esc(o.titulo)}</h3>
      <div class="destacada-meta">
        <span>${o.generoInfo.emoji} ${esc(o.generoInfo.nombre)}</span><span>·</span>
        <span>${esc(o.salaInfo.comuna)}</span><span>·</span>
        <span style="color:var(--oro)">${o.proxima ? etiquetaDia(o.proxima) : temporadaTxt(o)}</span>
      </div>
    </div>
  </article>`;
}

const TITULOS_RANGO = {
  todo: 'Todas las obras', hoy: 'Funciones de hoy', manana: 'Funciones de mañana',
  finde: 'Este fin de semana', semana: 'Próximos 7 días', gratis: 'Gratis o a la gorra',
};

function renderCartelera() {
  const obras = filtrar();
  const limpio = !estado.q && contarFiltrosActivos() === 0 && estado.rango === 'todo';

  // "En cartelera ahora": lo que ya está en funciones y se acaba pronto o recién estrena
  const destacadas = limpio
    ? CATALOGO.filter((o) => o.proxima && o.inicio <= hoy0()).sort((a, b) => a.proxima - b.proxima).slice(0, 10)
    : [];
  $('#destacadas-wrap').classList.toggle('hidden', destacadas.length === 0);
  $('#destacadas').innerHTML = destacadas.map((o, i) => destacadaHTML(o, i)).join('');

  $('#lista-titulo').textContent = `${TITULOS_RANGO[estado.rango]} · ${obras.length}`;
  $('#lista').innerHTML = obras.map((o, i) => tarjetaHTML(o, i)).join('');
  $('#vacio').classList.toggle('hidden', obras.length > 0);

  const n = contarFiltrosActivos();
  $('#filtros-badge').textContent = n;
  $('#filtros-badge').classList.toggle('hidden', n === 0);
  $('#orden-label').textContent = ORDENES[estado.orden].label;
}

function renderAgenda() {
  const cont = $('#agenda');
  const obras = filtrar();
  const h = hoy0(), ahora = new Date();
  const dias = [];

  for (let i = 0; i < 21; i++) {
    const dia = sumarDias(h, i);
    const eventos = [];
    obras.forEach((o) => o.futuras.forEach((f) => {
      if (mismaFecha(f, dia) && f >= ahora) eventos.push({ obra: o, fecha: f });
    }));
    if (eventos.length) { eventos.sort((a, b) => a.fecha - b.fecha); dias.push({ dia, eventos }); }
  }

  if (!dias.length) {
    cont.innerHTML = `<p class="vacio"><span class="vacio-emoji">📭</span><strong>No hay funciones en los próximos 21 días</strong><span>con los filtros actuales.</span></p>`;
    return;
  }

  cont.innerHTML = dias.map(({ dia, eventos }) => `
    <section class="dia-grupo">
      <div class="dia-cabecera">
        <div class="dia-num">${dia.getDate()}<small>${fmtMesCorto.format(dia).replace('.', '')}</small></div>
        <div>
          <div class="dia-nombre">${etiquetaDia(dia, true)}</div>
          <div class="dia-cuenta">${eventos.length} ${eventos.length === 1 ? 'función' : 'funciones'}</div>
        </div>
      </div>
      ${eventos.map(({ obra, fecha }) => `
        <button class="agenda-item" data-obra="${obra.id}" type="button">
          <span class="agenda-hora">${fmtHora.format(fecha)}</span>
          <span class="agenda-info">
            <h4>${esc(obra.titulo)}</h4>
            <span>${esc(obra.salaInfo.nombre)} · ${textoPrecio(obra)}</span>
          </span>
          <span style="color:var(--texto-3)">›</span>
        </button>`).join('')}
    </section>`).join('');
}

function renderFavoritos() {
  const obras = CATALOGO.filter((o) => favoritos.has(o.id)).sort(ORDENES.fecha.fn);
  $('#lista-favoritos').innerHTML = obras.map((o, i) => tarjetaHTML(o, i)).join('');
  $('#vacio-favoritos').classList.toggle('hidden', obras.length > 0);
}

function actualizarBadgeFav() {
  const badge = $('#tab-badge-fav');
  badge.textContent = favoritos.size;
  badge.classList.toggle('hidden', favoritos.size === 0);
}

function renderTodo() {
  if (estado.vista === 'cartelera') renderCartelera();
  if (estado.vista === 'agenda') renderAgenda();
  if (estado.vista === 'favoritos') renderFavoritos();
  actualizarBadgeFav();
}

/* ────────────────────────── Detalle de la obra ───────────────────────────── */

const iniciales = (n) => n.split(/\s+/).filter((p) => p.length > 2).slice(0, 2).map((p) => p[0].toUpperCase()).join('') || '★';

function detalleHTML(o) {
  const est = etiquetaEstado(o);
  const proximas = o.futuras.slice(0, 8);
  const esFav = favoritos.has(o.id);
  const mapa = `https://maps.apple.com/?q=${encodeURIComponent(o.salaInfo.mapa || o.salaInfo.nombre)}`;

  const fichaDatos = [
    ['Género', `${o.generoInfo.emoji} ${o.generoInfo.nombre}`],
    o.duracion ? ['Duración', duracionTxt(o.duracion)] : null,
    o.director ? ['Dirección', o.director] : null,
    o.edad ? ['Edad', `Desde ${o.edad} años`] : null,
    o.compania ? ['Compañía', o.compania] : null,
    o.dramaturgo ? ['Dramaturgia', o.dramaturgo] : null,
  ].filter(Boolean);

  return `
  <div class="detalle-hero">${posterHTML(o, { cinta: est ? est.txt : '', alto: true })}</div>

  <div class="detalle-cabecera">
    ${o.subtitulo ? `<div class="detalle-sub">${esc(o.subtitulo)}</div>` : ''}
    <div class="detalle-compania">${esc(o.salaInfo.nombre)} · ${esc(o.salaInfo.comuna)}</div>
  </div>

  <div class="detalle-acciones">
    <button class="accion ${esFav ? 'is-fav' : ''}" data-fav="${o.id}" type="button">
      <span style="font-size:17px">${esFav ? '♥' : '♡'}</span>${esFav ? 'Guardada' : 'Guardar'}
    </button>
    <button class="accion" data-compartir="${o.id}" type="button">
      <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V4"></path><path d="m8 8 4-4 4 4"></path><path d="M5 13v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"></path></svg>Compartir
    </button>
    ${o.entradas ? `<a class="accion destacado" href="${esc(o.entradas)}" target="_blank" rel="noopener">
      <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9a2 2 0 0 0 0 6v2a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2a2 2 0 0 1 0-6V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2z"></path><path d="M14 5v14"></path></svg>Entradas
    </a>` : ''}
  </div>

  ${o.sinopsis ? `<div class="detalle-seccion"><h3>De qué va</h3><p class="sinopsis">${esc(o.sinopsis)}</p></div>` : ''}

  <div class="detalle-seccion">
    <h3>Ficha</h3>
    <div class="datos-grid">
      ${fichaDatos.map(([k, v], i) => `<div class="dato" ${i >= 4 ? 'style="grid-column:1/-1"' : ''}><small>${esc(k)}</small><b>${esc(v)}</b></div>`).join('')}
    </div>
  </div>

  ${o.elenco.length ? `
  <div class="detalle-seccion">
    <h3>Elenco</h3>
    <div class="elenco">
      ${o.elenco.map((p) => `<div class="elenco-fila"><span class="avatar">${esc(iniciales(p))}</span><span>${esc(p)}</span></div>`).join('')}
    </div>
  </div>` : ''}

  <div class="detalle-seccion">
    <h3>Precios</h3>
    ${o.precios.length
      ? o.precios.map((p) => `<div class="precio-fila"><span>${esc(p.tipo)}</span><b class="${p.valor === 0 ? 'gratis' : ''}">${precioCLP(p.valor)}</b></div>`).join('')
      : `<p class="sinopsis" style="font-size:14px;color:var(--texto-2)">El teatro todavía no publica los precios. Revísalos en su sitio.</p>`}
  </div>

  <div class="detalle-seccion">
    <h3>Funciones</h3>
    <div class="temporada-linea">Temporada del ${esc(temporadaTxt(o))} · ${o.futuras.length ? `${o.futuras.length} funciones por delante` : 'consulta los horarios'}</div>
    ${o.horarioTexto ? `<div class="horario-oficial">🕐 ${esc(o.horarioTexto)}</div>` : ''}
    ${proximas.map((f, i) => `
      <div class="func-fila">
        <div class="func-fecha">
          <b>${etiquetaDia(f, true)}</b>
          <small>${fmtHora.format(f)} h · ${esc(o.salaInfo.nombre)}</small>
        </div>
        <button class="func-cal" data-ics="${o.id}" data-idx="${i}" type="button" aria-label="Agregar al calendario">
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="3"></rect><path d="M8 3v4M16 3v4M3 10h18M12 14v4M10 16h4"></path></svg>
        </button>
      </div>`).join('')}
  </div>

  <div class="detalle-seccion">
    <h3>Dónde</h3>
    <a class="sala-card" href="${esc(mapa)}" target="_blank" rel="noopener">
      <b>${esc(o.salaInfo.nombre)}</b>
      <span>${esc(o.salaInfo.direccion)}, ${esc(o.salaInfo.comuna)}</span>
      ${o.salaInfo.metro && o.salaInfo.metro !== '—' ? `<span>🚇 Metro ${esc(o.salaInfo.metro)}</span>` : ''}
      <span class="ir">Abrir en Mapas ›</span>
    </a>
  </div>

  <div class="detalle-seccion">
    <h3>Información oficial</h3>
    <a class="sala-card" href="${esc(o.url)}" target="_blank" rel="noopener">
      <b>Ver esta obra en ${esc(o.fuente.nombre)}</b>
      <span>Sinopsis, elenco, precios y entradas, directo del teatro.</span>
      <span class="ir">${esc((o.url || '').replace(/^https?:\/\/(www\.)?/, '').split('/')[0])} ›</span>
    </a>
    <p class="credito-foto">Afiche y datos: ${esc(o.fuente.nombre)}. Confirma horarios y precios en el sitio del teatro antes de ir.</p>
  </div>`;
}

/* ─────────────────────────── Calendario (.ics) ───────────────────────────── */

const icsFecha = (d) => { const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}T${p(d.getHours())}${p(d.getMinutes())}00`; };

const icsEsc = (s) => String(s).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');

function icsPlegar(linea) {
  if (new TextEncoder().encode(linea).length <= 75) return linea;
  const partes = []; let actual = '', usados = 0;
  for (const ch of linea) {
    const n = new TextEncoder().encode(ch).length;
    if (usados + n > (partes.length ? 74 : 75)) { partes.push(actual); actual = ''; usados = 0; }
    actual += ch; usados += n;
  }
  if (actual) partes.push(actual);
  return partes.join('\r\n ');
}

function descargarICS(obra, fecha) {
  const fin = new Date(fecha.getTime() + (obra.duracion || 90) * 60000);
  const ubic = `${obra.salaInfo.nombre}, ${obra.salaInfo.direccion}, ${obra.salaInfo.comuna}`;
  const desc = [obra.sinopsis, '',
    obra.compania ? `Compañía: ${obra.compania}` : null,
    obra.director ? `Dirección: ${obra.director}` : null,
    obra.duracion ? `Duración: ${duracionTxt(obra.duracion)}` : null,
    obra.precios.length ? `Entradas ${textoPrecio(obra).toLowerCase()}` : null,
    obra.url].filter(Boolean).join('\n');

  const ics = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Telon Santiago//ES', 'CALSCALE:GREGORIAN',
    'BEGIN:VEVENT', `UID:${obra.id}-${icsFecha(fecha)}@telon.local`, `DTSTAMP:${icsFecha(new Date())}`,
    `DTSTART:${icsFecha(fecha)}`, `DTEND:${icsFecha(fin)}`, `SUMMARY:🎭 ${icsEsc(obra.titulo)}`,
    `LOCATION:${icsEsc(ubic)}`, `DESCRIPTION:${icsEsc(desc)}`,
    'BEGIN:VALARM', 'TRIGGER:-PT2H', 'ACTION:DISPLAY', `DESCRIPTION:${icsEsc(obra.titulo)} en 2 horas`, 'END:VALARM',
    'END:VEVENT', 'END:VCALENDAR'].map(icsPlegar).join('\r\n');

  const url = URL.createObjectURL(new Blob([ics], { type: 'text/calendar;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = `${obra.id}-${claveDia(fecha)}.ics`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  toast('Evento listo · ábrelo para agregarlo al Calendario');
}

/* ────────────────────────────── Hojas modales ────────────────────────────── */

let hojaAbierta = null;

function abrirHoja(id) {
  if (hojaAbierta) cerrarHoja(true);
  const hoja = document.getElementById(id);
  hoja.hidden = false;
  $('#sheet-backdrop').hidden = false;
  document.body.classList.add('no-scroll');
  hoja.querySelector('.sheet-scroll').scrollTop = 0;
  hojaAbierta = id;
  history.pushState({ hoja: id }, '');
}

function cerrarHoja(inmediato = false) {
  if (!hojaAbierta) return;
  const hoja = document.getElementById(hojaAbierta);
  hojaAbierta = null;
  const terminar = () => {
    hoja.hidden = true;
    hoja.classList.remove('cerrando');
    hoja.style.transform = '';
    $('#sheet-backdrop').hidden = true;
    document.body.classList.remove('no-scroll');
  };
  if (inmediato) return terminar();
  hoja.classList.add('cerrando');
  setTimeout(terminar, 210);
}

function pedirCierre() {
  if (hojaAbierta && history.state && history.state.hoja) history.back();
  else cerrarHoja();
}

window.addEventListener('popstate', () => { if (hojaAbierta) cerrarHoja(); });

function abrirObra(id) {
  const o = CATALOGO.find((x) => x.id === id);
  if (!o) return;
  $('#sheet-contenido').innerHTML = detalleHTML(o);
  abrirHoja('sheet-obra');
}

$$('.sheet').forEach((hoja) => {
  let y0 = null, dy = 0;
  const grab = hoja.querySelector('.sheet-grab');
  grab.addEventListener('touchstart', (e) => { y0 = e.touches[0].clientY; dy = 0; hoja.style.transition = 'none'; }, { passive: true });
  grab.addEventListener('touchmove', (e) => {
    if (y0 === null) return;
    dy = Math.max(0, e.touches[0].clientY - y0);
    hoja.style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  grab.addEventListener('touchend', () => {
    if (y0 === null) return;
    hoja.style.transition = 'transform .22s ease';
    hoja.style.transform = '';
    if (dy > 90) pedirCierre();
    y0 = null;
  });
});

/* ──────────────────────────────── Toast ──────────────────────────────────── */

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('visible'), 2600);
}

/* ─────────────────────────────── Favoritos ───────────────────────────────── */

function alternarFavorito(id) {
  const o = CATALOGO.find((x) => x.id === id);
  if (favoritos.has(id)) { favoritos.delete(id); toast('Quitada de tu cartelera'); }
  else {
    favoritos.add(id);
    toast(`“${o.titulo}” guardada 🎭`);
    if (navigator.vibrate) navigator.vibrate(12);
  }
  localStorage.setItem(LS_FAV, JSON.stringify([...favoritos]));

  $$(`[data-fav="${id}"]`).forEach((btn) => {
    const activo = favoritos.has(id);
    btn.classList.toggle('is-fav', activo);
    btn.setAttribute('aria-pressed', activo);
    if (btn.classList.contains('fav-btn')) btn.textContent = activo ? '♥' : '♡';
    else btn.innerHTML = `<span style="font-size:17px">${activo ? '♥' : '♡'}</span>${activo ? 'Guardada' : 'Guardar'}`;
  });
  actualizarBadgeFav();
  if (estado.vista === 'favoritos') renderFavoritos();
}

/* ─────────────────────────────── Compartir ───────────────────────────────── */

async function compartir(id) {
  const o = CATALOGO.find((x) => x.id === id);
  const texto = `🎭 ${o.titulo}\n${o.salaInfo.nombre}, ${o.salaInfo.comuna}\n${o.proxima ? etiquetaDia(o.proxima, true) + ' a las ' + fmtHora.format(o.proxima) : temporadaTxt(o)}\n${textoPrecio(o)}\n${o.url}`;
  try {
    if (navigator.share) await navigator.share({ title: o.titulo, text: texto });
    else { await navigator.clipboard.writeText(texto); toast('Copiado al portapapeles'); }
  } catch { /* cancelado */ }
}

/* ──────────────────────────── Filtros: interfaz ──────────────────────────── */

function pintarFiltros() {
  $('#filtro-generos').innerHTML = GENEROS
    .filter((g) => CATALOGO.some((o) => o.genero === g.id))
    .map((g) => `<button class="chip ${estado.generos.has(g.id) ? 'is-active' : ''}" data-genero="${g.id}" type="button">${g.emoji} ${g.nombre}</button>`).join('');
  $('#filtro-comunas').innerHTML = COMUNAS
    .map((c) => `<button class="chip ${estado.comunas.has(c) ? 'is-active' : ''}" data-comuna="${esc(c)}" type="button">${esc(c)}</button>`).join('');
  $('#filtro-precio').value = estado.precioMax;
  $('#filtro-duracion').value = estado.duracionMax;
  $('#f-gratis').checked = estado.soloGratis;
  $('#f-ninos').checked = estado.soloNinos;
  actualizarEtiquetasFiltro();
}

function actualizarEtiquetasFiltro() {
  const p = Number($('#filtro-precio').value);
  $('#precio-valor').textContent = p >= 45000 ? 'Sin límite' : `Hasta ${precioCLP(p)}`;
  const d = Number($('#filtro-duracion').value);
  $('#duracion-valor').textContent = d >= 180 ? 'Sin límite' : `Hasta ${duracionTxt(d)}`;
  $('#btn-aplicar-n').textContent = filtrar().length;
}

function limpiarFiltros() {
  estado.generos.clear(); estado.comunas.clear();
  estado.precioMax = 45000; estado.duracionMax = 180;
  estado.soloGratis = estado.soloAccesible = estado.soloNinos = false;
  pintarFiltros(); renderTodo();
}

/* ───────────────────────────── Eventos globales ──────────────────────────── */

document.addEventListener('click', (e) => {
  const fav = e.target.closest('[data-fav]');
  if (fav) { e.stopPropagation(); alternarFavorito(fav.dataset.fav); return; }

  const comp = e.target.closest('[data-compartir]');
  if (comp) { compartir(comp.dataset.compartir); return; }

  const ics = e.target.closest('[data-ics]');
  if (ics) {
    const o = CATALOGO.find((x) => x.id === ics.dataset.ics);
    descargarICS(o, o.futuras[Number(ics.dataset.idx)]);
    return;
  }

  const card = e.target.closest('[data-obra]');
  if (card && !e.target.closest('a')) abrirObra(card.dataset.obra);
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && hojaAbierta) pedirCierre();
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-obra][role="button"]')) {
    e.preventDefault(); abrirObra(e.target.dataset.obra);
  }
});

$('#sheet-backdrop').addEventListener('click', pedirCierre);

let debounce;
$('#q').addEventListener('input', (e) => {
  estado.q = e.target.value;
  $('#q-clear').classList.toggle('hidden', !estado.q);
  clearTimeout(debounce);
  debounce = setTimeout(renderTodo, 130);
});
$('#q-clear').addEventListener('click', () => {
  estado.q = ''; $('#q').value = ''; $('#q-clear').classList.add('hidden'); renderTodo();
});

$('#chips-fecha').addEventListener('click', (e) => {
  const chip = e.target.closest('.chip');
  if (!chip) return;
  $$('#chips-fecha .chip').forEach((c) => c.classList.toggle('is-active', c === chip));
  estado.rango = chip.dataset.rango;
  renderTodo();
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

$('#btn-orden').addEventListener('click', () => {
  estado.orden = CICLO_ORDEN[(CICLO_ORDEN.indexOf(estado.orden) + 1) % CICLO_ORDEN.length];
  renderTodo();
  toast(`Ordenado: ${ORDENES[estado.orden].label.toLowerCase()}`);
});

$('#tabbar').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  estado.vista = tab.dataset.vista;
  $$('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
  $$('.vista').forEach((v) => v.classList.toggle('is-active', v.id === `vista-${estado.vista}`));
  renderTodo();
  window.scrollTo({ top: 0 });
});

$('#btn-filtros').addEventListener('click', () => { pintarFiltros(); abrirHoja('sheet-filtros'); });
$('#btn-info').addEventListener('click', () => abrirHoja('sheet-info'));
$('#btn-aplicar').addEventListener('click', pedirCierre);
$('#btn-limpiar').addEventListener('click', limpiarFiltros);
$('#btn-reset-vacio').addEventListener('click', () => {
  limpiarFiltros();
  estado.q = ''; $('#q').value = ''; $('#q-clear').classList.add('hidden');
  estado.rango = 'todo';
  $$('#chips-fecha .chip').forEach((c) => c.classList.toggle('is-active', c.dataset.rango === 'todo'));
  renderTodo();
});

$('#filtro-generos').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-genero]');
  if (!chip) return;
  const g = chip.dataset.genero;
  estado.generos.has(g) ? estado.generos.delete(g) : estado.generos.add(g);
  chip.classList.toggle('is-active');
  actualizarEtiquetasFiltro(); renderTodo();
});

$('#filtro-comunas').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-comuna]');
  if (!chip) return;
  const c = chip.dataset.comuna;
  estado.comunas.has(c) ? estado.comunas.delete(c) : estado.comunas.add(c);
  chip.classList.toggle('is-active');
  actualizarEtiquetasFiltro(); renderTodo();
});

$('#filtro-precio').addEventListener('input', (e) => { estado.precioMax = Number(e.target.value); actualizarEtiquetasFiltro(); renderTodo(); });
$('#filtro-duracion').addEventListener('input', (e) => { estado.duracionMax = Number(e.target.value); actualizarEtiquetasFiltro(); renderTodo(); });
$('#f-gratis').addEventListener('change', (e) => { estado.soloGratis = e.target.checked; actualizarEtiquetasFiltro(); renderTodo(); });
$('#f-ninos').addEventListener('change', (e) => { estado.soloNinos = e.target.checked; actualizarEtiquetasFiltro(); renderTodo(); });

/* ───────────────────────────────── Arranque ──────────────────────────────── */

function pintarEstadoDatos() {
  const { actualizado, fuentes } = CARTELERA;
  $('#sello-actualizado').textContent = actualizado ? `Cartelera actualizada ${haceCuanto(actualizado)}` : '';
  $('#info-fuentes').innerHTML = (fuentes || []).map((f) =>
    `<li><a href="${esc(f.sitio)}" target="_blank" rel="noopener">${esc(f.nombre)}</a> — ${f.obras} ${f.obras === 1 ? 'obra' : 'obras'}${f.error ? ' <em>(no respondió en la última revisión)</em>' : ''}</li>`).join('');
  $('#info-actualizado').textContent = actualizado
    ? new Intl.DateTimeFormat('es-CL', { dateStyle: 'full', timeStyle: 'short' }).format(new Date(actualizado))
    : '—';
}

function mostrarError(msg) {
  $('#lista').innerHTML = '';
  $('#destacadas-wrap').classList.add('hidden');
  $('#vacio').classList.remove('hidden');
  $('#vacio').innerHTML = `<span class="vacio-emoji">📡</span><strong>No se pudo cargar la cartelera</strong>
    <span>${esc(msg)}</span><span style="font-size:12.5px">Ejecuta <code>node tools/actualizar.mjs</code> para generarla.</span>`;
}

async function arrancar() {
  try {
    const res = await fetch('assets/cartelera.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error(`No se encontró assets/cartelera.json (HTTP ${res.status})`);
    CARTELERA = await res.json();
  } catch (e) {
    // La versión de un solo archivo trae la cartelera incrustada
    if (window.CARTELERA_INCRUSTADA) CARTELERA = window.CARTELERA_INCRUSTADA;
    else return mostrarError(e.message);
  }

  prepararCatalogo(CARTELERA);
  pintarEstadoDatos();
  pintarFiltros();

  const h = (location.hash || '').replace('#', '');
  if (h === 'hoy') {
    estado.rango = 'hoy';
    $$('#chips-fecha .chip').forEach((c) => c.classList.toggle('is-active', c.dataset.rango === 'hoy'));
  } else if (h === 'favoritos' || h === 'agenda') {
    estado.vista = h;
    $$('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.vista === h));
    $$('.vista').forEach((v) => v.classList.toggle('is-active', v.id === `vista-${h}`));
  }

  renderTodo();
}

arrancar();

document.addEventListener('visibilitychange', () => { if (!document.hidden && CATALOGO.length) { prepararCatalogo(CARTELERA); renderTodo(); } });

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}
