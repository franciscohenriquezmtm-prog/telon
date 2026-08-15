/* Empaqueta toda la app en un único HTML autocontenido (sin service worker).
   La cartelera va incrustada; los afiches quedan enlazados al sitio del teatro,
   así el archivo pesa poco y sigue mostrando las fotos con internet.

   Uso:  node tools/build-single.mjs   →  dist/telon.html + dist/telon-artifact.html */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, dirname, basename, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), '..');
const leer = (p) => readFileSync(join(RAIZ, p), 'utf8');

const html = leer('index.html');
const css = leer('assets/styles.css');
const app = leer('assets/app.js');
const cartelera = JSON.parse(leer('assets/cartelera.json'));

/* Los afiches van incrustados como data URI: así se ven aunque el archivo se abra
   donde no se permiten imágenes externas (por ejemplo, publicado como artifact).
   Se usa la miniatura si existe; si no, la foto completa. */
let pesoFotos = 0, conFoto = 0;

function fotoIncrustada(obra) {
  if (!obra.imagenLocal) return null;
  const nombre = basename(obra.imagenLocal);
  const mini = join(RAIZ, 'assets', 'img', 'mini', basename(nombre, extname(nombre)) + '.jpg');
  const completa = join(RAIZ, obra.imagenLocal);
  const archivo = existsSync(mini) ? mini : (existsSync(completa) ? completa : null);
  if (!archivo) return null;

  const datos = readFileSync(archivo);
  pesoFotos += datos.length;
  conFoto++;
  const tipo = archivo.endsWith('.png') ? 'image/png' : archivo.endsWith('.webp') ? 'image/webp' : 'image/jpeg';
  return `data:${tipo};base64,${datos.toString('base64')}`;
}

const incrustada = {
  ...cartelera,
  obras: cartelera.obras.map((o) => ({ ...o, imagenLocal: fotoIncrustada(o) })),
};

const js = app.replace(
  /if \('serviceWorker' in navigator\) \{[\s\S]*?\n\}/,
  '/* sin service worker en la versión de un archivo */'
);

const cuerpo = html
  .replace(/[\s\S]*<body>/, '')
  .replace(/<\/body>[\s\S]*/, '')
  .replace(/<script type="module"[^>]*><\/script>/, '');

const datos = `<script>window.CARTELERA_INCRUSTADA = ${JSON.stringify(incrustada)};</script>`;
const icono = readFileSync(join(RAIZ, 'assets/icon-180.png')).toString('base64');

const salida = `<!DOCTYPE html>
<html lang="es-CL">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=5">
<title>Telón · Cartelera de teatro en Santiago</title>
<meta name="theme-color" content="#12060a">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Telón">
<link rel="apple-touch-icon" href="data:image/png;base64,${icono}">
<style>
${css}
</style>
</head>
<body>
${cuerpo}
${datos}
<script type="module">
${js}
</script>
</body>
</html>`;

mkdirSync(join(RAIZ, 'dist'), { recursive: true });
writeFileSync(join(RAIZ, 'dist', 'telon.html'), salida);
console.log('✓ dist/telon.html', (salida.length / 1024).toFixed(0) + ' KB');

/* Variante para publicar como Artifact: sin doctype/html/head/body (los pone el host) */
const artifact = `<title>Telón · Cartelera de teatro en Santiago</title>
<style>
${css}
</style>
${cuerpo}
${datos}
<script type="module">
${js}
</script>`;
writeFileSync(join(RAIZ, 'dist', 'telon-artifact.html'), artifact);
console.log('✓ dist/telon-artifact.html', (artifact.length / 1024).toFixed(0) + ' KB');
console.log(`  ${incrustada.obras.length} obras · ${conFoto} afiches incrustados (${(pesoFotos / 1024).toFixed(0)} KB de fotos)`);
