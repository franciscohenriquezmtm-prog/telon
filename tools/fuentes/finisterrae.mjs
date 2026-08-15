/* Fuente: Teatro Finis Terrae — https://teatrofinisterrae.cl
   Mismo plugin que Teatro UC (The Events Calendar), así que reusa el mismo lector.
   Ojo: el sitio responde en el dominio sin "www". */

import { leerEventsCalendar } from '../lib/tribe.mjs';

export const meta = { id: 'finisterrae', nombre: 'Teatro Finis Terrae', sitio: 'https://teatrofinisterrae.cl' };

export const obtener = ({ log }) => leerEventsCalendar({
  api: 'https://teatrofinisterrae.cl/wp-json/tribe/events/v1/events',
  idFuente: 'finisterrae',
  sala: 'finis-terrae',
  log,
});
