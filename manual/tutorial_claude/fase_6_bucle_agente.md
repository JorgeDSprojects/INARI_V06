# Fase 6 — El bucle agente: donde todo se conecta

Con la capa LLM (Fase 4) y las tools (Fase 5) verificadas cada una por su
cuenta, esta fase las conecta. Es la pieza que convierte "un modelo que
puede pedir funciones" y "funciones que se pueden ejecutar con seguridad"
en algo que de verdad mantiene una conversación. Vive en dos archivos:
`app/agent/history.py` (traducción) y `app/agent/loop.py` (orquestación).

---

## 1. Qué tiene que resolver esta capa, antes de escribir nada

Antes de tocar código, enumera lo que el bucle tiene que hacer — porque
cada punto de esta lista se convierte, uno a uno, en una decisión de
diseño concreta más abajo:

1. Coger el texto del usuario y guardarlo.
2. Reunir el historial de la conversación en el formato que espera
   `LLMProvider.chat()` (Fase 4) — que es distinto del formato en que
   está guardado en la tabla `messages` (Fase 3).
3. Preguntarle al modelo qué quiere hacer.
4. Si pide tools, ejecutarlas (Fase 5) y devolverle los resultados.
5. Repetir 3-4 hasta que el modelo ya no pida más tools.
6. No repetir eso indefinidamente si el modelo se queda "enganchado".
7. Guardar cada paso, no solo el resultado final, para poder
   reconstruir la conversación completa después.

Los puntos 2 y 6-7 son los que no son obvios hasta que te sientas a
implementarlos — los desarrollamos en las secciones siguientes en el
orden en que te los vas a encontrar tú mismo.

---

## 2. Primero, la traducción: `app/agent/history.py`

El punto 2 de la lista anterior es el primer problema real: la tabla
`messages` (Fase 3) guarda `role`/`content`/`tool_calls`/`tool_call_id`
como columnas separadas; `LLMProvider.chat()` (Fase 4) espera una lista
de `dict` en formato OpenAI, donde un mensaje del asistente con tool
calls tiene una forma particular anidada. Se resuelve con una función de
traducción, separada del bucle:

```python
def to_llm_messages(messages: list[Message]) -> list[dict]:
    result = []
    for m in messages:
        if m.role == "tool":
            result.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant" and m.tool_calls:
            result.append({
                "role": "assistant",
                "content": m.content,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                    for tc in m.tool_calls
                ],
            })
        else:
            result.append({"role": m.role, "content": m.content or ""})
    return result
```

Por qué esto es una función separada y no unas líneas sueltas dentro del
bucle: **es la traducción exacta e inversa** de cómo se guarda un mensaje
`role="assistant"` con `tool_calls` en la Fase 3 (columna `JSONB`, forma
plana) frente a cómo lo exige el wire format OpenAI (forma anidada, con
`arguments` como *texto* JSON, no como dict). Tener esta traducción
aislada en un solo sitio significa que si el día de mañana cambias cómo
se guarda `tool_calls` en la base de datos, solo tocas esta función — el
bucle nunca sabe ni le importa la forma exacta de las columnas.

Fíjate en el detalle inverso al que viste en `OllamaAdapter` (Fase 4,
sección 3): allí, `arguments` llegaba del modelo como *texto* y se
parseaba a `dict` para guardarlo. Aquí, al reconstruir el historial para
mandárselo de nuevo al modelo, `arguments` se vuelve a convertir a texto
(`json.dumps(tc["arguments"])`) — porque así es como el wire format
OpenAI espera recibir los argumentos de una tool call dentro del
historial. Es la misma frontera, cruzada en direcciones opuestas.

---

## 3. El bucle: `app/agent/loop.py` — construcción incremental

No lo escribas de una vez. Constrúyelo respondiendo, en orden, a cada
punto de la lista de la sección 1.

### 3.1 — El mensaje de sistema (antes de cualquier historial)

El modelo necesita contexto que no está en la conversación: quién es, y
sobre todo, *qué hora es* — sin eso, no puede resolver "¿qué pasó hoy?"
en un `from_time`/`to_time` concretos que exigen las tools.

```python
def _system_message() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "role": "system",
        "content": (
            "You are UNS Copilot, an assistant answering questions about an industrial plant's "
            "Unified Namespace data. "
            f"The current time is {now} (UTC, ISO 8601) — resolve relative expressions such as "
            '"today" or "the last hour" against it and always pass explicit from_time/to_time values. '
            "Signals are identified by an ISA-95 topic and a signal_key within that topic; use "
            "get_catalog to discover what is available before querying readings or events. "
            "Base every answer on tool results — never invent a measurement."
        ),
    }
```

**Decisión que se toma al construir esta función, no antes:** ¿se guarda
este mensaje en la base de datos, como los demás? No. Se genera de nuevo
en cada turno y nunca se persiste — porque la hora que contiene deja de
ser correcta en el instante siguiente. Guardar un "ahora" viejo en el
historial sería peor que no guardarlo: el modelo lo leería como si fuera
verdad. Es la primera vez que te encuentras, al construirlo tú mismo, con
un principio general: **cualquier dato que cambie con el tiempo va en un
system prompt fresco, nunca en el historial persistido** — lo volverás a
ver si añades identidad de usuario al prompt más adelante.

También merece explicarse la frase *"Base every answer on tool results —
never invent a measurement"* — no es relleno. Es la única defensa contra
alucinación de datos que existe en este sistema: no hay ningún mecanismo
de código que impida al modelo decir un número inventado en `content` si
decide no llamar a ninguna tool. La única barrera es la instrucción.
Vale la pena construirlo sabiendo eso — es una limitación real, no una
garantía, y el motivo de la sección 8 (verificación manual) es
precisamente comprobar que el modelo que uses de verdad la respeta.

### 3.2 — El esqueleto del bucle, sin tools todavía

Antes de conectar la ejecución de tools, escribe la versión que solo
resuelve el caso "el modelo responde directamente, sin pedir nada":

```python
async def run_turn(db_session, silver_session, llm, conversation_id, user_text, *,
                    max_iterations, max_history_messages, row_limit, max_raw_range_hours):
    await create_message(db_session, conversation_id, role="user", content=user_text)

    for _ in range(max_iterations):
        history = await load_messages(db_session, conversation_id, limit=max_history_messages)
        messages = [_system_message(), *to_llm_messages(history)]
        response = await llm.chat(messages=messages, tools=TOOL_SCHEMAS)

        await create_message(db_session, conversation_id, role="assistant",
                              content=response.content,
                              tool_calls=[tc.model_dump() for tc in response.tool_calls] or None)

        if not response.tool_calls:
            return response.content or _FALLBACK_REPLY

        # (sección 3.3: qué hacer si SÍ hay tool_calls)

    return _FALLBACK_REPLY
```

Ya en este punto puedes verificarlo (checkpoint parcial, sección 5) con
un `FakeLLMProvider` que devuelve una sola respuesta sin tool calls — el
caso más simple de todos: usuario pregunta, modelo responde, fin.

**Por qué `for _ in range(max_iterations)` y no `while True`:** esta
decisión se toma en este mismo momento, no se añade "por si acaso"
después. Un modelo que se queda pidiendo tools sin nunca llegar a una
respuesta final (porque no sabe interpretar el resultado, por ejemplo)
consumiría recursos sin límite con un `while True`. El bucle acotado es
la cota de seguridad más simple posible, y se decide desde la primera
versión que escribes, no se parchea cuando falla en producción.

**Por qué recargar `load_messages` en cada iteración**, en vez de mantener
`messages` como variable local que vas anexando: porque cada paso del
bucle se persiste inmediatamente (`create_message`) — si el proceso se
cayera a mitad de una secuencia de tool calls, releer de la base de datos
en la siguiente iteración (o en un reintento tras reinicio) reconstruye
el estado real, en vez de depender de una variable en memoria que se
perdió con el proceso.

### 3.3 — Conectar la ejecución de tools

Con el esqueleto verificado, añade el caso que faltaba: qué pasa cuando
`response.tool_calls` no está vacío.

```python
        for call in response.tool_calls:
            result = await execute_tool(
                silver_session, call.name, call.arguments,
                row_limit=row_limit, max_raw_range_hours=max_raw_range_hours,
            )
            await create_message(
                db_session, conversation_id, role="tool", tool_call_id=call.id,
                content=json.dumps(result, ensure_ascii=False, default=str),
            )
```

Este `for` — dentro del `for` de iteraciones — es donde de verdad se ve
por qué las Fases 4 y 5 se construyeron con las garantías que tienen:
`execute_tool` (Fase 5, sección 6) **nunca lanza**, así que este bucle
interno puede procesar varias tool calls del mismo turno sin un
`try/except` propio — cualquier fallo ya llegó convertido en
`{"error": ...}` antes de salir de `execute_tool`.

`default=str` en el `json.dumps` final: es una **red de seguridad**, no
el mecanismo principal de normalización — ese ya lo resolvió
`row_to_json_safe` en la Fase 5, sección 4, *antes* de que el resultado
llegue aquí. `default=str` solo entra en juego si algún tipo no previsto
se coló sin pasar por esa normalización; degradarlo a texto en vez de
crashear el turno entero es la aplicación, una vez más, del mismo
principio de toda esta fase: un fallo imprevisto nunca debe tumbar la
conversación si se puede degradar con elegancia.

### 3.4 — Un detalle que solo se descubre escribiendo el test, no leyendo el código

Antes de dar por terminado el bucle, hay un problema que la Fase 3
(`load_messages`, sección de checkpoint) ya adelantó y que aquí se
entiende *por qué* importa: si truncas el historial por cantidad de
mensajes (`limit=max_history_messages`), la ventana puede empezar justo
en un mensaje `role="tool"` cuyo `role="assistant"` con la petición
original quedó fuera. El wire format OpenAI exige que un mensaje `tool`
siga inmediatamente a la petición `assistant` que lo generó — mandarle al
proveedor un historial que empieza con un `tool` huérfano lo rechaza con
un 400. `load_messages` (Fase 3) ya resuelve esto recortando hacia
delante cualquier `tool` huérfano al principio de la ventana — un
ejemplo perfecto de por qué construir de abajo hacia arriba (Fase 3 antes
que Fase 6) evita que descubras este bug *dentro* del bucle agente, donde
sería mucho más difícil de aislar.

---

## 4. Un detalle de persistencia que se te puede pasar: `_touch_conversation`

El código real añade una pieza más, que no está en la lista original de
la sección 1 pero que se vuelve obvia en cuanto pruebas el endpoint de
listar conversaciones (Fase 7): ¿cómo sabe `GET /conversations/?user_id=`
ordenar por actividad reciente? `Conversation.updated_at` tiene
`onupdate=func.now()` (Fase 3), pero eso solo dispara con un `UPDATE`
explícito sobre esa fila — insertar mensajes nuevos en `messages` nunca
toca la fila de `conversations` por sí solo.

```python
async def _touch_conversation(db_session, conversation_id, user_text) -> None:
    values = {"updated_at": func.now()}
    first_line = user_text[:50].strip()
    if first_line:
        values["title"] = func.coalesce(Conversation.title, first_line)
    await db_session.execute(update(Conversation).where(Conversation.id == conversation_id).values(**values))
    await db_session.commit()
```

Se llama justo después de guardar el mensaje del usuario, al principio de
`run_turn`. `func.coalesce(Conversation.title, first_line)` — no
`Conversation.title = first_line` a secas — es la forma de decir "pon un
título solo si no hay uno ya": la primera vez, `title` es `NULL` y
`COALESCE` devuelve `first_line`; las siguientes veces, `title` ya tiene
un valor y `COALESCE` lo conserva, sin sobreescribir un título que el
usuario pudiera haber editado a mano más adelante.

---

## 5. Checkpoint de esta fase

Todo verificable con `FakeLLMProvider` (Fase 4) — **sin Ollama real**, y
sin mocks de Postgres gracias a que estos tests sí usan una base real
pero controlada:

```bash
cd UNS_COPILOT/backend
DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot \
  pytest tests/test_agent_loop.py -v
```

**Casos que debes ver en verde, y qué verifica cada uno:**

- `test_final_answer_with_no_tool_calls` — el caso más simple (sección
  3.2): el modelo responde directamente, el bucle no llama a ninguna
  tool.
- `test_empty_final_answer_falls_back_to_a_readable_message` — un modelo
  que "calla" (`content=None`, sin tool calls) nunca debe llegar al
  usuario como respuesta vacía; cae al mensaje de fallback.
- `test_one_tool_call_then_final_answer` — el caso central (sección 3.3):
  pide una tool, recibe el resultado, responde. Comprueba explícitamente
  que la **segunda** llamada al modelo incluye un mensaje `role: "tool"`
  en `llm.calls[1]` — es la prueba directa de que la traducción de la
  sección 2 y la persistencia de la sección 3.3 encajan.
- `test_max_iterations_exhausted_returns_graceful_fallback` — un modelo
  que pide tools sin parar (sección 3.2, la cota de seguridad) termina en
  el mensaje de fallback, no en un cuelgue.

Si alguno falla, no sigas a la Fase 7 sin entender por qué — cada uno de
estos cuatro casos protege una decisión concreta de las secciones
anteriores, y un fallo aquí normalmente señala qué sección no se
implementó como se describe.

---

## 6. Qué sigue

Con el bucle agente completo y verificado en sus cuatro casos límite, la
Fase 7 lo expone por HTTP: los routers de FastAPI, los esquemas de
request/response, y el `main.py` completo (con `lifespan`, ahora sí) que
conecta todo lo construido hasta ahora en un servicio que responde de
verdad a `curl`.
