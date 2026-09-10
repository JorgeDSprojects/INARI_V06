# Fase 10 — Qué se dejó fuera a propósito, y cómo lo extenderías

Con el sistema completo funcionando (Fase 9), esta última fase no añade
código. Repasa, una por una, las decisiones de alcance que ya se
asomaron en fases anteriores como "fuera de alcance por ahora" — y las
trata con el mismo rigor que el resto del tutorial: no como una lista de
"cosas que faltan", sino como decisiones de diseño tan deliberadas como
las cuatro tools de la Fase 1. Saber justificar un YAGNI es tan parte de
construir un sistema como saber justificar lo que sí se construyó.

---

## 1. Por qué esta fase importa tanto como las nueve anteriores

Un error común al aprender a construir sistemas es pensar que "diseño"
termina cuando decides qué construir. La mitad que falta es decidir, con
la misma explicitud, **qué no construir todavía** — y dejarlo escrito,
no solo omitido. La diferencia entre "no lo construimos porque no se nos
ocurrió" y "no lo construimos, y aquí está por qué, y aquí está la señal
que dispararía construirlo" es la diferencia entre deuda técnica
accidental y alcance gestionado a propósito. El spec real de este
proyecto (`docs/superpowers/specs/2026-09-06-uns-copilot-design.md`)
termina con una sección `Explicitly deferred` — no es un accidente de
formato, es la mitad final del ejercicio de diseño de la Fase 1.

---

## 2. Lo que se dejó fuera, y la señal que dispararía construirlo

### Interfaz de chat gráfica

**Por qué fuera:** el alcance de este proyecto, decidido en la Fase 1
(sección 6, "Round scope"), es *solo backend* — un chat consumible por
`curl`/Swagger. Una UI es un sub-proyecto propio, con su propio ciclo de
spec/plan, no una extensión trivial de lo que ya existe.
**Cómo la construirías tú:** el endpoint `POST
/conversations/{id}/messages` (Fase 7) ya devuelve exactamente lo que una
UI necesitaría — no requiere ningún cambio en el backend para empezar,
solo un cliente nuevo que lo consuma.
**Señal para construirla:** en cuanto alguien que no sea desarrollador
necesite usar esto — un operario de planta no va a abrir Swagger.

### Autenticación real

**Por qué fuera:** decidido explícitamente en la Fase 1 (sección 6) — el
selector de usuario sin contraseña es forward-compatible a propósito: el
esquema `users`/`conversations`/`messages` (Fase 3) no cambia cuando
llegue auth real, solo cambia *cómo* se obtiene `user_id` en el router
(Fase 7).
**Cómo la construirías tú:** sustituir el `GET /users/` + selección
manual por un flujo OAuth/JWT que resuelva `user_id` a partir del token
— ningún cambio en `models/chat.py`, `crud.py` ni `agent/loop.py`.
**Señal para construirla:** en cuanto esto salga de un entorno de
desarrollo/demo controlado.

### Compactación/resumen del historial

**Por qué fuera:** la Fase 6 (sección 3.2) ya usa truncado simple por
cantidad (`MAX_HISTORY_MESSAGES`) — una decisión de alcance explícita
documentada como "limitación conocida", no un bug pendiente. Resumir de
verdad requeriría, a su vez, otra llamada a un LLM (para generar el
resumen) — complejidad real que solo se justifica si el truncado simple
demuestra ser insuficiente en uso real.
**Cómo la construirías tú:** cuando `load_messages` (Fase 3/6) recorte
mensajes fuera de la ventana, en vez de simplemente descartarlos,
generarías un resumen de ellos (con una llamada aparte, más barata, a un
modelo) y lo insertarías como un mensaje `role="system"` adicional al
principio del historial truncado.
**Señal para construirla:** cuando una conversación real necesite más
contexto del que cabe en `MAX_HISTORY_MESSAGES` sin perder información
que el usuario esperaba que el modelo recordara — algo que solo se ve con
uso real, no especulando.

### Más adaptadores de `LLMProvider` (OpenAI, OpenRouter, Anthropic-nativo)

**Por qué fuera:** la Fase 4 ya construyó la interfaz pensada
explícitamente para esto — el punto entero de `LLMProvider` es que
añadir un proveedor no toque el bucle agente ni las tools.
**Cómo la construirías tú:** para OpenAI/OpenRouter, sería casi una copia
de `OllamaAdapter` (mismo wire format, Fase 4 sección 3) cambiando solo
`base_url`. Para Anthropic-nativo, la traducción real vive *dentro* de
ese adaptador nuevo — tool_use/tool_result en vez de tool_calls — sin
tocar `ChatResponse`/`ToolCall` (Fase 4, sección 2) ni nada aguas abajo.
**Señal para construirla:** en cuanto necesites comparar coste/calidad
entre un modelo local y uno hosted, o el hardware local (DGX/RTX) deje de
ser suficiente para la carga real.

### Streaming de respuestas

**Por qué fuera:** `run_turn` (Fase 6) devuelve la respuesta completa de
una vez — razonable mientras el único consumidor es `curl`/Swagger
(Fase 9); una UI de chat de verdad (que no existe todavía, ver arriba)
es lo que hace valioso el streaming token a token.
**Cómo lo construirías tú:** cambiaría la forma de `LLMProvider.chat()`
(Fase 4) a un generador async que produce fragmentos, y `run_turn`
tendría que decidir streaming vs. no según si hay tool calls en curso
(no tiene sentido "streamear" una decisión de qué tool llamar, solo la
respuesta final en lenguaje natural).
**Señal para construirla:** en cuanto exista una UI (la primera pieza que
falta, arriba) donde la latencia percibida importe.

### `INFRAESTRUCTURA/` (el propio stack de Langfuse)

**Por qué fuera:** es un prerequisito de infraestructura compartida entre
proyectos, con su propio ciclo de vida — la Fase 8 ya construyó el
*punto de integración* (el contrato cliente: `LangfuseTracer`) sin
necesitar que el stack exista todavía. Es la misma relación que
`UNS_SILVER` tiene con `UNS_HISTORIAN` como prerequisito que no gestiona
(ver `CLAUDE.md`).
**Señal para construirlo:** en cuanto más de un proyecto en este repo
necesite tracing compartido — no vale la pena montarlo para uno solo.

---

## 3. El patrón general, para cuando construyas tu propio sistema

De los seis casos anteriores sale un método reutilizable, no solo una
lista de "cosas de este proyecto":

1. **Nombra la decisión explícitamente** ("no construimos X"), no la
   dejes implícita por omisión.
2. **Justifica el porqué con la misma seriedad que justificarías
   construirlo** — casi siempre es "la complejidad real de X solo se
   justifica con una necesidad que todavía no existe", no "no dio
   tiempo".
3. **Escribe la señal concreta que dispararía construirlo** — no "cuando
   haga falta" (vago) sino la condición observable exacta (como en la
   tabla anterior). Sin esto, "lo dejamos para después" tiende a
   convertirse en "nunca se revisó".
4. **Verifica que la interfaz de lo que sí construiste no cierra la
   puerta** a lo que dejaste fuera — es exactamente lo que hace que
   `LLMProvider` (Fase 4) o el esquema `users`/`conversations`/`messages`
   (Fase 3) sigan sirviendo el día que se construya lo que hoy es YAGNI.

---

## 4. El tutorial, de principio a fin — qué construiste

| Fase | Qué añadiste | Principio que aprendiste |
|---|---|---|
| 1 | Nada de código — el contrato de las 4 tools, en papel | Diseñar el contrato antes de programarlo; derivar tools de *formas de acceso*, no de tablas ni de intuición |
| 2 | Docker, config, esqueleto HTTP mínimo | El andamiaje se verifica antes que la lógica de negocio |
| 3 | Modelo de datos, dos motores de DB separados | El esquema en código (no SQL) cuando no hay necesidad de migraciones complejas; separar físicamente lectura de escritura |
| 4 | `LLMProvider`, `OllamaAdapter`, `FakeLLMProvider` | Una interfaz antes que su consumidor, para que lo que dependa de ella sea testeable sin red |
| 5 | Las 4 tools: validar → ejecutar → normalizar → contrato | Nunca confiar en lo que manda el modelo, en ninguna capa; SQL siempre parametrizado |
| 6 | El bucle agente | Cota de iteraciones obligatoria; system prompt fresco, nunca persistido; persistir cada paso, no solo el final |
| 7 | Routers, esquemas HTTP, `main.py` completo | La capa HTTP no debería tener lógica de negocio propia si todo lo de abajo está bien construido |
| 8 | Tracer opcional con Langfuse | Una dependencia externa opcional nunca debe poder romper el camino principal |
| 9 | Verificación extremo a extremo | Verificar capa por capa hace que un fallo al final tenga un espacio de búsqueda pequeño |
| 10 | Nada de código — el alcance explícito | Decidir qué no construir es tan parte del diseño como decidir qué sí |

Esto es, punto por punto, el mismo sistema que ya existe en
`UNS_COPILOT/backend/` — pero ahora sabes, de cada línea, no solo qué
hace sino por qué existe y en qué orden tendría sentido haberla escrito.
Esa es la diferencia entre leer un código y saber replicarlo.
