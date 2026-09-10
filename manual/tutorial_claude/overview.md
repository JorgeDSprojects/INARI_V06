# Tutorial — Construye tú mismo `UNS_COPILOT` (mapa del curso)

Este no es un tutorial de "cómo usar UNS_COPILOT" (eso ya está en
`manual/es/02-probar-uns-copilot.md`) ni de "cómo leer su código" (eso está
en `manual/es/03-tutorial-function-calling.md`). Es un tutorial de
**cómo lo construirías tú, desde cero, en el orden en que de verdad
conviene construirlo** — qué decides primero, qué código añades en cada
paso, por qué ese orden y no otro, y cómo compruebas que cada pieza
funciona antes de construir la siguiente encima.

Cada fase termina con un **checkpoint verificable**: algo que ejecutas (un
test, un `curl`, una consulta SQL) y una salida esperada concreta. No
avanzamos a la fase siguiente sin ese checkpoint en verde — es la misma
disciplina que ya usa este repo (ver `AGENTS.md` / los `docs/superpowers`
de cada servicio: spec antes que plan, plan antes que código, verificación
antes que "está hecho").

> **Nota sobre una versión anterior de este tutorial:** existió un primer
> intento (sesiones 1-5) que describía una estructura de código inventada
> — no coincidía con `UNS_COPILOT/backend/app/` real (rutas de archivo,
> SQLAlchemy síncrono en vez de async, un `TOOL_DEFINITIONS` que no existe,
> faltaba la normalización de tipos real, etc.). Se ha descartado. Todo lo
> que sigue está verificado línea a línea contra el código que hay
> realmente en el repo — cuando cito una línea, existe.

---

## Cómo se estructura

**Orden de construcción, no orden de las capas en el diagrama.** Un error
común es construir "de fuera hacia dentro" (primero la API HTTP, luego
las tools, luego el LLM) porque es como se *dibuja* la arquitectura. Aquí
construimos **de dentro hacia fuera**: primero las piezas que no dependen
de nada (el contrato de una tool, la validación) y solo al final las que
dependen de todo lo anterior (el endpoint HTTP). Esto tiene una ventaja
muy concreta: cada fase se puede probar con tests unitarios *sin* Docker,
sin Postgres y sin un LLM real corriendo — exactamente como está hecho
este repo (`tests/test_tool_executor.py`, `tests/test_agent_loop.py`
nunca necesitan una base de datos ni un Ollama vivo).

| Fase | Título | Qué se construye | Se puede verificar sin Docker/Ollama |
|---|---|---|---|
| **1** | Planteamiento del proyecto | Nada de código todavía — el problema, la decisión de tool-calling, qué herramientas hacen falta y por qué esas y no otras | ✅ (es una revisión de diseño) |
| **2** | Esqueleto del proyecto | Estructura de carpetas, `docker-compose.yml`, `.env.example`, `Dockerfile`, scripts — el andamiaje antes de escribir lógica | ⚠️ parcial (arranca contenedores vacíos) |
| **3** | Modelo de datos | Tablas `users`/`conversations`/`messages`, por qué ese esquema exacto, `init.sql` | ❌ (necesita Postgres) |
| **4** | Capa LLM | `LLMProvider` (interfaz), `ChatResponse`/`ToolCall`, `OllamaAdapter`, `FakeLLMProvider` | ✅ |
| **5** | Las herramientas (tools) | Validación Pydantic, contrato JSON Schema, funciones de consulta, normalización de tipos, el ejecutor/despachador | ✅ (con mocks de sesión) |
| **6** | El bucle agente | `run_turn`: conecta LLM + tools + historial + persistencia | ✅ (con `FakeLLMProvider`, sin Postgres real gracias a mocks) |
| **7** | La capa HTTP | Routers FastAPI, esquemas de request/response, inyección de dependencias | ⚠️ parcial (`TestClient`, sin Postgres real) |
| **8** | Observabilidad opcional | Tracer con Langfuse, "nunca debe romper el chat" | ✅ |
| **9** | Extremo a extremo real | Todo junto: Docker + Postgres real + Ollama real, prueba manual completa | ❌ (necesita todo vivo) |
| **10** | Qué se dejó fuera a propósito | YAGNI del proyecto: qué no se construyó y por qué, cómo extenderlo tú | — |

## Método de estudio

1. Lee la fase completa antes de tocar nada — incluye el razonamiento, no
   solo el código.
2. Ejecuta el checkpoint de verificación tal cual se describe. Si no
   coincide la salida, para ahí — no sigas acumulando pasos sobre una base
   que no verificaste.
3. Cuando el checkpoint esté en verde, dímelo y seguimos con la fase
   siguiente.
4. Todo el código que veas está citado con archivo y línea real del repo
   — si quieres, ábrelo en paralelo mientras lees.

## Dónde estamos

Las 10 fases están escritas y ancladas al código real:

- ✅ **1 — Planteamiento**: `fase_1_planteamiento.md`
- ✅ **2 — Esqueleto del proyecto**: `fase_2_esqueleto.md`
- ✅ **3 — Modelo de datos**: `fase_3_modelo_datos.md`
- ✅ **4 — Capa LLM**: `fase_4_capa_llm.md`
- ✅ **5 — Las herramientas**: `fase_5_tools.md`
- ✅ **6 — El bucle agente**: `fase_6_bucle_agente.md`
- ✅ **7 — La capa HTTP**: `fase_7_capa_http.md`
- ✅ **8 — Observabilidad opcional**: `fase_8_observabilidad.md`
- ✅ **9 — Extremo a extremo real**: `fase_9_extremo_a_extremo.md`
- ✅ **10 — Qué se dejó fuera a propósito**: `fase_10_alcance.md`

Ahora toca **recorrerlas de verdad**: lee cada fase, ejecuta su
checkpoint tal cual se describe, y solo entonces pasa a la siguiente — no
las leas todas seguidas sin ejecutar nada, porque el valor está en
verificar cada capa antes de confiar en la siguiente (sección "Método de
estudio" arriba).
