# Fase 1 — Planteamiento del proyecto: por qué esas herramientas y no otras

Sin código todavía. El objetivo de esta fase es que, antes de abrir un
editor, sepas explicar **por qué** el proyecto terminó con exactamente
cuatro herramientas de un tipo muy concreto — no tres, no diez, no un
mega-tool genérico — y por qué esa decisión se toma *antes* de escribir
nada. Si te saltas esta fase y empiezas por el código, terminas
programando una arquitectura sin poder justificar ninguna de sus
decisiones — que es exactamente lo que le pasó al borrador descartado (ver
nota en `overview.md`).

---

## 1. El punto de partida: qué tienes ya, qué te piden

Cuando te plantean "haz un chat que responda preguntas sobre los datos de
la planta", lo primero no es pensar en el LLM — es inventariar **qué datos
ya existen y en qué forma**, porque eso acota completamente el problema.

En este repo, `UNS_SILVER` ya resuelve la parte difícil (ver
`UNS_SILVER/docs/superpowers/specs/*-design.md`): expone un catálogo de
señales versionado, lecturas tipadas con agregados precalculados, y un
log de eventos. Es decir, cuando te sientas a diseñar `UNS_COPILOT`, el
recurso de datos **no es una base de datos genérica** — son exactamente
cuatro superficies de consulta ya existentes:

| Recurso ya construido en `UNS_SILVER` | Qué contiene |
|---|---|
| `signal_catalog` | metadatos de cada señal: unidad, umbrales, tipo (`raw`/`kpi`), solo la versión vigente (`effective_until IS NULL`) |
| `silver_latest_value` | vista con el último valor de cada señal — pensada para lookup O(1), no para recorrer historia |
| `silver_readings` / `silver_readings_1m` / `silver_readings_1h` | series temporales, en crudo o pre-agregadas |
| `silver_events` | eventos discretos (alarmas, fallos) con marca de tiempo |

**Esta tabla es el primer artefacto de diseño**, y existe antes de pensar
una sola línea de prompt o de código Python. La pregunta que estás
respondiendo en esta fase no es "¿qué puede hacer un LLM?" sino "¿qué
formas de acceder a estos cuatro recursos necesita un usuario para hacer
las preguntas que va a hacer?".

---

## 2. El error de diseño que hay que descartar primero: NL → SQL directo

La solución que se te ocurre primero — "el LLM lee la pregunta, escribe el
SQL, lo ejecutamos" — hay que evaluarla y **rechazarla explícitamente**,
con razones concretas, no por intuición:

1. **Imprecisión.** Un modelo pequeño, local (este proyecto usa modelos
   de 8-14B vía Ollama, no un frontier model gigante) se equivoca de
   columna, de JOIN, de sintaxis específica de TimescaleDB. Un error de
   sintaxis rompe la respuesta; un error *silencioso* (JOIN mal hecho que
   igualmente devuelve filas, pero las equivocadas) es peor: el usuario
   recibe un número que parece correcto y no lo es.
2. **Superficie de inyección / alcance no acotado.** Nada garantiza que el
   SQL generado sea de solo lectura, ni que tenga un `LIMIT`, ni que no
   traiga toda la tabla `silver_readings` a 1Hz de los últimos dos años.
   Tendrías que *analizar* el SQL generado para decidir si es seguro
   ejecutarlo — y ese analizador sería, en la práctica, tan complejo como
   no generar SQL libre en absoluto.
3. **No hay dónde poner un guardrail.** Con SQL libre, "limita a 1000
   filas" o "máximo 24h en crudo" son reglas que tendrías que inyectar
   *dentro* del SQL que genera el modelo — fragilísimo. Con una función
   fija, el guardrail vive en tu código Python, fuera del alcance del
   modelo, y no hay forma de que el modelo lo evite.

Este descarte razonado es exactamente lo que dice la sección "Context" del
spec real (`UNS_COPILOT/docs/superpowers/specs/2026-09-06-uns-copilot-design.md`):
*"The core risk this design avoids is letting an LLM write or approve
free-form SQL against production data"*. No es una preferencia estética —
es la primera decisión de diseño, y todo lo demás se deriva de ella.

---

## 3. La alternativa: function calling — pero como decisión, no como magia

La alternativa es **tool-calling / function calling**: en vez de que el
modelo genere la consulta, tú escribes de antemano un menú cerrado de
funciones parametrizadas, y el modelo solo elige cuál llamar y con qué
argumentos. El SQL que se ejecuta es siempre el que **tú** escribiste —
el modelo nunca ve ni toca una cadena SQL.

Compara las dos superficies de ataque:

```
NL → SQL directo:                       Function calling:
┌─────────┐   SQL libre    ┌─────┐      ┌─────────┐  nombre+args   ┌─────┐
│   LLM   │ ─────────────► │ DB  │      │   LLM   │ ─────────────► │ Tú  │──► SQL fijo ──► DB
└─────────┘                └─────┘      └─────────┘                └─────┘  parametrizado
   el modelo controla                      el modelo elige de un menú;
   la forma de la query                    tú controlas la query entera
```

Esta es la primera pieza de las tres que ya viste en el tutorial de
lectura de código (`manual/es/03-tutorial-function-calling.md`, sección
1): **el contrato**. Antes de escribir el bucle o el ejecutor, tienes que
decidir **qué contrato** vas a ofrecer — y eso significa decidir cuántas
funciones, con qué forma.

---

## 4. Cómo se decide *qué* herramientas exponer (el método, no el resultado)

Aquí es donde se comete el segundo error típico: diseñar las tools
mirando el esquema SQL ("hay 4 tablas, hago 4 tools que hacen `SELECT *`")
o mirando lo que "parece razonable". Ninguna de las dos es la forma
correcta. El método real es:

**Paso 1 — Enumera preguntas reales que un usuario haría**, antes de
pensar en ninguna función:

- "¿Qué señales hay en la línea 3?" / "¿qué mide `Gen_RPM_Avg`?"
- "¿Cuál es el valor actual de la temperatura del horno?"
- "¿Cómo ha evolucionado la presión en la última hora?" / "muéstrame el
  histórico de ayer"
- "¿Ha habido alguna alarma esta madrugada?"

**Paso 2 — Para cada pregunta, identifica qué *forma* de acceso a los
datos la resuelve** (no la solución SQL exacta, la forma):

| Pregunta tipo | Forma de acceso que necesita |
|---|---|
| "¿qué señales/KPIs existen...?" | listar/filtrar un catálogo |
| "¿cuál es el valor *ahora*?" | lookup puntual del último valor |
| "¿cómo ha evolucionado entre t1 y t2?" | rango temporal, con o sin agregación |
| "¿ha habido algún evento/alarma?" | rango temporal sobre un log de eventos |

**Paso 3 — Agrupa las preguntas por la forma de acceso, no por la
pregunta literal.** "¿Cuál fue el pico de temperatura ayer?" y "¿qué
temperatura hay ahora?" *parecen* la misma tool candidata ("dame la
temperatura") pero son formas de acceso distintas (rango vs. punto) con
guardrails distintos (una necesita límite de rango temporal, la otra no
tiene ese problema porque siempre son 0 o 1 filas). Si las mezclas en una
sola tool con parámetros opcionales para todo, terminas con una función
ambigua que el modelo no sabe cuándo usar.

**Paso 4 — El resultado, en este proyecto, son cuatro formas de acceso
distintas — no tres.** (Si partías de la idea de que eran tres, merece la
pena parar aquí: cuéntalas — `get_catalog`, `get_latest_value`,
`query_readings`, `list_events` — cada una resuelve una *forma* de
pregunta distinta, y cada una mapea 1:1 a uno de los cuatro recursos de
la tabla de la sección 1):

| # | Tool | Forma de acceso | Recurso de `UNS_SILVER` |
|---|---|---|---|
| 1 | `get_catalog` | descubrir qué existe | `signal_catalog` |
| 2 | `get_latest_value` | lookup puntual | `silver_latest_value` |
| 3 | `query_readings` | rango temporal, con agregación opcional | `silver_readings(_1m/_1h)` |
| 4 | `list_events` | rango temporal sobre eventos | `silver_events` |

**Por qué no una quinta tool "genérica" que las cubra todas con un
parámetro `mode`:** porque el modelo decide qué tool llamar leyendo su
`description` (lo verás en la Fase 5) — cuanto más se parezcan dos tools
entre sí, peor elige el modelo entre ellas. Cuatro tools con propósito
claro y sin solape es más fácil de acertar para un modelo pequeño que una
tool "todopoderosa" con un parámetro que decide el comportamiento.

**Por qué no más de cuatro** (por ejemplo, una tool por cada tipo de
agregación): porque `agg` ya es un parámetro dentro de `query_readings`
(`raw`/`1m`/`1h`), no una tool distinta — sigue siendo la misma *forma* de
acceso (rango temporal de una señal), solo cambia la resolución. La regla
de bolsillo: **una tool nueva se justifica por una forma de acceso nueva,
no por cada variación de parámetros de una forma ya cubierta.**

---

## 5. El principio de diseño que se deriva de todo esto

De los pasos anteriores sale un principio que vas a aplicar en cada fase
siguiente, así que conviene dejarlo explícito ahora:

> **Cada tool es una función Python fija y parametrizada que golpea
> exactamente un recurso de solo lectura. El modelo elige la función y
> los parámetros; nunca la forma de la consulta.**

De este único principio se derivan, sin necesidad de decidirlas por
separado, varias reglas que verás aplicadas en el código real:

- Cada tool valida sus parámetros antes de tocar la base de datos
  (Fase 5) — porque "parametrizada" no significa "confía en lo que manda
  el modelo".
- Toda tool es de solo lectura contra `uns_silver_postgres` — el
  principio dice "consulta", no "modifica".
- Los guardrails (límite de filas, rango máximo en crudo) viven en tu
  código, no en el prompt ni en el modelo — porque tú controlas la forma
  de la consulta, así que tú controlas también sus límites.

---

## 6. Otras decisiones que se toman en esta fase (antes de escribir código)

Diseñar no es solo "qué tools" — hay un puñado de decisiones de alcance
que, si no se toman ahora, se acaban decidiendo de facto y mal a mitad de
la implementación. Este proyecto las dejó escritas en la tabla "Key
Decisions" del design spec; la forma de leerla como ejercicio de diseño,
no como lista ya resuelta, es preguntarte *el porqué* de cada una:

| Decisión | Lo que se eligió | Por qué se decide ya, no después |
|---|---|---|
| Proveedor de LLM | Ollama local, tras una interfaz `LLMProvider` propia (no el SDK/Tool-Runner de un proveedor concreto) | Si acoplas el bucle agente al SDK de un proveedor concreto desde el primer día, cambiar de proveedor implica reescribir el bucle, no solo un adaptador. Decidir la interfaz antes de escribir el primer adaptador evita ese acoplamiento. |
| Multi-turno persistente | Conversaciones guardadas en Postgres, no en memoria | Una pregunta de seguimiento ("¿y ayer?") necesita el historial. Si empiezas con estado en memoria "porque es más simple" y lo migras después, migras también el formato de mensajes — más caro que decidirlo ahora. |
| Identidad de usuario | Selector sin contraseña, con un `user_id` estable | No se construye auth real todavía, pero el esquema de `conversations`/`messages` se diseña para que cambiar *cómo* se obtiene `user_id` no toque esas tablas. |
| Gestión del contexto | Truncar a los últimos N mensajes, sin resumen todavía | Los modelos locales vía Ollama tienen ventanas de contexto pequeñas (8K-32K). Decidir "no hay compactación aún" es una decisión de alcance explícita (YAGNI), no un olvido — lo verás documentado como limitación conocida, no descubierto como bug. |
| Guardrails de tools | Límite de filas y de rango temporal en crudo, configurables | Se decide en esta fase, aunque se *implemente* en la Fase 5, porque es una propiedad del contrato de la tool (sección 4), no un detalle de implementación. |

El patrón general: **cualquier decisión que, si la tomas tarde, obliga a
tocar una capa ya construida, se toma en esta fase.** Cualquier decisión
que se puede posponer sin coste (streaming, resumen de historial, más
proveedores) se declara explícitamente "fuera de alcance por ahora" y se
documenta como tal — no se decide por omisión.

---

## 7. La arquitectura, como consecuencia de las decisiones — no al revés

Con las decisiones de las secciones 4 y 6 ya tomadas, la arquitectura casi
se dibuja sola — es el resultado, no el punto de partida:

```
UNS_SILVER (ya existe)                UNS_COPILOT (lo que vas a construir)
┌─────────────────────┐               ┌──────────────────────────────┐
│ uns_silver_postgres  │◄─solo lectura─┤ backend (FastAPI)             │
│ signal_catalog        │  4 tools     │  - bucle agente               │
│ silver_latest_value   │              │  - 4 tools parametrizadas     │
│ silver_readings(_1m/  │              │  - LLMProvider (interfaz)     │
│  _1h)                 │              └──────┬─────────────────┬─────┘
│ silver_events          │                     │                 │
└─────────────────────┘               ┌────────┴───────┐   ┌─────┴─────┐
                                       │ propia Postgres │   │  Ollama   │
                                       │ users/conv/msgs │   │ (externo) │
                                       └────────────────┘   └───────────┘
```

Fíjate en lo que **no** aparece: no hay una capa "genérica de acceso a
datos", no hay un traductor NL→SQL, no hay una capa de permisos SQL
dinámicos. No están porque las decisiones de las secciones 2-5 ya las
descartaron — el diagrama es una consecuencia, no una entrada nueva de
diseño.

---

## 8. Checkpoint de esta fase (sin código — es un ejercicio de diseño)

Como todavía no hay nada que ejecutar, el checkpoint es un **entregable
escrito**: antes de pasar a la Fase 2, rellena tú mismo esta tabla — sin
mirar `UNS_COPILOT/backend/app/tools/schemas.py` todavía — para cada una
de las cuatro tools:

| Campo a rellenar | `get_catalog` | `get_latest_value` | `query_readings` | `list_events` |
|---|---|---|---|---|
| ¿Qué pregunta de usuario resuelve? | | | | |
| ¿Qué parámetros necesita, mínimo? | | | | |
| ¿Cuáles son opcionales? | | | | |
| ¿Qué recurso de `UNS_SILVER` consulta? | | | | |
| ¿Necesita un guardrail? ¿Cuál? | | | | |

**Cómo te auto-verificas:** cuando la tengas rellena, compárala con la
tabla real del spec (`docs/superpowers/specs/2026-09-06-uns-copilot-design.md`,
sección "Section 4 — Tools") y con `tools/schemas.py` (lo verás en detalle
en la Fase 5). Si tu tabla coincide en la forma de acceso (aunque no
acierta el nombre exacto de cada parámetro) — vas bien, entiendes el
*por qué*. Si te salió una tool de más o de menos, vuelve a la sección 4
y repite el paso 3 (agrupar por forma de acceso, no por pregunta literal)
para la pregunta que te descuadró.

Este ejercicio es literalmente lo que hay que hacer en cualquier proyecto
real de tool-calling antes de escribir una sola línea: diseñar el
contrato en papel, revisarlo, y solo entonces implementarlo. En este
repo, ese "papel" es justo el documento
`docs/superpowers/specs/2026-09-06-uns-copilot-design.md` — que existe
*antes* que cualquier código en `UNS_COPILOT/backend/`, con fecha de
aprobación 2026-09-09, un día antes del primer commit de código.

---

## 9. Qué sigue

Con el contrato de las cuatro tools decidido (aunque todavía no exista una
sola línea de Python), la Fase 2 monta el **esqueleto** del proyecto:
estructura de carpetas, `docker-compose.yml`, `.env.example`, Dockerfile y
scripts — el andamiaje mínimo para que exista un contenedor que arranca,
antes de escribir ninguna lógica de negocio dentro. Avísame cuando tengas
la tabla del checkpoint rellena y seguimos con la Fase 2.
