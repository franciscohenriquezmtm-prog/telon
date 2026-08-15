/* Datos fijos de cada sala. Los sitios entregan el nombre de la sala, pero rara vez
   la comuna o el metro; esto los completa. Si aparece una sala nueva, el actualizador
   la agrega igual (sin metro) y queda anotada en el informe para completarla acá. */

export const SALAS = {
  'gam': {
    nombre: 'Centro GAM', comuna: 'Santiago Centro',
    direccion: 'Av. Libertador Bernardo O’Higgins 227', metro: 'Universidad Católica (L1)',
    mapa: 'Centro Cultural Gabriela Mistral, Santiago', sitio: 'https://www.gam.cl',
  },
  'teatro-uc': {
    nombre: 'Teatro UC', comuna: 'Ñuñoa',
    direccion: 'Jorge Washington 26', metro: 'Plaza Ñuñoa (L3)',
    mapa: 'Teatro UC, Santiago', sitio: 'https://teatrouc.uc.cl',
  },
  'mori-bellavista': {
    nombre: 'Teatro Mori Bellavista', comuna: 'Providencia',
    direccion: 'Constitución 183', metro: 'Baquedano (L1/L5)',
    mapa: 'Teatro Mori Bellavista', sitio: 'https://teatromori.cl',
  },
  'mori-parque-arauco': {
    nombre: 'Teatro Mori Parque Arauco', comuna: 'Las Condes',
    direccion: 'Av. Kennedy 5413, nivel 3', metro: 'Manquehue (L1)',
    mapa: 'Teatro Mori Parque Arauco', sitio: 'https://teatromori.cl',
  },
  'mori-recoleta': {
    nombre: 'Teatro Mori Recoleta', comuna: 'Recoleta',
    direccion: 'Antonia López de Bello 0180', metro: 'Patronato (L3)',
    mapa: 'Teatro Mori Recoleta', sitio: 'https://teatromori.cl',
  },
  'mori-vitacura': {
    nombre: 'Teatro Mori Vitacura', comuna: 'Vitacura',
    direccion: 'Av. Nueva Costanera 3736', metro: '—',
    mapa: 'Teatro Mori Vitacura', sitio: 'https://teatromori.cl',
  },

  /* Salas agregadas después. La dirección y el metro son los que publica cada
     teatro en su propio sitio (pie de página o ficha de contacto); donde el sitio
     no nombra la estación, va "—" antes que suponerla. */

  'matucana-100': {
    nombre: 'Matucana 100', comuna: 'Estación Central',
    direccion: 'Av. Matucana 100', metro: 'Quinta Normal (L5) / Estación Central (L1)',
    mapa: 'Centro Cultural Matucana 100', sitio: 'https://www.m100.cl',
  },
  'teatro-del-puente': {
    nombre: 'Teatro del Puente', comuna: 'Santiago Centro',
    direccion: 'Parque Forestal s/n, entre los puentes Pío Nono y Purísima', metro: 'Baquedano (L1/L5)',
    mapa: 'Teatro del Puente, Santiago', sitio: 'https://teatrodelpuente.cl',
  },
  'finis-terrae': {
    nombre: 'Teatro Finis Terrae', comuna: 'Providencia',
    direccion: 'Av. Pocuro 1935', metro: '—',
    mapa: 'Teatro Finis Terrae, Providencia', sitio: 'https://teatrofinisterrae.cl',
  },
  'nescafe-de-las-artes': {
    nombre: 'Teatro Nescafé de las Artes', comuna: 'Providencia',
    direccion: 'Manuel Montt 032', metro: '—',
    mapa: 'Teatro Nescafé de las Artes', sitio: 'https://www.teatro-nescafe-delasartes.cl',
  },
  'teatro-zoco': {
    nombre: 'Centro para las Artes Zoco', comuna: 'Lo Barnechea',
    direccion: 'Av. La Dehesa 1500, piso -2 (local 30)', metro: '—',
    mapa: 'Zoco, La Dehesa 1500, Lo Barnechea', sitio: 'https://teatrozoco.cl',
  },
  'teatro-ictus': {
    nombre: 'Teatro Ictus — Sala La Comedia', comuna: 'Santiago Centro',
    direccion: 'Merced 349, Barrio Lastarria', metro: '—',
    mapa: 'Teatro Ictus, Merced 349, Santiago', sitio: 'https://teatroictus.cl',
  },
  'tm-las-condes': {
    nombre: 'Teatro Municipal de Las Condes', comuna: 'Las Condes',
    direccion: 'Av. Apoquindo 3300', metro: '—',
    mapa: 'Teatro Municipal de Las Condes', sitio: 'https://www.tmlascondes.cl',
  },
};

/** Resuelve la sala de una obra: usa la ficha del catálogo y le agrega el nombre exacto
    de la sala interior (Sala A2, Sala Ana González, etc.) cuando el sitio lo informa. */
export function resolverSala(claveBase, salaInterior) {
  const base = SALAS[claveBase] || { nombre: claveBase, comuna: '—', direccion: '', metro: '', mapa: claveBase };
  if (!salaInterior) return { ...base };
  const limpia = salaInterior.replace(/\s+/g, ' ').trim();
  if (!limpia || limpia.toLowerCase() === base.nombre.toLowerCase()) return { ...base };
  return { ...base, nombre: `${base.nombre} — ${limpia}` };
}
