# Tutorial: cómo se programa function calling — caso real con `UNS_COPILOT`

Este manual no es solo "cómo probar UNS_COPILOT" (eso ya lo cubre
`02-probar-uns-copilot.md`) sino **cómo está programado por dentro**, leído
como clase: cada concepto de *function calling / tool calling* explicado y
luego señalado en el archivo y las líneas reales donde vive en este repo.

Todo el código citado está en `UNS_COPILOT/backend/app/`.

---

## 1. El concepto, antes de ver código

**Function calling** (también llamado *tool calling*, *tool use*) es un
mecanismo por el cual un LLM, en vez de responder solo con texto libre,
puede pedirle a **tu programa** que ejecute una función concreta con unos
parámetros concretos, y luego seguir razonando con el resultado.

El LLM **nunca ejecuta código**. Nunca toca una base de datos. Solo hace
tres cosas:

1. Recibe la conversación + una lista de "funciones disponibles" descritas
   como datos (JSON Schema), no como código.
2. Decide (es una predicción estadística, no magia) si necesita llamar a
   alguna, y con qué argumentos — también como JSON.
3. Cuando tu programa le devuelve el resultado, sigue razonando con él y
   redacta la respuesta final.

Todo lo demás — validar los argumentos, ejecutar la función de verdad,
manejar errores, decidir cuándo parar — **lo programas tú**. Ese es el
contenido de este tutorial: no "cómo usar un LLM", sino cómo se construye
alrededor de él el andamiaje que lo hace seguro y fiable.

### Por qué esto y no "que el LLM escriba SQL"

La alternativa obvia — pedirle al modelo que genere SQL directamente — es
lo que este diseño evita explícitamente (ver
`docs/superpowers/specs/2026-09-06-uns-copilot-design.md`, sección
"Context"): es impreciso (un modelo pequeño local se equivoca de columna,
de join, de sintaxis) y es una superficie de inyección — nada garantiza que
el SQL generado sea de solo lectura o esté acotado. Con function calling,
**el LLM elige entre un menú cerrado de funciones que tú escribiste**;
nunca genera la consulta, solo los parámetros de una consulta que ya existe
y que tú controlas.

### Las tres piezas mentales

```
┌─────────────┐   1. mensajes + "menú" de tools   ┌─────────────┐
│  Tu backend │ ────────────────────────────────► │     LLM     │
│ (el bucle)  │                                    │             │
│             │ ◄──────────────────────────────── │             │
└──────┬──────┘   2. "quiero llamar a X(args)"     └─────────────┘
       │             o "aquí está la respuesta"
       │ 3. tú validas y ejecutas X(args) de verdad
       ▼
┌─────────────┐
│  Ejecutor   │──► valida parámetros (Pydantic) ──► SQL parametrizado ──► DB
│ (dispatcher)│◄── nunca lanza excepción, siempre devuelve algo ─────────┘
└─────────────┘
       │
       └── el resultado vuelve al paso 1, como un mensaje más, y el
           bucle repite hasta que el LLM ya no pide más tools
```

Tres piezas que vas a ver una y otra vez en cualquier implementación de
function calling, sea con Ollama, OpenAI o Anthropic:

- **El contrato** (`TOOL_SCHEMAS` en `tools/schemas.py`): qué funciones
  existen, descritas en un formato que el modelo entiende.
- **El bucle** (`run_turn` en `agent/loop.py`): la conversación entre tu
  código y el modelo, iteración a iteración.
- **El ejecutor** (`execute_tool` en `tools/executor.py`): el puente entre
  "el modelo pidió esto" y "esto se ejecutó de verdad, con seguridad".

---

## 2. El contrato: cómo le describes una función al modelo

El modelo no ve tu código Python. Ve exactamente esto —
`tools/schemas.py:1-26` (recortado a una tool):

```python
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_catalog",
            "description": (
                "List cataloged signals/KPIs. Use this to discover what signals exist before "
                "querying their readings, or to answer 'what does X measure?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_filter": {
                        "type": "string",
                        "description": "ISA-95 topic prefix to filter by, e.g. 'plant1.line3'. Omit to list everything.",
                    },
                    "signal_type": {
                        "type": "string",
                        "enum": ["raw", "kpi"],
                        "description": "Restrict to raw physical signals or computed KPIs. Omit for both.",
                    },
                },
                "required": [],
            },
        },
    },
    ...
]
```

Esto es **JSON Schema** — el mismo formato que usa OpenAPI/Swagger. Es el
estándar de facto que adoptaron OpenAI, y que Ollama y la mayoría de
proveedores siguen porque exponen un endpoint "compatible con OpenAI".

Puntos a interiorizar:

- **`name`** es literalmente el identificador que el modelo va a devolver
  luego para decir "quiero llamar a esta". Tiene que coincidir
  exactamente con como lo registras en tu ejecutor (sección 5).
- **`description`** no es un comentario decorativo — es la única
  información que tiene el modelo para decidir *cuándo* usar esta función
  y no otra. Aquí es donde de verdad "programas" el comportamiento del
  agente: una descripción ambigua ("obtener datos") produce llamadas mal
  dirigidas; una descripción como la de arriba, que dice explícitamente
  *"Use this to discover what signals exist before querying"*, empuja al
  modelo a llamar a `get_catalog` antes que a `query_readings` cuando no
  sabe el `signal_key` exacto. Esto es *prompt engineering aplicado a
  tools*, no a la conversación.
- **`parameters`** es un JSON Schema de objeto: cada propiedad con su tipo,
  descripción y (si aplica) `enum`; `required` lista cuáles son
  obligatorios. Fíjate que aquí **no hay ninguna validación real** — es
  solo lo que se le enseña al modelo. La validación real está en la
  sección 5, y es imprescindible: nada impide que el modelo mande
  `topic_filter: 123` (un entero) aunque el schema diga `"string"`.

Las cuatro tools completas (`get_catalog`, `get_latest_value`,
`query_readings`, `list_events`) están en `tools/schemas.py`; cada una
mapea 1:1 a una tabla de `UNS_SILVER` (ver
`docs/superpowers/specs/2026-09-06-uns-copilot-design.md`, sección 4).

---

## 3. Lo que el modelo responde: `tool_calls`

Cuando decide usar una función, el modelo no ejecuta nada — devuelve una
estructura de datos pidiéndotelo. Normalizada en este proyecto como
(`llm/base.py:8-17`):

```python
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class ChatResponse(BaseModel):
    content: str | None       # texto final, cuando no hay más tool_calls
    tool_calls: list[ToolCall] = []
    finish_reason: str        # "tool_calls" | "stop" | "length" | ...
```

Tres campos que tienes que entender bien porque el resto del bucle gira
sobre ellos:

- **`id`**: un identificador que **el propio modelo** genera para esa
  llamada concreta. No lo inventas tú. Tienes que devolvérselo tal cual
  cuando le mandes el resultado (como `tool_call_id`), porque si el modelo
  pidió varias tools en el mismo turno, así es como sabe qué resultado
  corresponde a qué petición.
- **`name`** / **`arguments`**: qué función y con qué parámetros — texto y
  JSON, sin ninguna garantía de que sean correctos (de ahí la sección 5).
- **`content`**: normalmente `None` cuando hay `tool_calls` (el modelo
  "calla" mientras pide datos) y con texto cuando ya no necesita más
  herramientas — esa es tu señal para terminar el bucle (sección 4).

`OllamaAdapter.chat()` (`llm/ollama_adapter.py:25-47`) es quien construye
este objeto normalizado a partir de la respuesta cruda del SDK de OpenAI:

```python
tool_calls = []
for raw_call in (message.tool_calls or []):
    try:
        arguments = json.loads(raw_call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        # Nunca revienta por una llamada mal formada — la validación
        # Pydantic aguas abajo (sección 5) la rechazará y se lo dirá
        # al modelo como error de tool, no como un crash del turno.
        arguments = {}
    tool_calls.append(ToolCall(id=raw_call.id, name=raw_call.function.name, arguments=arguments))
```

Fíjate en el detalle: `arguments` llega del modelo como una **cadena de
texto JSON**, no como un dict — hay que parsearla, y ese parseo puede
fallar (el modelo puede "alucinar" JSON inválido). El adaptador nunca deja
que ese fallo tumbe el turno; degrada a `{}` y deja que la capa de
validación (sección 5) lo rechace de forma controlada.

---

## 4. El bucle agente: el corazón de todo

Esta es la pieza que de verdad "programa" el comportamiento agéntico.
Versión simplificada de `agent/loop.py:53-98` (`run_turn`):

```python
for _ in range(max_iterations):                       # ← cota de seguridad
    history = await load_messages(db_session, conversation_id, limit=max_history_messages)
    messages = [_system_message(), *to_llm_messages(history)]
    response = await llm.chat(messages=messages, tools=TOOL_SCHEMAS)

    await create_message(db_session, conversation_id, role="assistant",
                          content=response.content, tool_calls=[...])

    if not response.tool_calls:
        return response.content or _FALLBACK_REPLY    # ← condición de salida

    for call in response.tool_calls:
        result = await execute_tool(silver_session, call.name, call.arguments, ...)
        await create_message(db_session, conversation_id, role="tool",
                              tool_call_id=call.id, content=json.dumps(result))

return _FALLBACK_REPLY                                  # ← se acabaron los intentos
```

Cosas que aprender de este bucle, no solo copiarlo:

1. **Es un `for`, no un `while True`.** `max_iterations` (por defecto 5,
   `config.py:14`) es una cota de seguridad obligatoria: sin ella, un
   modelo que se queda "enganchado" pidiendo la misma tool una y otra vez
   (por ejemplo porque no sabe interpretar el resultado) consumiría
   recursos indefinidamente. El test
   `test_max_iterations_exhausted_returns_graceful_fallback`
   (`tests/test_agent_loop.py:87-104`) verifica exactamente este caso: el
   bucle termina con un mensaje legible, no con un error ni un cuelgue.
2. **La condición de salida es "no hay `tool_calls`".** No es "el modelo
   dijo basta" ni un campo especial — simplemente, si `response.tool_calls`
   viene vacío, se asume que `content` es la respuesta final. Esto es
   universal en todas las implementaciones de function calling: el propio
   modelo decide, turno a turno, si necesita más herramientas o ya puede
   responder.
3. **El historial se relee de la base de datos en cada iteración**
   (`load_messages`), no se acumula solo en una variable local en memoria.
   Esto es una decisión deliberada de este proyecto: cada turno del bucle
   (incluida cada llamada a herramienta) se persiste inmediatamente
   (`create_message`), así que si el proceso se cae a mitad de una
   secuencia de tool calls, no se pierde el rastro.
4. **El mensaje de sistema se genera de nuevo en cada turno**
   (`_system_message()`, `agent/loop.py:20-36`) y **nunca se persiste**.
   Contiene la hora actual en UTC, y guardar una hora vieja en el
   historial sería peor que no tener hora — el comentario del propio
   código lo dice: *"a stale 'now' baked into conversation history would
   be worse than none at all"*. Lección general: cualquier dato que
   cambie con el tiempo (fecha/hora, quién eres, qué permisos tienes) va
   en el *system prompt* fresco, nunca en el historial guardado.
5. **Cada resultado de tool se manda de vuelta como un mensaje `role:
   "tool"`** con su `tool_call_id` correlacionado (sección 3). Así el
   modelo, en la siguiente iteración del bucle, ve la conversación
   completa: su propia petición + el resultado + puede razonar sobre él.

`agent/history.py` (`to_llm_messages`) es la pieza que convierte filas de
la tabla `messages` de vuelta al formato de diccionarios que
`LLMProvider.chat()` espera — una traducción directa fila→dict porque el
esquema de la tabla (`role`/`tool_calls`/`tool_call_id`, ver
`models/chat.py:32-41`) se diseñó a propósito para calcar el formato
OpenAI (ver la sección 3 del design spec).

---

## 5. Ejecutar la función de verdad: validar → despachar → nunca lanzar

Aquí es donde se cierra el círculo de seguridad. `tools/executor.py:23-63`
(`execute_tool`):

```python
_PARAM_MODELS = {
    "get_catalog": GetCatalogParams,
    "get_latest_value": GetLatestValueParams,
    "query_readings": QueryReadingsParams,
    "list_events": ListEventsParams,
}

async def execute_tool(session, name, arguments, *, row_limit, max_raw_range_hours) -> dict:
    param_model = _PARAM_MODELS.get(name)
    if param_model is None:
        return {"error": f"Unknown tool: {name!r}"}          # ① nombre desconocido

    try:
        params = param_model(**arguments)
    except (ValidationError, TypeError) as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}   # ② argumentos inválidos

    try:
        if name == "get_catalog":
            result = await get_catalog(session, params, row_limit=row_limit)
        ...
    except Exception as exc:                                  # ③ fallo real (DB caída, etc.)
        if session is not None:
            await session.rollback()
        return {"error": str(exc)}

    return {"result": result}
```

La regla de oro de todo este archivo, y probablemente la lección más
importante de function calling en producción: **`execute_tool` nunca
lanza una excepción hacia el bucle**. Siempre devuelve un diccionario —
`{"result": ...}` o `{"error": ...}` — porque ese diccionario se serializa
tal cual y se le manda al modelo como el resultado de su llamada
(`json.dumps(result)` en `agent/loop.py:95`). Si dejaras que una excepción
se propagara, tumbarías el turno entero (y probablemente el endpoint HTTP)
por, por ejemplo, un parámetro con un typo. En vez de eso, el modelo *ve*
el error como texto y puede reaccionar: pedir la tool otra vez con otros
parámetros, o admitir que no puede responder.

Tres capas de por qué nunca confiar ciegamente en lo que manda el modelo:

1. **① Nombre desconocido** — el modelo podría "alucinar" el nombre de una
   función que no existe. El `dict` `_PARAM_MODELS` actúa como *tabla de
   despacho* (dispatch table): si el nombre no está, error controlado, no
   un `KeyError`.
2. **② Argumentos inválidos** — este es el paso que de verdad hace el
   trabajo pesado, y usa **Pydantic**, no comprobaciones manuales.
   `tools/params.py` define un modelo por tool:

   ```python
   class GetLatestValueParams(BaseModel):
       topic: str
       signal_key: str

   class QueryReadingsParams(_TimeRangeMixin):   # from_time, to_time + validación
       topic: str
       signal_key: str
       agg: Literal["raw", "1m", "1h"] = "raw"
   ```

   `param_model(**arguments)` intenta construir el modelo con lo que mandó
   el modelo; si falta un campo obligatorio, si un tipo no encaja, o si un
   validador personalizado rechaza el valor, Pydantic lanza
   `ValidationError` — capturada y convertida en `{"error": ...}`. Un
   ejemplo de validador personalizado, `tools/params.py:17-24`
   (`_TimeRangeMixin`):

   ```python
   @model_validator(mode="after")
   def _check_time_order(self):
       if self.to_time <= self.from_time:
           raise ValueError(f"to_time ({self.to_time}) must be strictly after from_time ({self.from_time})")
       return self
   ```

   Sin esto, un `to_time` anterior a `from_time` no rompería nada a nivel
   de tipos — simplemente el `BETWEEN` de SQL devolvería cero filas, y el
   modelo concluiría (incorrectamente) que no hay datos, en vez de que la
   pregunta estaba mal formulada. **Validar no es solo "que no crashee":
   es que el modelo reciba el error correcto para poder corregirse.**

   Hay un segundo guardrail, deliberadamente *fuera* del modelo Pydantic
   porque depende de un parámetro de configuración (`max_raw_range_hours`)
   que no es parte del schema del tool: `check_raw_range`
   (`tools/params.py:48-60`) rechaza una consulta `agg='raw'` sobre una
   ventana de tiempo demasiado ancha — sin esto, una pregunta ambigua
   podría intentar traer semanas de datos a 1Hz y reventar el contexto (ya
   de por sí pequeño) de un modelo local.

3. **③ Fallo de verdad en la ejecución** (la base de datos no responde,
   una tabla no existe...) — el `except Exception` genérico en
   `executor.py` es intencional (comentario `# noqa: BLE001 - deliberate
   catch-all`), no un descuido: en este punto concreto, "algo salió mal
   ejecutando la tool" es exactamente lo que quieres capturar, sea lo que
   sea. Fíjate también en el `rollback()`: si una tool falla a mitad de
   una transacción compartida, la sesión queda "envenenada" y cualquier
   tool posterior en el mismo turno fallaría también si no se hace
   rollback — un detalle fácil de pasar por alto y que el test
   `test_a_failing_tool_rolls_the_session_back`
   (`tests/test_tool_executor.py:64-76`) existe justamente para no
   olvidarlo.

### Un problema que no es obvio hasta que te pasa: serializar el resultado

`tools/normalize.py` resuelve algo que rompe a casi todo el mundo la
primera vez que conecta un LLM a una base de datos real: `json.dumps` no
sabe serializar un `datetime` ni un `Decimal`, y ambos son exactamente lo
que devuelve Postgres para columnas `TIMESTAMPTZ` y `NUMERIC`.

```python
def to_json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    ...
```

El comentario del propio archivo explica una decisión sutil: convertir
`Decimal` a `float` en vez de a `str` (que sería la salida por defecto de
`json.dumps(..., default=str)`) porque un número como texto (`"22.0"`)
llega al modelo como algo que tiene que *interpretar*, mientras que un
número JSON real (`22.0`) es algo con lo que puede *razonar
aritméticamente* (comparar, promediar, etc.) directamente.

---

## 6. Por qué existe una capa de abstracción sobre el proveedor

`llm/base.py` define una interfaz `LLMProvider` con un único método
abstracto:

```python
class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...
```

Ahora mismo solo hay una implementación real, `OllamaAdapter`
(sección 3), pero el resto del sistema — el bucle, los tools, la
persistencia — nunca ve el formato específico de Ollama/OpenAI; solo ve
`ChatResponse`/`ToolCall`. El comentario del propio archivo lo explica:
*"no adapter may leak its own wire format ... back past this boundary"*.
Esto importa por una razón muy concreta citada en el design spec: **el
formato de "tool calling" de Anthropic es estructuralmente distinto**
(bloques de contenido `tool_use`/`tool_result` en vez de un array
`tool_calls` separado) — así que el día que se añada un adaptador
Anthropic-nativo, esa traducción vive *solo* dentro de ese adaptador nuevo,
y ni el bucle ni los tools se tocan. Es el patrón *Adapter* aplicado
específicamente para no acoplar el resto del programa a la forma de hablar
de un proveedor concreto.

El mismo mecanismo, además, es lo que hace testeable el bucle sin un
Ollama de verdad corriendo: `llm/fake.py` (`FakeLLMProvider`) implementa la
misma interfaz devolviendo una lista de respuestas ya escritas a mano —
así es como `tests/test_agent_loop.py` prueba "una tool call y luego
respuesta final", "se agotan las iteraciones", etc., sin red ni modelo
real. En FastAPI esto se conecta vía *dependency injection*
(`routers/chat.py:24-27`, `get_llm_provider`), sustituida en tests con
`app.dependency_overrides` en vez de parchear módulos globalmente.

---

## 7. El ciclo completo, con un ejemplo concreto

Pregunta del usuario: *"¿Cuál es el valor actual de Gen_RPM_Avg?"*

```
Usuario                Backend (run_turn)                    LLM (Ollama)
   │                          │                                    │
   │ "¿cuál es el valor       │                                    │
   │  actual de Gen_RPM_Avg?" │                                    │
   ├─────────────────────────►│ guarda role=user                  │
   │                          │                                    │
   │                          │ 1) manda [system, ...historial,    │
   │                          │    user] + TOOL_SCHEMAS ──────────►│
   │                          │                                    │ decide: necesito
   │                          │                                    │ get_latest_value
   │                          │◄─── tool_calls=[{id:"c1",          │
   │                          │      name:"get_latest_value",      │
   │                          │      arguments:{topic:.., ...}}]   │
   │                          │ guarda role=assistant, tool_calls  │
   │                          │                                    │
   │                          │ 2) execute_tool("get_latest_value",│
   │                          │    args) → valida (Pydantic) →     │
   │                          │    SQL parametrizado → Postgres    │
   │                          │    → {"result": {"value_numeric":  │
   │                          │       1561.4, "time": "..."}}      │
   │                          │ guarda role=tool, tool_call_id=c1  │
   │                          │                                    │
   │                          │ 3) manda [system, ...historial     │
   │                          │    (incluye la tool call y su      │
   │                          │    resultado), user] ─────────────►│
   │                          │                                    │ ya tiene el dato,
   │                          │                                    │ redacta respuesta
   │                          │◄─── content="El valor actual es    │
   │                          │      1561.4 RPM...", tool_calls=[] │
   │                          │ guarda role=assistant              │
   │◄─────────────────────────┤ tool_calls vacío → fin del bucle,  │
   │  "El valor actual es     │ devuelve response.content          │
   │   1561.4 RPM..."         │                                    │
```

Nota que hay **dos** llamadas al modelo para una sola pregunta del
usuario: una que decide "necesito la tool" y otra, ya con el resultado en
el historial, que redacta la respuesta final. Esto es justo lo que
verifica `test_one_tool_call_then_final_answer`
(`tests/test_agent_loop.py:63-84`), incluyendo la aserción de que la
segunda llamada al modelo incluye un mensaje `role: "tool"`.

---

## 8. Ejercicio guiado: añade tú una quinta tool

La mejor forma de fijar esto es programarlo. Objetivo: una tool
`get_signal_stats(topic, signal_key, from_time, to_time)` que devuelva
`{min, max, avg, count}` de un signal en un rango, usando `silver_readings`
directamente con funciones de agregación SQL (no reutiliza las tablas
`_1m`/`_1h`, para practicar una query nueva).

Sigue exactamente el mismo camino que ya siguen las otras cuatro tools —
son 4 archivos, en este orden:

1. **`tools/params.py`** — el modelo de validación:
   ```python
   class GetSignalStatsParams(_TimeRangeMixin):
       topic: str
       signal_key: str
   ```
   (reutiliza `_TimeRangeMixin` porque también necesitas `from_time <
   to_time` — no lo reimplementes).

2. **Un nuevo archivo o función en `tools/readings.py`** — la ejecución
   real, SQL parametrizado (nunca f-strings con valores del usuario
   dentro del `WHERE`):
   ```python
   async def get_signal_stats(session, params: GetSignalStatsParams) -> dict | None:
       result = await session.execute(
           text(
               "SELECT MIN(value_numeric) AS min_value, MAX(value_numeric) AS max_value, "
               "AVG(value_numeric) AS avg_value, COUNT(*) AS sample_count "
               "FROM silver_readings WHERE topic = :topic AND signal_key = :signal_key "
               "AND time BETWEEN :from_time AND :to_time"
           ),
           {"topic": params.topic, "signal_key": params.signal_key,
            "from_time": params.from_time, "to_time": params.to_time},
       )
       row = result.mappings().first()
       return row_to_json_safe(row) if row else None   # no olvides normalize.py
   ```

3. **`tools/executor.py`** — regístrala en las dos tablas de despacho:
   añade `"get_signal_stats": GetSignalStatsParams` a `_PARAM_MODELS` y una
   rama `elif name == "get_signal_stats": result = await
   get_signal_stats(session, params)`.

4. **`tools/schemas.py`** — el contrato que ve el modelo, con una
   `description` clara de cuándo usarla (por ejemplo: *"Use for questions
   like 'what was the average/max/min of X over a period'; use
   query_readings instead if the user wants individual data points"* — la
   frase de comparación con otra tool ayuda al modelo a elegir bien).

Y para comprobar que funciona **sin depender de un LLM real**, escribe el
test al estilo de `tests/test_tool_executor.py`: parchea
`app.tools.executor.get_signal_stats` con un `AsyncMock` y comprueba que
`execute_tool` lo despacha con los parámetros correctos — exactamente el
patrón de `test_valid_call_dispatches_to_the_right_tool_function`
(`tests/test_tool_executor.py:36-50`). Si tienes Ollama corriendo, puedes
además probarlo de verdad con el flujo manual de
`02-probar-uns-copilot.md`, preguntando algo como *"¿cuál fue el máximo de
Gen_RPM_Avg en la última hora?"*.

---

## 9. Errores típicos al programar esto (checklist)

Basado en las decisiones que este proyecto tomó explícitamente para
evitarlos:

- ☐ **No perder el `tool_call_id`.** Si el resultado que le mandas de
  vuelta al modelo no lleva el mismo `id` que él generó en su petición, el
  modelo no puede correlacionar (sobre todo si pidió varias tools a la
  vez) y la conversación se corrompe.
- ☐ **No dejar que una excepción de una tool tumbe el turno.** Captúrala,
  conviértela en `{"error": ...}`, devuélvesela al modelo como resultado.
  Es una llamada de función, no una llamada de sistema que puede fallar
  "hacia arriba".
- ☐ **No confiar en los tipos que promete el JSON Schema.** El schema es
  documentación para el modelo, no un `parser`. Valida siempre con algo
  real (Pydantic aquí) antes de tocar una base de datos.
- ☐ **No dejar el bucle sin cota.** Un `for _ in range(MAX_ITERATIONS)`,
  no un `while True` — un modelo puede quedarse pidiendo tools sin llegar
  nunca a una respuesta final.
- ☐ **No serializar tipos "crudos" de la base de datos.** `Decimal` y
  `datetime` rompen `json.dumps` — normalízalos a `float`/ISO-8601 *antes*
  de construir el resultado, no como parche al serializar.
- ☐ **No dejar sin rollback una sesión de DB compartida tras un fallo.**
  Si varias tool calls comparten sesión/transacción en el mismo turno, un
  fallo sin rollback envenena las siguientes.
- ☐ **No hornear datos que cambian (fecha/hora, identidad) en el
  historial persistido** — recalcúlalos en cada turno, en el mensaje de
  sistema.

---

## 10. Glosario

| Término | Qué significa aquí |
|---|---|
| **Tool / function** | Una función que tu backend expone al modelo, descrita como JSON Schema. En este repo: `get_catalog`, `get_latest_value`, `query_readings`, `list_events`. |
| **Tool call** | La petición del modelo para ejecutar una tool concreta con unos argumentos concretos (`ToolCall` en `llm/base.py`). |
| **Tool result / tool message** | El mensaje `role: "tool"` que le devuelves al modelo con el resultado (o error) de ejecutar la tool, correlacionado por `tool_call_id`. |
| **System message / system prompt** | El primer mensaje de la conversación, que fija el rol del asistente y contexto que cambia con el tiempo (aquí, la hora actual). No es una tool, pero condiciona igual de fuerte el comportamiento del modelo. |
| **Agentic loop** | El `for` que alterna "preguntar al modelo" ↔ "ejecutar lo que pida" hasta que ya no pide más (`run_turn`). |
| **`finish_reason`** | Por qué el modelo paró de generar en esa respuesta: `"tool_calls"` (quiere ejecutar algo), `"stop"` (respuesta final), `"length"` (se cortó por límite de tokens), etc. |
| **Guardrail** | Un límite que tú impones y el modelo no puede saltarse (límite de filas, rango máximo de tiempo, solo lectura) — la diferencia entre "el modelo decide qué preguntar" y "el modelo decide qué se le permite hacer". |
| **Dispatch table (tabla de despacho)** | El `dict` nombre→función/modelo (`_PARAM_MODELS` en `executor.py`) que convierte un `name` en texto en la función Python real a ejecutar, sin un `if/elif` gigante ni `eval`. |
| **Adapter (patrón)** | La clase que traduce el formato específico de un proveedor (aquí, `OllamaAdapter`) al formato neutral que usa el resto de tu programa (`ChatResponse`/`ToolCall`). |

---

## 11. Para seguir

- El diseño completo, con las decisiones y sus porqués:
  `UNS_COPILOT/docs/superpowers/specs/2026-09-06-uns-copilot-design.md`.
- Probar todo esto en vivo, con Ollama de verdad: `02-probar-uns-copilot.md`.
- Los tests son la mejor documentación ejecutable de los casos límite:
  `UNS_COPILOT/backend/tests/test_agent_loop.py` (el bucle) y
  `test_tool_executor.py` (la validación/despacho) — léelos como
  especificación, no solo como suite de regresión.
- Ideas para seguir practicando *tool use* sobre esta misma base (RAG,
  salida estructurada, agentes que escriben con confirmación humana, MCP):
  `manual/ideas.md`.
