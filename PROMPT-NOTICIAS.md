# Prompt — Repaso de noticias diario (7:30, hora de Canarias)

Este es el texto que se ejecuta cada mañana de forma automática. También puedes
pegarlo tal cual en una conversación nueva si quieres el repaso en otro momento.

## Cómo está programado

El planificador solo entiende UTC y no sabe nada del cambio de hora, así que
una sola rutina se desviaría una hora en verano o en invierno. Para que salga
siempre a las **7:30 reales de Canarias**, hay dos rutinas gemelas:

| Rutina  | Cron (UTC)    | Genera el repaso en |
| ------- | ------------- | ------------------- |
| turno A | `30 6 * * *`  | horario de verano (WEST, UTC+1) |
| turno B | `30 7 * * *`  | horario de invierno (WET, UTC+0) |

Las dos arrancan cada día con la misma comprobación: miran la hora local con
`TZ=Atlantic/Canary date` y, si no son las 7 y pico en Canarias, se paran sin
hacer nada. Así solo una de las dos escribe el repaso, y lo hace a la hora
buena los 365 días sin tocar nada en marzo ni en octubre.

**Si cambias el prompt, cámbialo en las dos rutinas**: este fichero es la
versión de referencia. Las dos llevan exactamente el mismo texto.

### Dónde escribe el repaso: una conversación nueva cada día

Cada disparo abre **su propia conversación**, con el nombre `Repaso — 9 de
septiembre de 2026`. Así, al entrar en la lista y darle a play, sale el repaso
de ese día sin tener que bajar por un hilo interminable.

Antes estaban las dos rutinas enganchadas a una sesión persistente, y antes de
eso en sesión nueva sin más. Las dos versiones anteriores tenían un problema:

- **Sesión nueva sin nombre** (hasta el 27 de agosto): la rutina se ejecutaba
  bien, buscaba tres minutos y medio y escribía el repaso entero, pero lo
  dejaba en una conversación sin identificar que nadie miraba. Como las
  notificaciones tampoco llegan, el repaso existía y no se leía.
- **Sesión persistente** (del 27 de agosto al 9 de septiembre): resolvía lo
  anterior y además dejaba ver el repaso del día anterior, pero lo apilaba
  todo en un solo hilo cada vez más largo.

La versión actual se queda con lo bueno de las dos: conversación separada y
con fecha en el título, **y** memoria de ayer, porque el histórico ya no vive
en el hilo sino en el repositorio.

### El histórico: la carpeta `repasos/`

Cada repaso se guarda en `repasos/AAAA-MM-DD.md` y se sube a la rama
`claude/thirtieth-maximum-rn2em7`. Ese fichero es lo único que la conversación
de mañana podrá ver del día de hoy, así que tiene que quedar completo.

El orden de cada mañana es: comprobar la hora, ponerle nombre a la
conversación, leer el fichero más reciente de `repasos/`, buscar, escribir el
repaso en la respuesta y guardarlo en su fichero. De ahí siguen funcionando la
regla NO REPITAS LO DE AYER y los seguimientos de una línea.

Si algún día `repasos/` estuviera vacío o el fetch fallara, la rutina da el
repaso igual, solo que sin comparar con el día anterior.

### Efectos secundarios que hay que conocer

- **El turno que no toca también abre una conversación.** Intenta renombrarse
  a `(sin uso) turno equivocado — fecha` y archivarse sola para no ensuciar la
  lista. Si esas herramientas no están disponibles en la sesión, se queda una
  conversación corta con la frase de turno equivocado; es inofensiva.
- **Renombrar es opcional.** Las sesiones que dispara una rutina pueden
  arrancar sin las herramientas de `claude-code-remote`. El prompt lo
  contempla: si no puede renombrarse, sigue con el repaso igual y deja el
  encabezado con la fecha como primera línea.

Las rutinas corren con el modelo por defecto de la cuenta, que es lo que
entra en el plan Pro. Se probó Opus 5 y se revirtió por coste.

### Y aparte: el vigía de urgentes (DESACTIVADO)

**Está desactivado, no borrado.** La idea era que avisara al móvil o por correo
cuando pasara algo grave fuera de la hora del repaso, pero ninguno de los dos
canales entrega nada en esta cuenta: se probaron push y email, las ejecuciones
terminan bien y marcadas para notificar, y no llega ni notificación ni correo.
Los conectores (Gmail) no se pueden adjuntar a una rutina en esta organización,
así que tampoco hay forma de que se mande el correo por su cuenta. Sin canal de
entrega solo gastaba dinero, así que se apagó. Si algún día las notificaciones
de las rutinas funcionan, se vuelve a activar tal cual está.

Cuando estaba activo se ejecutaba cada dos horas y cubría una ventana de dos
horas y media, solapada a propósito para que no se escapara nada entre pasadas.
Llevaba su propio filtro (víctimas, guerra, caída de gobierno, emergencia,
desplome económico) y la instrucción de callarse siempre que dudara, y fuera de
8:00–23:00 hora canaria se paraba sin buscar.

---

## Los tres pasos previos (van al principio de las dos rutinas)

> **PASO 0 — COMPROBACIÓN DE HORA** (obligatorio, antes de nada).
>
> Ejecuta en bash: `TZ=Atlantic/Canary date '+%H:%M %Z'`
>
> Si la hora local de Canarias NO empieza por "07", hoy no te toca. Responde
> únicamente "Turno equivocado: son las HH:MM en Canarias. El repaso de hoy lo
> genera la otra rutina." y termina ahí. No busques nada, no escribas el repaso,
> no toques el repositorio.
> Antes de terminar, y solo si tienes disponibles las herramientas de
> `claude-code-remote`, renómbrate a "(sin uso) turno equivocado — AAAA-MM-DD" y
> archívate para no ensuciar la lista. Si no están o fallan, sáltatelo.
>
> Si la hora local sí empieza por "07", continúa.
>
> **PASO 1 — PON NOMBRE A LA CONVERSACIÓN** (antes de buscar nada).
>
> Si tienes `set_session_title`, llama antes a `get_session` sin `session_id` y
> renómbrate a "Repaso — 9 de septiembre de 2026", con la fecha de hoy. Hazlo ya,
> antes de buscar, para que el nombre aparezca aunque el repaso tarde. Si esa
> herramienta no está o falla, sáltate el paso y sigue: en ese caso, que la
> primera línea de la respuesta sea el encabezado con la fecha.
>
> **PASO 2 — LEE EL REPASO DE AYER.**
>
> ```
> cd "$(git rev-parse --show-toplevel 2>/dev/null || echo /home/user/Sangam)"
> git fetch origin claude/thirtieth-maximum-rn2em7
> git checkout -B claude/thirtieth-maximum-rn2em7 origin/claude/thirtieth-maximum-rn2em7
> ls repasos/ | tail -3
> ```
>
> El fichero más reciente de `repasos/` es el repaso de ayer. Si la carpeta no
> existe o está vacía, sigue igual y da el repaso sin comparar.

Y al final, después de escribir el repaso en la respuesta:

> **PASO 4 — GUARDA EL REPASO PARA MAÑANA.**
>
> Guarda el mismo texto en `repasos/AAAA-MM-DD.md`, con `git add repasos/`,
> `git commit` y `git push -u origin claude/thirtieth-maximum-rn2em7`. Si el push
> falla por red, reintenta cuatro veces esperando 2, 4, 8 y 16 segundos. No
> modifiques ningún otro fichero y no abras ninguna pull request.

---

## Fuentes: cuáles se pueden leer y cuáles no

Muchos medios bloquean al rastreador. Filtrar una búsqueda por ellos devuelve un
error, y buscar sin filtro los deja fuera igual. Comprobado el 26 de agosto de
2026, medio a medio.

**Accesibles:**

- *India*: indiatvnews.com, theprint.in, scroll.in, thewire.in,
  business-standard.com, deccanherald.com, aninews.in, downtoearth.org.in,
  newslaundry.com, rediff.com, y pib.gov.in para notas oficiales del Gobierno.
- *España*: eldiario.es, publico.es, elespanol.com, eleconomista.es, e
  infobae.com, que reproduce los teletipos de EFE.
- *Estados Unidos*: npr.org, cbsnews.com, nbcnews.com, abcnews.go.com,
  axios.com, thehill.com, washingtonpost.com, cnn.com, pbs.org y bloomberg.com.
  Para sociedad en profundidad, kffhealthnews.org (sanidad), chalkbeat.org
  (educación), stateline.org (los estados), propublica.org y statnews.com.
- *Internacional*: cnnespanol.cnn.com, euronews.com, elfinanciero.com.mx.
- *Deporte*: espncricinfo.com y espn.com para críquet, con marcador y crónica en
  vivo; skysports.com y besoccer.com para fútbol. El críquet indio también sale
  bien en indiatvnews.com, aninews.in y deccanherald.com, y el fútbol español en
  elespanol.com, eldiario.es y publico.es.

**Bloqueados** (no gastar búsquedas en ellos): The Hindu, Indian Express, NDTV,
Hindustan Times, Times of India, Livemint, India Today, Firstpost, News18,
Telegraph India, Economic Times, Moneycontrol, PTI, The Hindu BusinessLine,
Reuters, BBC, AP, The Guardian, New York Times, Los Angeles Times, USA Today,
Politico, El País, El Mundo, ABC, La Vanguardia, RTVE, Europa Press,
Cadena SER, El Confidencial, 20minutos, La Razón, El Periódico, Antena 3,
laSexta, Expansión, HuffPost España, y en deporte Marca, AS, Mundo Deportivo,
Sport, Relevo y Cricbuzz.

### Cómo buscar la sociedad india

Es el punto que más costó. Buscar "India sociedad" o "India desigualdad" en
genérico devuelve informes y columnas sin fecha, no actualidad. Hay que atacar
por temas concretos, nombrando el tema y la fecha de hoy, con el filtro de
dominios puesto. Comprobado el 26 de agosto de 2026:

| Tema | Consulta | Resultado |
| ---- | -------- | --------- |
| Sanidad | `India public health hospital patients <fecha>` | Muy bien: brotes, hospitales, avisos del IMA, decisiones estatales, con fecha |
| Educación | `India schools students education news <fecha>` | Muy bien: exámenes, protestas estudiantiles, cierres, sucesos en campus |
| Empleo | `India workers labour wages jobs <fecha>` | Flojo: páginas de tema y datos de encuestas viejas |
| Medio ambiente | `India pollution water climate <fecha>` | Flojo: informes y reportajes, poca noticia del día |

Cuando el día lo pida, el mismo patrón sirve para vivienda urbana, casta y
discriminación, seguridad de las mujeres, o precios de alimentos y combustible.
Al menos dos búsquedas temáticas antes de dar el bloque por cerrado, y lo que
salga en formato informe va marcado como informe, nunca disfrazado de noticia.

---

Para India, Estados Unidos y deporte hay que buscar **con el filtro de dominios
puesto**: sin él las
búsquedas genéricas devuelven Wikipedia y refritos viejos en vez de la
actualidad del día. Esta lista envejece — si un bloque empieza a salir flojo,
toca volver a comprobar qué medios siguen abiertos.

---

## El repaso

Prepárame el repaso de noticias de esta mañana. Busca en la web: no contestes de
memoria, todo tiene que salir de fuentes consultadas hoy.

**Alcance**: lo publicado en las últimas 24 horas. Si una historia es de días
atrás pero hoy tiene una novedad relevante, entra, y explicas cuál es la novedad.
Comprueba la fecha de cada pieza: lo de semanas atrás o no entra, o entra
marcado explícitamente como contexto.

**No repitas lo de ayer**: el repaso anterior lo has leído en el PASO 2. No des
la misma noticia salvo que hoy tenga una novedad de verdad, y entonces di cuál
es. Si una historia sigue viva pero sin avances, va en una sola línea de
seguimiento al final del bloque, no como titular nuevo.

**Qué es "sociedad" aquí**: sanidad, educación, vivienda, migración, trabajo y
precios de la vida diaria, desigualdad, derechos, sucesos con alcance general y
cultura cuando importe. No es prensa rosa ni sucesos locales.

**Cinco bloques, en este orden**:

1. **India** — política, economía, tecnología y **sociedad** con el mismo peso:
   sanidad, educación, vivienda, migración interna, casta y desigualdad,
   condiciones de trabajo, medio ambiente cuando afecta a la gente.
2. **España** — nacional, economía y **sociedad**. Lo autonómico, solo si
   trasciende su comunidad.
3. **Estados Unidos** — política, economía y **sociedad**. Bloque propio, no va
   dentro de Mundo.
4. **Mundo** — lo importante de fuera de esos tres, incluida la UE.
5. **Deporte** — solo críquet y fútbol. Nada de otros deportes.

En los tres bloques de país, sociedad no es el relleno del final: si la mejor
noticia del día en India o en Estados Unidos es de sanidad o de vivienda, va la
primera.

**Límite duro: 30 noticias en total** sumando los cinco bloques. Nunca más de
30. Si el día da menos, das menos: prefiero 18 buenas que 30 rellenas. Reparto
orientativo: 7 India, 7 España, 6 Estados Unidos, 5 Mundo, 5 Deporte, ajustando
según el peso real del día.

**Qué va en Deporte**:

- *Críquet*: la selección india por encima de todo (Tests, ODI, T20, series en
  curso), la IPL cuando esté en temporada, y los torneos internacionales
  grandes. Marcador concreto y punto del partido, no una frase vaga: si un Test
  está a mitad, el día de juego y quién manda.
- *Fútbol*: LaLiga y la selección española, competiciones europeas, y fútbol
  indio si hay algo que lo merezca.
- Resultados y lo que cambia la clasificación o la eliminatoria. Lesiones y
  sanciones importantes, sí. Rumores de fichajes y ruedas de prensa, no.

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
- Nada de relleno en los cuatro primeros bloques: famosos y sucesos menores solo
  si de verdad son la noticia del día. El deporte tiene su propio bloque y ahí
  no aplica esta regla.
- Si un bloque sale flojo porque no hay material, decirlo en una línea al final
  de ese bloque en vez de rellenarlo con lo primero que haya.

**Cierre**: si hay una noticia que domina el día, termina con una línea —
**Lo único que hay que saber hoy:** ...— y si no la hay, dilo también. El
deporte no cuenta para esa línea salvo que sea algo histórico.

Todo en español, tono directo, sin introducciones ni despedidas.
