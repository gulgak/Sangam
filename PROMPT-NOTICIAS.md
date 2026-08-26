# Prompt — Repaso de noticias diario (8:30, hora de Canarias)

Este es el texto que se ejecuta cada mañana de forma automática. También puedes
pegarlo tal cual en una conversación nueva si quieres el repaso en otro momento.

## Cómo está programado

El planificador solo entiende UTC y no sabe nada del cambio de hora, así que
una sola rutina se desviaría una hora en verano o en invierno. Para que salga
siempre a las **8:30 reales de Canarias**, hay dos rutinas gemelas:

| Rutina  | Cron (UTC)    | Genera el repaso en |
| ------- | ------------- | ------------------- |
| turno A | `30 7 * * *`  | horario de verano (WEST, UTC+1) |
| turno B | `30 8 * * *`  | horario de invierno (WET, UTC+0) |

Las dos arrancan cada día con la misma comprobación: miran la hora local con
`TZ=Atlantic/Canary date` y, si no son las 8 y pico en Canarias, se paran sin
hacer nada. Así solo una de las dos escribe el repaso, y lo hace a la hora
buena los 365 días sin tocar nada en marzo ni en octubre.

**Si cambias el prompt, cámbialo en las dos rutinas**: este fichero es la
versión de referencia.

Las tres rutinas corren con **Sonnet 5** (`claude-sonnet-5`), que es lo que
entra en el plan Pro. Se probó Opus 5 y se revirtió por coste.

### Y aparte: el vigía de urgentes

Una tercera rutina, independiente de esta, se ejecuta **cada dos horas** (cron
`2 */2 * * *`) y solo sirve para una cosa: avisar al móvil si pasa algo lo
bastante grave como para no esperar al repaso de la mañana. Cubre una ventana de
dos horas y media, solapada a propósito para que no se escape nada entre pasadas.
Lleva su propio filtro (víctimas, guerra, caída de gobierno, emergencia, desplome
económico) y su instrucción es callarse siempre que dude. Fuera de 8:00–23:00
hora canaria comprueba la hora y se para sin buscar. Esa sí tiene la notificación
push activada; el repaso de las 8:30 no.

---

## Comprobación de hora (va al principio de las dos rutinas)

> PASO 0 — COMPROBACIÓN DE HORA (obligatorio, antes de nada).
>
> Ejecuta en bash: `TZ=Atlantic/Canary date '+%H:%M %Z'`
>
> Si la hora local de Canarias NO empieza por "08", NO hagas nada más: responde
> únicamente "Turno equivocado: son las HH:MM en Canarias. El repaso de hoy lo
> genera la otra rutina." y termina ahí. No busques nada, no escribas el repaso,
> no toques el repositorio.
>
> Si la hora local sí empieza por "08", continúa con el repaso.

---

## El repaso

Prepárame el repaso de noticias de esta mañana. Busca en la web: no contestes de
memoria, todo tiene que salir de fuentes consultadas hoy.

**Alcance**: lo publicado en las últimas 24 horas. Si una historia es de días
atrás pero hoy tiene una novedad relevante, entra, y explicas cuál es la novedad.

**Tres bloques, en este orden**:

1. **India** — política, economía, sociedad, tecnología, lo que de verdad mueva el país.
2. **España** — nacional, economía, y lo autonómico solo si tiene alcance general.
3. **Mundo** — lo importante de fuera de esos dos, incluida la UE.

**Límite duro: 30 noticias en total.** Nunca más de 30. Si el día da menos, das
menos: prefiero 18 buenas que 30 rellenas. Reparto orientativo de hasta 10 por
bloque, pero ajusta según el peso real del día (si India está tranquila y el
mundo arde, se nota en el reparto).

**Orden**: dentro de cada bloque, de más importante a menos. Importante =
consecuencias reales para mucha gente, no cuánto se comparte.

**Formato de cada noticia**:

> **Titular corto y concreto** — una o dos frases con lo esencial: qué ha pasado,
> quién y por qué importa. [Medio](enlace)

**Reglas**:

- Nada sin fuente enlazada. Si no encuentras la fuente, la noticia no entra.
- Una historia va en un solo bloque, en el que más peso tenga. Sin repeticiones.
- Distingue el hecho de la interpretación. Si es análisis, previsión o encuesta,
  dilo ("previsión", "análisis", "según la encuesta X").
- Si algo está en desarrollo y los datos pueden cambiar en horas, márcalo como
  **en desarrollo**.
- Varía los medios. No montes el repaso entero sobre una sola cabecera, y para
  temas polémicos contrasta al menos dos.
- Si detectas que una noticia que circula mucho es dudosa o desmentida, dilo en
  vez de omitirla sin más.
- Nada de relleno: sucesos, famosos o deporte solo si de verdad es la noticia del día.

**Cierre**: si hay una noticia que domina el día, termina con una línea —
**Lo único que hay que saber hoy:** ...— y si no la hay, dilo también.

Todo en español, tono directo, sin introducciones ni despedidas.
