/* Servidor estático mínimo para probar la app en el computador o en el iPhone.
   Uso:  node tools/server.mjs        → http://localhost:5173
   Muestra también la IP local, para abrirla desde el teléfono en la misma wifi. */

import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { networkInterfaces } from 'node:os';

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), '..');
const PUERTO = Number(process.env.PORT) || 5173;

const TIPOS = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ics': 'text/calendar; charset=utf-8',
};

createServer(async (req, res) => {
  try {
    let ruta = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (ruta === '/') ruta = '/index.html';
    const archivo = join(RAIZ, normalize(ruta).replace(/^(\.\.[/\\])+/, ''));
    if (!archivo.startsWith(RAIZ)) { res.writeHead(403).end('Prohibido'); return; }

    await stat(archivo);
    const datos = await readFile(archivo);
    res.writeHead(200, {
      'Content-Type': TIPOS[extname(archivo).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
    });
    res.end(datos);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<h1>404</h1>');
  }
}).listen(PUERTO, '0.0.0.0', () => {
  const ips = Object.values(networkInterfaces()).flat()
    .filter((i) => i && i.family === 'IPv4' && !i.internal).map((i) => i.address);
  console.log(`\n  🎭 Telón corriendo\n`);
  console.log(`     En este equipo:  http://localhost:${PUERTO}`);
  ips.forEach((ip) => console.log(`     En tu iPhone:    http://${ip}:${PUERTO}`));
  console.log('');
});
