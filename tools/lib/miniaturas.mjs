/* Genera las miniaturas de los afiches usando lo que ya trae el sistema operativo:
   Windows → System.Drawing (PowerShell) · macOS → sips · Linux → ImageMagick.
   Si no hay ninguna herramienta, la app sigue funcionando con los afiches completos;
   solo la versión de un archivo pesará más. */

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { existsSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { join, extname, basename } from 'node:path';

const ejecutar = promisify(execFile);

export async function generarMiniaturas(raiz, { ancho = 480, calidad = 68, log = console.log } = {}) {
  const origen = join(raiz, 'assets', 'img');
  const destino = join(origen, 'mini');
  if (!existsSync(origen)) return { total: 0, herramienta: null };

  const fuentes = readdirSync(origen).filter((f) => /\.(jpe?g|png|webp)$/i.test(f));
  if (!fuentes.length) return { total: 0, herramienta: null };
  mkdirSync(destino, { recursive: true });

  const pendientes = fuentes.filter((f) => {
    const mini = join(destino, basename(f, extname(f)) + '.jpg');
    return !existsSync(mini) || statSync(mini).mtimeMs < statSync(join(origen, f)).mtimeMs;
  });

  try {
    if (process.platform === 'win32') {
      await ejecutar('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', join(raiz, 'tools', 'miniaturas.ps1'),
        '-Ancho', String(ancho), '-Calidad', String(calidad)], { windowsHide: true });
      return { total: contar(destino), herramienta: 'System.Drawing', nuevas: pendientes.length };
    }

    if (process.platform === 'darwin') {
      for (const f of pendientes) {
        const salida = join(destino, basename(f, extname(f)) + '.jpg');
        await ejecutar('sips', ['-s', 'format', 'jpeg', '-s', 'formatOptions', String(calidad),
          '-Z', String(ancho), join(origen, f), '--out', salida]);
      }
      return { total: contar(destino), herramienta: 'sips', nuevas: pendientes.length };
    }

    for (const f of pendientes) {
      const salida = join(destino, basename(f, extname(f)) + '.jpg');
      await ejecutar('convert', [join(origen, f), '-resize', `${ancho}x`, '-quality', String(calidad),
        '-background', '#170a10', '-flatten', salida]);
    }
    return { total: contar(destino), herramienta: 'ImageMagick', nuevas: pendientes.length };
  } catch (e) {
    log(`  ! no se pudieron generar miniaturas (${e.message.split('\n')[0]})`);
    log('    La app funciona igual; solo dist/telon.html quedará más pesado.');
    return { total: contar(destino), herramienta: null, error: e.message };
  }
}

const contar = (dir) => (existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.jpg')).length : 0);

/** Peso total de las miniaturas, en KB. */
export function pesoMiniaturas(raiz) {
  const dir = join(raiz, 'assets', 'img', 'mini');
  if (!existsSync(dir)) return 0;
  return readdirSync(dir).reduce((s, f) => s + statSync(join(dir, f)).size, 0) / 1024;
}
