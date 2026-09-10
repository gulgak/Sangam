# Prompt — Repaso de noticias diario (7:30, hora de Canarias)

Este es el texto que se ejecuta cada mañana de forma automática. También puedes
pegarlo tal cual en una conversación nueva si quieres el repaso en otro momento.

## Cómo está programado

Dos rutinas gemelas, `30 6 * * *` y `30 7 * * *` (UTC), que escriben las dos en
**la misma conversación**. El planificador solo entiende UTC y no sabe nada del
cambio de hora, así que una sola rutina se desviaría una hora media año. Con
dos, y una comprobación de la hora local al principio, el repaso sale a las
**7:30 reales de Canarias** los 365 días sin tocar nada en marzo ni en octubre.

| Rutina  | Cron (UTC)   | Escribe el repaso en |
| ------- | ------------ | -------------------- |
| turno A | `30 6 * * *` | horario de verano (WEST, UTC+1) |
| turno B | `30 7 * * *` | horario de invierno (WET, UTC+0) |

La que no toca contesta una sola línea ("Turno equivocado…") y se calla. Cuesta
una línea al día en el hilo y a cambio no hay que acordarse de nada.

**Si cambias el prompt, cámbialo en las dos rutinas**: este fichero es la
versión de referencia. Las dos llevan exactamente el mismo texto.

## Un solo hilo, con los días bien separados

Todos los repasos van seguidos en una misma conversación, en modo **sesión
persistente**. Para que no se conviertan en una pared de texto, cada día empieza
con un corte visible: una línea horizontal, un titular con la fecha en
mayúsculas y otra línea horizontal, y se cierra con una tercera al terminar.

Se probó lo contrario —una conversación nueva por día— entre el 9 y el 10 de
septiembre de 2026, y hubo que revertirlo. Lo que lo mató está en el apartado
siguiente. Lo que se gana con el hilo único es la continuidad: el repaso ve el
del día anterior, así que puede evitar repeticiones, dar seguimientos de una
línea y corregirse a sí mismo de un día para otro.

### Lo que no se puede hacer desde una sesión de rutina

Comprobado el 10 de septiembre de 2026, con dos ejecuciones reales y una prueba
técnica aparte. Las conversaciones que **abre una rutina** (las de
`create_new_session_on_fire`) tienen dos limitaciones que no se ven venir:

- **Nacen sin repositorio.** No hay clon, ni aunque la rutina declare
  `source_url`: se probó y la sesión respondió que no había repositorio. Sin
  repositorio no hay `repasos/` que leer ni adónde empujar. Esto es lo que
  rompió el diseño de conversación-por-día: la rutina del 10 de septiembre se
  ejecutó, se marcó como correcta, escribió un repaso completo de 51.000
  palabras… y no pudo guardar nada ni comparar con el día anterior.
- **No pueden renombrarse.** Arrancan sin las herramientas de
  `claude-code-remote`, así que `set_session_title` y `archive_session` no
  existen ahí. El título lo pone el sistema con el nombre de la rutina, igual
  todos los días, y no se puede cambiar desde dentro.

Juntas dejan la conversación-por-día sin sus dos ventajas: ni se distinguen los
días por el título ni hay memoria de ayer. De ahí la vuelta al hilo único.

Una sesión persistente no tiene ninguno de los dos problemas: es una
conversación normal, con su repositorio y sus herramientas.

### La copia de seguridad: la carpeta `repasos/`

Además de escribirlo en el hilo, cada repaso se guarda en
`repasos/AAAA-MM-DD.md` en la rama `claude/thirtieth-maximum-rn2em7`. No es la
vía principal —la memoria está en la propia conversación— sino el respaldo para
cuando el hilo se resuma o se pierda: entonces la rutina lee el fichero más
reciente y sigue funcionando la regla de no repetir.

Falta el fichero del 10 de septiembre de 2026: ese repaso se generó en una
conversación aparte, sin repositorio, y no se pudo guardar.

Las rutinas corren con el modelo por defecto de la cuenta, que es lo que entra
en el plan Pro. Se probó Opus 5 y se revirtió por coste.

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

## Los pasos que envuelven al repaso

> **PASO 0 — COMPROBACIÓN DE HORA** (obligatorio, antes de nada).
>
> Ejecuta en bash: `TZ=Atlantic/Canary date '+%H:%M %Z'`
>
> Si la hora local de Canarias NO empieza por "07", no hagas nada más: responde
> únicamente "Turno equivocado: son las HH:MM en Canarias. El repaso de hoy lo
> genera la otra rutina." y termina ahí.
>
> **PASO 1 — MIRA LO DE AYER.**
>
> El repaso de ayer está más arriba en esta misma conversación. Si no aparece
> porque la conversación se ha resumido, está en `repasos/` del repositorio:
>
> ```
> cd "$(git rev-parse --show-toplevel 2>/dev/null || echo /home/user/Sangam)"
> git pull --ff-only 2>&1 | tail -2
> ls repasos/ | tail -3
> ```

Y la separación con la que empieza cada día, sin ninguna frase delante:

```
---

# 📅 JUEVES 10 DE SEPTIEMBRE DE 2026

---
```

cerrando el repaso, después de "Lo único que hay que saber hoy", con otra
línea `---`.

Al terminar:

> **PASO 3 — GUARDA UNA COPIA.**
>
> Guarda el mismo texto en `repasos/AAAA-MM-DD.md`, con `git add repasos/`,
> `git commit` y `git push -u origin claude/thirtieth-maximum-rn2em7`. Si el
> push falla por red, reintenta cuatro veces esperando 2, 4, 8 y 16 segundos.
> Si falla por otra cosa, dilo en una línea al final en vez de esconderlo. No
> toques ningún otro fichero y no abras ninguna pull request.

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

**No repitas lo de ayer**: el repaso anterior está más arriba en esta misma conversación (PASO 1). No des
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
