# Feriado y permisos — control de días

Tablero para llevar el feriado legal y los permisos administrativos de un
funcionario público chileno (Ley 18.834). Sin cuentas, sin servidores, sin
publicidad: es una sola página HTML que guarda lo que marcas en el navegador
del aparato donde lo marcaste.

## 👉 En vivo: **https://franciscohenriquezmtm-prog.github.io/telon/feriado/**

Se abre en cualquier teléfono o computador. En Safari, **Compartir → Agregar a
pantalla de inicio** la deja como app, a pantalla completa y funcionando sin
señal. Al pie de la página dice la fecha de la última publicación.

---

## Qué calcula

Las bolsas de feriado **no son intercambiables**, y ese es el punto del tablero:

| Bolsa | Qué pasa a fin de año |
|---|---|
| **Feriado del año** (15 días hábiles) | se acumula entero al año siguiente |
| **Acumulado del año anterior** | ya es el segundo período consecutivo: **no puede volver a acumularse** |
| **Bolsa 2022** | plazo legal hasta el **31-12-2027**, y exenta del bloque de diez días |
| **Permisos administrativos** (6 días) | **se extinguen el 31 de diciembre**, sin postergación posible |

De ahí salen las tres cifras de cabecera: cuántos días libres quedan, cuántos
hay que gastar dentro del año, y cuántos llegan al año siguiente.

### Las reglas, con su fuente

- **Techo de acumulación: 30, 40 o 50 días hábiles** según antigüedad —
  [art. 104 inc. 3](https://leyes-cl.com/ley_sobre_estatuto_administrativo/104.htm)
  en relación con el [art. 103](https://leyes-cl.com/ley_sobre_estatuto_administrativo/103.htm).
  Con menos de 15 años de servicio son 15 días al año y techo de 30.
- **Bloque de diez días seguidos.** «Los funcionarios podrán solicitar hacer uso
  del feriado en forma fraccionada, pero una de las fracciones no podrá ser
  inferior a diez días» (art. 104 inc. final). Una racha más corta no cuenta
  como avance: o hay una de diez, o se debe entera. Los sábados no son hábiles
  (art. 103), así que un bloque de diez son dos semanas de lunes a viernes.
- **La jefatura no puede negar el feriado** discrecionalmente; solo anticipar o
  postergar su época **dentro del mismo año** (art. 104 incs. 1 y 2). Para que
  los días crucen al año siguiente tiene que existir petición expresa del
  funcionario.
- **Bolsa 2022:** feriados acumulados bajo el art. 51 de la ley 21.526; el
  [art. 60 de la ley 21.647](https://leyes-cl.com/otorga_reajuste_general_de_remuneraciones_a_las_y_los_trabajadores_del_sector_publico/60.htm)
  les fija plazo hasta el 31-12-2027 y permite fraccionar el bloque de diez
  días solo respecto de ellos.
- **Permisos administrativos:** seis días hábiles del **año calendario**,
  fraccionables por días o medios días
  ([art. 109](https://leyes-cl.com/ley_sobre_estatuto_administrativo/109.htm)).

### El trámite de postergación

Para traspasar feriado al año siguiente se pide **como si se fuera a tomar** y
la jefatura lo rechaza por razones de buen servicio, acordado antes. Por eso el
período pedido tiene que **caber completo antes del 31 de diciembre**: el
tablero cuenta hacia atrás tantos días hábiles como días se quieran postergar y
te da la fecha tope, con una cuenta regresiva.

---

## Cómo trabajar en el código

```
feriado/
├── index.html            ← generado, no editar a mano
├── manifest.webmanifest  ← generado
├── icon.svg              ← generado
├── sw.js                 ← generado
└── fuente/
    ├── tablero.html      ← AQUÍ se edita todo
    └── construir.py      ← genera los cuatro archivos de arriba
```

Editar y reconstruir:

```bash
python3 fuente/construir.py
```

No hace falta instalar nada: solo Python 3. Después, `git add feriado`,
commit y push — el workflow `.github/workflows/actualizar.yml` copia esta
carpeta al sitio y GitHub Pages la publica en un par de minutos.

Cada construcción estampa la fecha en la página. Si un teléfono tiene guardada
una versión más vieja, gana la página: así una corrección llega sola a todos
los aparatos, sin volver a cargar nada.

### Dónde están los datos

En `fuente/tablero.html`, arriba del todo del `<script>`:

- `SEMILLA` — los días ya tomados, con su bolsa, estado y número de resolución.
- `DEFAULTS.ini` — los saldos iniciales del año de cada bolsa.
- `DEFAULTS.techo`, `.nuevo`, `.bloque`, `.margen` — los supuestos legales.
- `FERIADOS` — los feriados de calendario de 2026 y 2027. **Los movibles de
  2027 conviene confirmarlos** cuando salga el calendario oficial.

Todos son editables también desde la página, en «Saldos iniciales y supuestos».

---

## Atajo de iPhone

Un `.shortcut` es un archivo firmado por Apple: se crea en la app Atajos, no a
mano. Para esto probablemente no haga falta ninguno — **Agregar a pantalla de
inicio** ya deja la página como app, a pantalla completa y sin conexión.

Un atajo sí tiene sentido si quieres algo que la página no hace sola, por
ejemplo:

- **Abrir el tablero** con un widget o desde el Back Tap → acción *Abrir URL*.
- **Recordatorio del trámite** → un atajo de automatización que el 25 de
  noviembre te abra el tablero y te muestre la cuenta regresiva.
- **Marcar un día desde el teléfono** sin abrir la página: eso necesitaría que
  la página leyera parámetros de la dirección (algo como
  `?marcar=2026-11-09&bolsa=vigente`). Hoy no lo hace; es un cambio chico en
  `fuente/tablero.html` si lo quieres.
