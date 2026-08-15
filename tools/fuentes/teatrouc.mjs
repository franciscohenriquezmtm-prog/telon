/* Fuente: Teatro UC — https://teatrouc.uc.cl
   Usa WordPress con el plugin The Events Calendar, que expone una API REST pública.
   El lector vive en lib/tribe.mjs porque otros teatros usan el mismo plugin. */

import { leerEventsCalendar } from '../lib/tribe.mjs';

export const meta = { id: 'teatrouc', nombre: 'Teatro UC', sitio: 'https://teatrouc.uc.cl' };

export const obtener = ({ log }) => leerEventsCalendar({
  api: 'https://teatrouc.uc.cl/wp-json/tribe/events/v1/events',
  idFuente: 'teatrouc',
  sala: 'teatro-uc',
  log,
});
