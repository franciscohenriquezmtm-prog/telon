/* Genera los íconos PNG de la app sin dependencias externas.
   Uso:  node tools/make-icons.mjs
   Dibuja por campos de distancia con supersampling 3x y comprime con zlib. */

import { writeFileSync, mkdirSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), '..');
const SS = 3; // supersampling

const mezcla = (a, b, t) => a + (b - a) * Math.min(1, Math.max(0, t));
const sobre = (base, capa, alfa) => base.map((c, i) => mezcla(c, capa[i], alfa));

/** Máscara de teatro: cara + ojos + boca, en coordenadas normalizadas 0..1 */
function mascara(x, y, cx, cy, r, giro) {
  const dx0 = x - cx, dy0 = y - cy;
  const c = Math.cos(giro), s = Math.sin(giro);
  const dx = dx0 * c - dy0 * s, dy = dx0 * s + dy0 * c;

  // cara: elipse un poco más alta que ancha, mentón afinado
  const ex = dx / (r * 0.86);
  const ey = dy / (r * (dy > 0 ? 1.12 : 0.98));
  const cara = 1 - Math.hypot(ex, ey);

  // ojos
  const ojo = Math.min(
    Math.hypot((dx + r * 0.34) / (r * 0.19), (dy + r * 0.2) / (r * 0.13)),
    Math.hypot((dx - r * 0.34) / (r * 0.19), (dy + r * 0.2) / (r * 0.13))
  );

  // boca: arco sonriente (anillo recortado)
  const bocaR = Math.hypot(dx / (r * 0.62), (dy - r * 0.02) / (r * 0.62));
  const boca = Math.abs(bocaR - 0.72) < 0.14 && dy > r * 0.18;

  return { cara, ojo, boca };
}

function render(size) {
  const N = size * SS;
  const px = new Float64Array(N * N * 3);

  for (let j = 0; j < N; j++) {
    for (let i = 0; i < N; i++) {
      const x = (i + 0.5) / N, y = (j + 0.5) / N;

      // fondo: telón rojo con luz cenital
      const luz = Math.max(0, 1 - Math.hypot((x - 0.5) * 1.1, y + 0.15) * 1.25);
      let col = sobre([0.34, 0.03, 0.08], [0.84, 0.15, 0.24], luz * 0.95);
      col = sobre(col, [0.10, 0.02, 0.04], Math.max(0, (y - 0.45) * 0.9));

      // pliegues verticales del telón
      const pliegue = 0.5 + 0.5 * Math.cos(x * Math.PI * 14);
      col = sobre(col, col.map((c) => c * 0.72), pliegue * 0.35 * (1 - luz * 0.5));

      // máscara dorada
      const m = mascara(x, y, 0.5, 0.53, 0.29, -0.06);
      const borde = Math.min(1, Math.max(0, m.cara * N * 0.35));
      if (borde > 0) {
        const brillo = 1 - (y - 0.3) * 0.55;
        const oro = [0.97 * brillo, 0.78 * brillo, 0.36 * brillo];
        col = sobre(col, oro, borde);
        // sombra interior bajo el mentón
        col = sobre(col, [0.75, 0.52, 0.16], borde * Math.max(0, (y - 0.62) * 2.4));
        // rasgos
        const rasgo = Math.min(1, Math.max(0, (1 - m.ojo) * N * 0.35));
        col = sobre(col, [0.20, 0.04, 0.09], rasgo * borde);
        if (m.boca) col = sobre(col, [0.20, 0.04, 0.09], borde);
      }

      const o = (j * N + i) * 3;
      px[o] = col[0]; px[o + 1] = col[1]; px[o + 2] = col[2];
    }
  }

  // downsample + esquinas ligeramente redondeadas (iOS recorta igual, pero se ve mejor)
  const out = Buffer.alloc(size * size * 4);
  for (let j = 0; j < size; j++) {
    for (let i = 0; i < size; i++) {
      let r = 0, g = 0, b = 0;
      for (let sj = 0; sj < SS; sj++) {
        for (let si = 0; si < SS; si++) {
          const o = (((j * SS + sj) * size * SS) + (i * SS + si)) * 3;
          r += px[o]; g += px[o + 1]; b += px[o + 2];
        }
      }
      const n = SS * SS, o = (j * size + i) * 4;
      out[o] = Math.round(Math.min(1, r / n) * 255);
      out[o + 1] = Math.round(Math.min(1, g / n) * 255);
      out[o + 2] = Math.round(Math.min(1, b / n) * 255);
      out[o + 3] = 255;
    }
  }
  return out;
}

/* ── Escritura PNG mínima ──────────────────────────────────────────────── */
const crcTabla = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

function crc32(buf) {
  let c = -1;
  for (const b of buf) c = crcTabla[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function chunk(tipo, datos) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(datos.length);
  const cuerpo = Buffer.concat([Buffer.from(tipo, 'ascii'), datos]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(cuerpo));
  return Buffer.concat([len, cuerpo, crc]);
}

function png(rgba, size) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 6;   // RGBA
  const filas = Buffer.alloc((size * 4 + 1) * size);
  for (let j = 0; j < size; j++) {
    filas[j * (size * 4 + 1)] = 0; // filtro none
    rgba.copy(filas, j * (size * 4 + 1) + 1, j * size * 4, (j + 1) * size * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(filas, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

mkdirSync(join(RAIZ, 'assets'), { recursive: true });
for (const size of [180, 192, 512]) {
  const archivo = join(RAIZ, 'assets', `icon-${size}.png`);
  writeFileSync(archivo, png(render(size), size));
  console.log('✓', archivo);
}
