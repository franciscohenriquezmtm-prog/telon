/* Prueba del lector de días y horarios con los formatos REALES de cada teatro.
   Uso:  node tools/probar-horarios.mjs   (termina con error si algún caso falla)

   Cada caso salió de una ficha que el lector leyó mal alguna vez, o de un formato
   que hoy funciona y no se puede romper. Si un teatro estrena un formato nuevo,
   agrégalo acá antes de tocar parseHorarios. */

import { parseHorarios } from './lib/util.mjs';

const D = ['do', 'lu', 'ma', 'mi', 'ju', 'vi', 'sa'];
const comoTexto = (r) => (r ? r.dias.map((d) => `${D[d]}=${r.horas[d]}`).join(' ') : 'sin horario');

const CASOS = [
  // Teatro UC — antes salía mié, jue y sáb (faltaba el viernes)
  ['Miércoles a sábado, a las 20:00 horas.', 'mi=20:00 ju=20:00 vi=20:00 sa=20:00'],
  ['De miércoles a sábado a las 20:30 horas.', 'mi=20:30 ju=20:30 vi=20:30 sa=20:30'],
  // Finis Terrae
  ['Jue. y vie. 20:30 hrs.\nSáb. y dom 19 hrs.', 'do=19:00 ju=20:30 vi=20:30 sa=19:00'],
  // GAM — antes inventaba un domingo en las dos últimas
  ['Ju a Sá— 19.30 h\nDo— 18.30 h', 'do=18:30 ju=19:30 vi=19:30 sa=19:30'],
  ['Vi— 19 h\nSá y Do— 17 h', 'do=17:00 vi=19:00 sa=17:00'],
  ['Ju, Vi y Sá — 19 h', 'ju=19:00 vi=19:00 sa=19:00'],
  ['Horario: 10 a 13 h y 15 a 18 h\nJu y Sá — 19 h', 'ju=19:00 sa=19:00'],
  ['Ju y Vi— 19.30 h / Sá y Do— 18.30 h', 'do=18:30 ju=19:30 vi=19:30 sa=18:30'],
  // Mori
  ['De jueves a sábado a las 20:00 horas', 'ju=20:00 vi=20:00 sa=20:00'],
  // Zoco
  ['De jueves a sábados 20:00 hrs, domingo 19:00 hrs.', 'do=19:00 ju=20:00 vi=20:00 sa=20:00'],
  // Días mencionados en otros párrafos de la página no deben colarse
  ['El sábado pasado estrenamos. El domingo hubo prensa.\nMiércoles a sábado, a las 20:00 horas.',
    'mi=20:00 ju=20:00 vi=20:00 sa=20:00'],
  // Hora sin días: NO se inventan días (Zoco, "PULSIÓN")
  ['Fecha: del 24 de noviembre al 17 de diciembre\nHorarios: 20:00 hrs.', 'sin horario'],
  ['Texto sin horarios', 'sin horario'],
];

let fallas = 0;
for (const [texto, esperado] of CASOS) {
  const obtenido = comoTexto(parseHorarios(texto));
  const ok = obtenido === esperado;
  if (!ok) fallas++;
  console.log(`${ok ? '✓' : '✗'} ${JSON.stringify(texto).slice(0, 62).padEnd(64)} → ${obtenido}${ok ? '' : `   (esperado: ${esperado})`}`);
}
console.log(`\n${CASOS.length - fallas}/${CASOS.length} correctos`);
if (fallas) process.exitCode = 1;
