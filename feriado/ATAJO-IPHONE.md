# Atajo de iPhone — «Día libre»

Marca en el tablero los días que te tomaste: el tablero descuenta el saldo de
la bolsa correcta, los pinta en su calendario y recalcula todo.

## El archivo ya está listo

En esta carpeta está **`Día libre.shortcut`**, ya construido y firmado con la
herramienta de Apple (`shortcuts sign --mode anyone`), listo para mandar por
**AirDrop** al iPhone — al recibirlo se abre en la app Atajos y solo hay que
tocar «Agregar atajo».

Al ejecutarlo pregunta tres cosas:

1. **¿Qué te tomaste?** — permiso completo, medio permiso, feriado 2026,
   acumulado 2025 o bolsa 2022. El medio permiso pregunta además **¿mañana o
   tarde?** (el descuento es medio día igual; la diferencia queda en el
   título del evento del calendario).
2. **¿Desde qué día?**
3. **¿Hasta qué día?** — si fue uno solo, la misma fecha.

El período se pide **corrido, como uno lo piensa**: «del 17 al 22 de
septiembre» descuenta solo los hábiles (17, 21 y 22) y deja intactos el 18,
el 19 y el fin de semana de por medio. Sirve igual para un día suelto, para
juntar días con un fin de semana o para el bloque obligatorio de 10.

Después abre el tablero con los días ya marcados y descontados, ofrece
agendar el período como evento de día completo en el calendario del iPhone
(se abre la hoja de confirmación para elegir calendario) y termina
preguntando **¿Marcar otro tramo?**

### Mezclas

Cada pasada marca **un tramo homogéneo** (una bolsa, una fracción), y el
tablero los va acumulando sin pisarse. Las mezclas se arman encadenando
tramos con la pregunta final:

> «Me tomé lunes a jueves de feriado y el viernes administrativo» →
> tramo 1: *Feriado 2026*, lunes → jueves · **Sí, otro tramo** →
> tramo 2: *Permiso completo*, viernes → viernes · **No, listo**.

Con medios días es igual: *Medio permiso* → *Tarde (½ PM)* → el día. (Por el
art. 109, los medios días existen solo en los permisos administrativos; el
feriado legal se toma por días hábiles completos, y por eso el menú solo
ofrece mitades ahí.)

Se regenera con `python3 fuente/atajo.py` (la firma requiere macOS con la app
Atajos, es decir, este mismo Mac).

---

## Cómo está armado (por si quieres modificarlo en la app Atajos)

1. **Seleccionar en el menú** — `¿Qué te tomaste?`, cinco opciones. Dentro de
   cada rama, dos pares de **Texto → Establecer variable**:

   | Rama | variable `Parametros` | variable `Titulo` |
   |---|---|---|
   | Permiso completo | `bolsa=permiso&frac=1` | `Permiso administrativo` |
   | Medio permiso | `bolsa=permiso&frac=0.5` | (menú anidado, abajo) |
   | Feriado 2026 | `bolsa=vigente&frac=1` | `Feriado legal` |
   | Acumulado 2025 | `bolsa=acum25&frac=1` | `Feriado legal (acumulado)` |
   | Bolsa 2022 | `bolsa=b2022&frac=1` | `Feriado legal (bolsa 2022)` |

   La rama *Medio permiso* tiene un segundo menú `¿Mañana o tarde?` que fija
   `Titulo` en `Permiso administrativo (½ AM)` o `(½ PM)`.

2. **Pedir entrada** (Fecha) `¿Desde qué día?` → **Formatear fecha**,
   personalizado, `yyyy-MM-dd`.
3. **Pedir entrada** (Fecha) `¿Hasta qué día? (si es uno solo, la misma
   fecha)` → **Formatear fecha**, igual.
4. **Texto** con la dirección, insertando las variables:

   ```
   https://franciscohenriquezmtm-prog.github.io/telon/feriado/?desde=[Fecha 1]&hasta=[Fecha 2]&[Parametros]
   ```

5. **Abrir URL** con ese texto.
6. **Agregar nuevo evento** — título `Titulo`, inicio la fecha 1, fin la
   fecha 2, **Todo el día** activado.
7. **Seleccionar en el menú** — `¿Marcar otro tramo?`: la rama «Sí» tiene una
   acción **Ejecutar atajo** apuntando a este mismo atajo; la rama «No» va
   vacía.

Dale nombre, ícono, y agrégalo a la pantalla de inicio o a un widget. También
funciona por voz: «Oye Siri, Día libre».

---

## Referencia de parámetros de la página

| Parámetro | Valores | Si falta |
|---|---|---|
| `desde` + `hasta` | período corrido `AAAA-MM-DD`; se marcan solo los hábiles | — |
| `desde` + `habiles` | inicio + cuántos días hábiles contar | `habiles=1` |
| `dias` | una fecha suelta, o varias separadas por coma | — |
| `bolsa` | `vigente` · `acum25` · `b2022` · `permiso` | `permiso` |
| `frac` | `0.5` = media jornada | `1` = día completo |
| `estado` | `plan` planificado · `sol` solicitado · `res` resuelto | `res` |
| `folio` | número de resolución (queda en el registro) | vacío |

- Sábados, domingos y feriados de calendario **nunca se descuentan**.
- Un día ya marcado **se sobreescribe** con lo nuevo, no se duplica.
- Para planificar en vez de registrar algo ya tomado, agrega `&estado=plan`.

---

## Dos cosas que conviene saber

1. **Dónde queda guardado.** Lo que el atajo marca se guarda en el navegador
   que abre la dirección. Si además tienes el tablero **agregado a pantalla de
   inicio**, iOS guarda los datos de esa «app» aparte de los de Safari: según
   la versión de iOS, el enlace del atajo puede abrir una u otra. Haz la prueba
   una vez — si el día marcado por el atajo no aparece en la app instalada,
   usa siempre la misma puerta de entrada (el atajo) y listo.

2. **Las publicaciones nuevas mandan.** Por diseño, cuando se publica una
   versión nueva del tablero, cada aparato vuelve al registro de `SEMILLA`
   (así las correcciones llegan solas). Los días definitivos que marques con
   el atajo conviene consolidarlos tarde o temprano en la `SEMILLA` de
   `fuente/tablero.html`, que es la fuente de verdad.
