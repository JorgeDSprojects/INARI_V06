# Fase 5 — Las herramientas: del contrato de la Fase 1 al código

Con la capa LLM verificada (Fase 4), toca construir las cuatro
herramientas que diseñaste en papel en la Fase 1. Esta fase tiene más
piezas que las anteriores porque cada tool cruza cuatro capas distintas
— y el orden en que las construyes dentro de la fase importa tanto como
el orden de las fases entre sí.

---

## 1. El orden dentro de la fase: de adentro hacia afuera, otra vez

Mismo principio que en el tutorial completo, aplicado ahora a escala de
una sola tool. Para cada herramienta hay cuatro capas, y se construyen en
este orden:

```
1. Validación (Pydantic)  →  2. Ejecución (SQL real)  →  3. Normalización (tipos)  →  4. Contrato (JSON Schema)
   "¿son válidos              "¿qué SQL corre              "¿cómo llega el              "¿qué ve el
    los parámetros?"           de verdad?"                  resultado al LLM?"          modelo?"
```

Por qué en ese orden y no, por ejemplo, empezando por el JSON Schema (que
es lo primero que "ve" el modelo): porque el JSON Schema es *documentación
para el modelo*, no código que se ejecuta — puedes escribirlo en el
último minuto sin que nada dependa de él para funcionar internamente. La
validación, en cambio, es lo primero de lo que depende todo lo demás: sin
ella, "ejecutar la query real" no tiene ninguna garantía de recibir datos
sensatos.

---

## 2. Capa 1 — Validación: `app/tools/params.py`

Cada tool tiene su propio modelo Pydantic — el mismo mapeo 1:1 de la
Fase 1 (una tool, un modelo de parámetros):

```python
from datetime import datetime, timedelta
from typing import Literal
from pydantic import BaseModel, model_validator

class _TimeRangeMixin(BaseModel):
    from_time: datetime
    to_time: datetime

    @model_validator(mode="after")
    def _check_time_order(self):
        if self.to_time <= self.from_time:
            raise ValueError(
                f"to_time ({self.to_time.isoformat()}) must be strictly after from_time ({self.from_time.isoformat()})"
            )
        return self

class GetCatalogParams(BaseModel):
    topic_filter: str | None = None
    signal_type: Literal["raw", "kpi"] | None = None

class GetLatestValueParams(BaseModel):
    topic: str
    signal_key: str

class QueryReadingsParams(_TimeRangeMixin):
    topic: str
    signal_key: str
    agg: Literal["raw", "1m", "1h"] = "raw"

class ListEventsParams(_TimeRangeMixin):
    topic_filter: str | None = None
    event_key: str | None = None

def check_raw_range(params: QueryReadingsParams, max_raw_range_hours: int) -> None:
    if params.agg != "raw":
        return
    span = params.to_time - params.from_time
    if span > timedelta(hours=max_raw_range_hours):
        raise ValueError(
            f"raw query range of {span} exceeds the {max_raw_range_hours}h limit for agg='raw'; "
            "use agg='1m' or agg='1h' for a wider window"
        )
```

Decisiones que construir tú mismo te obliga a razonar, no solo copiar:

- **`_TimeRangeMixin` es una clase compartida, no dos validaciones
  copiadas** en `QueryReadingsParams` y `ListEventsParams`. Ambas tools
  necesitan "rango temporal válido" — extraer el mixin en cuanto ves el
  segundo caso es la señal correcta de cuándo generalizar (no antes, con
  una sola tool, hubiera sido especulativo).
- **El validador de rango (`_check_time_order`) rechaza `to_time <=
  from_time` con `mode="after"`** — se ejecuta después de que Pydantic ya
  parseó ambos campos como `datetime`, así que puede compararlos
  directamente. Sin este validador, un rango invertido no rompe nada a
  nivel de tipos — simplemente el `BETWEEN` de SQL devuelve cero filas
  (verás el porqué exacto en la sección 3), y el modelo concluiría
  (incorrectamente) que no hay datos, en vez de que la pregunta estaba
  mal formada. Este es el primer guardrail "porque sí" que se te ocurre
  al construirlo: sin él, un fallo silencioso es peor que un error
  explícito.
- **`check_raw_range` está fuera de la clase Pydantic, como función
  aparte**, aunque también es "validación". La razón: necesita
  `max_raw_range_hours`, que es un parámetro de *configuración*
  (`settings.raw_query_max_range_hours`, Fase 2), no algo que el modelo
  mande como argumento de la tool. Meterlo dentro del modelo Pydantic
  significaría tener que pasarle la configuración en cada construcción
  del objeto — más acoplado de lo necesario. Separarlo dice: "esto no es
  parte de la forma de los datos, es una política operativa que se aplica
  después".

**Checkpoint parcial — solo esta capa, sin tocar base de datos:**

```bash
cd UNS_COPILOT/backend
pytest tests/test_tool_params.py -v
```

**Salida esperada:** en verde. Fíjate en que estos tests no necesitan
`DATABASE_URL` ni `SILVER_DATABASE_URL` — ninguno tiene el
`pytest.mark.skipif` que viste en la Fase 3, porque Pydantic no toca
ninguna base de datos. Puedes (y debes) verificar la validación
completamente aislada antes de escribir una sola consulta SQL.

---

## 3. Capa 2 — Ejecución: consultas parametrizadas contra `uns_silver_postgres`

Con los parámetros ya garantizados válidos, la función de ejecución de
cada tool es SQL directo — pero **siempre** parametrizado:

```python
# app/tools/catalog.py
from sqlalchemy import text

async def get_catalog(session, params: GetCatalogParams, row_limit: int) -> list[dict]:
    query = (
        "SELECT topic, signal_key, signal_type, unit, description, range_min, range_max, thresholds "
        "FROM signal_catalog WHERE effective_until IS NULL"
    )
    bind: dict = {"row_limit": row_limit}
    if params.topic_filter:
        query += " AND topic LIKE :topic_prefix"
        bind["topic_prefix"] = f"{params.topic_filter}%"
    if params.signal_type:
        query += " AND signal_type = :signal_type"
        bind["signal_type"] = params.signal_type
    query += " ORDER BY topic, signal_key LIMIT :row_limit"

    result = await session.execute(text(query), bind)
    return [row_to_json_safe(row) for row in result.mappings()]
```

Dos disciplinas que hay que mantener en **todas** las tools, sin
excepción, porque una sola grieta aquí anula la garantía de seguridad de
toda la Fase 1:

1. **Nunca interpolar un valor del usuario/modelo directamente en la
   cadena SQL** (ni con f-strings, ni con `.format()`, ni con `%`). Fíjate
   en `query += " AND topic LIKE :topic_prefix"` — el *placeholder*
   `:topic_prefix` es literal en la cadena SQL; el valor real
   (`params.topic_filter`) va aparte, en el diccionario `bind`, que
   `session.execute(text(query), bind)` sustituye de forma segura a nivel
   de driver. Es la diferencia exacta entre "consulta parametrizada" (a
   salvo de inyección) y "SQL libre con extra pasos" (que sería tan
   inseguro como lo que la Fase 1 descartó).
2. **`WHERE effective_until IS NULL`** — no es un detalle de esta tool,
   es cómo funciona el catálogo versionado de `UNS_SILVER`: cambiar la
   unidad o los umbrales de una señal cierra la versión antigua en vez de
   sobreescribirla (ver `CLAUDE.md`). Sin este filtro, `get_catalog`
   devolvería también versiones históricas ya cerradas — sería
   técnicamente correcto y prácticamente inútil para el modelo.

`get_latest_value` y `query_readings` viven juntas en
`app/tools/readings.py` (no en dos archivos separados) porque ambas
resuelven la misma familia de recurso — el valor de una señal, puntual o
en rango — y comparten una tabla de mapeo:

```python
_AGG_TABLES = {"raw": "silver_readings", "1m": "silver_readings_1m", "1h": "silver_readings_1h"}
_AGG_TIME_COLUMN = {"raw": "time", "1m": "bucket", "1h": "bucket"}

async def query_readings(session, params: QueryReadingsParams, row_limit: int, max_raw_range_hours: int) -> list[dict]:
    check_raw_range(params, max_raw_range_hours)   # el guardrail de la sección 2, aplicado justo antes de tocar la DB
    table = _AGG_TABLES[params.agg]
    time_column = _AGG_TIME_COLUMN[params.agg]
    select_cols = (
        f"{time_column} AS time, value_numeric, value_text" if params.agg == "raw"
        else f"{time_column} AS time, avg_value, min_value, max_value, sample_count"
    )
    result = await session.execute(
        text(f"SELECT {select_cols} FROM {table} WHERE topic = :topic AND signal_key = :signal_key "
             f"AND {time_column} BETWEEN :from_time AND :to_time ORDER BY {time_column} LIMIT :row_limit"),
        {"topic": params.topic, "signal_key": params.signal_key,
         "from_time": params.from_time, "to_time": params.to_time, "row_limit": row_limit},
    )
    return [row_to_json_safe(row) for row in result.mappings()]
```

El nombre de tabla (`table`) y de columna (`time_column`) sí se
interpolan directamente en el f-string — y esto **no** contradice la
regla de la disciplina 1: `table`/`time_column` nunca vienen de
`params.agg` sin pasar antes por los diccionarios `_AGG_TABLES`/
`_AGG_TIME_COLUMN`, que son un mapeo cerrado controlado por ti, no texto
libre del modelo. El propio `Literal["raw", "1m", "1h"]` de
`QueryReadingsParams` (sección 2) ya garantiza que `params.agg` solo
puede ser una de esas tres cadenas exactas — así que el `dict[params.agg]`
solo puede resolver a una de las tres tablas que tú escribiste. Es la
misma disciplina, aplicada con una herramienta distinta (un mapeo cerrado
en vez de un placeholder) porque el nombre de una tabla no se puede
parametrizar con `:bind` en SQL — los placeholders son para *valores*, no
para identificadores.

---

## 4. Capa 3 — Normalización: `app/tools/normalize.py`

Aquí es donde se descubre, al construirlo tú mismo, un problema que no
es obvio hasta que revienta en producción: `json.dumps` no sabe
serializar lo que Postgres/asyncpg devuelve para `TIMESTAMPTZ` (un
`datetime` de Python) ni para `NUMERIC` (un `Decimal`) — y el resultado
de cada tool se serializa con `json.dumps` antes de mandárselo al modelo
(lo verás en la Fase 6). Si construyes la capa 2 y la 4 sin esta capa
intermedia, el primer intento real de llamar a `get_latest_value` lanza
`TypeError: Object of type Decimal is not JSON serializable` en cuanto
llega un valor numérico de verdad.

```python
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

def to_json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {k: to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    return value

def row_to_json_safe(row: Mapping) -> dict:
    return {key: to_json_safe(value) for key, value in row.items()}
```

La decisión que no es obvia: **convertir `Decimal` a `float`, no a
`str`.** La alternativa perezosa sería `json.dumps(result, default=str)`
— funciona, pero un número serializado como texto (`"22.0"`) llega al
modelo como algo que tiene que *interpretar*, mientras que un número JSON
real (`22.0`) es algo con lo que puede *razonar aritméticamente*
(comparar, promediar, decir "subió 2 grados") directamente. Es una
decisión de calidad de respuesta, no solo de "que no crashee" — y es
exactamente el tipo de detalle que solo se te ocurre cuando construyes
esta capa *sabiendo para qué la necesita el modelo* aguas abajo, no como
un ejercicio aislado de serialización.

---

## 5. Capa 4 — El contrato: `app/tools/schemas.py`

Con las tres capas anteriores funcionando, el JSON Schema que ve el
modelo es casi mecánico de escribir — es una traducción directa de los
modelos Pydantic de la sección 2, pero en el formato que el wire protocol
de function calling exige:

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
                    "topic_filter": {"type": "string", "description": "ISA-95 topic prefix to filter by, e.g. 'plant1.line3'. Omit to list everything."},
                    "signal_type": {"type": "string", "enum": ["raw", "kpi"], "description": "Restrict to raw physical signals or computed KPIs. Omit for both."},
                },
                "required": [],
            },
        },
    },
    # ... get_latest_value, query_readings, list_events — mismo patrón
]
```

Dos cosas que solo aprecias al escribirlo tú mismo, no al leerlo ya
hecho:

- **No hay ninguna herramienta que genere este JSON automáticamente desde
  los modelos Pydantic** (aunque Pydantic sabe exportar su propio JSON
  Schema con `.model_json_schema()`). Es una elección consciente en este
  proyecto: la `description` de cada campo aquí está escrita *para guiar
  al modelo a elegir bien* (ver la sección 2 del tutorial de lectura de
  código), no solo para documentar el tipo — un `.model_json_schema()`
  automático te daría los tipos correctos pero descripciones genéricas o
  ausentes, perdiendo exactamente la parte que más afecta a si el modelo
  acierta la tool correcta.
- **`required` puede estar vacío** (`get_catalog`) mientras que en
  `GetCatalogParams` (Pydantic) ningún campo es literalmente obligatorio
  tampoco — coherencia intencionada: lo que el JSON Schema le "promete"
  al modelo sobre qué es opcional tiene que coincidir exactamente con lo
  que la validación Pydantic de verdad exige, o el modelo omitirá un
  campo que luego la validación rechaza como requerido (o al revés,
  mandará uno "requerido" que en realidad no hacía falta).

---

## 6. El despachador: `app/tools/executor.py`

La pieza que conecta las cuatro capas anteriores en una sola función,
aplicando el principio ya visto en la Fase 4: nunca confíes en lo que
manda el modelo, en ningún punto.

```python
_PARAM_MODELS = {
    "get_catalog": GetCatalogParams,
    "get_latest_value": GetLatestValueParams,
    "query_readings": QueryReadingsParams,
    "list_events": ListEventsParams,
}

async def execute_tool(session, name: str, arguments: dict, *, row_limit: int, max_raw_range_hours: int) -> dict:
    param_model = _PARAM_MODELS.get(name)
    if param_model is None:
        return {"error": f"Unknown tool: {name!r}"}                       # ① nombre desconocido

    try:
        params = param_model(**arguments)
    except (ValidationError, TypeError) as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}          # ② argumentos inválidos

    try:
        if name == "get_catalog":
            result = await get_catalog(session, params, row_limit=row_limit)
        elif name == "get_latest_value":
            result = await get_latest_value(session, params)
        elif name == "query_readings":
            result = await query_readings(session, params, row_limit=row_limit, max_raw_range_hours=max_raw_range_hours)
        elif name == "list_events":
            result = await list_events(session, params, row_limit=row_limit)
    except Exception as exc:                                             # ③ fallo real de ejecución
        if session is not None:
            await session.rollback()
        return {"error": str(exc)}

    return {"result": result}
```

Construir esto tú mismo te obliga a decidir, en orden, tres cosas que ya
viste explicadas en el tutorial de lectura de código (sección 5) — aquí
la pregunta es *cuándo las decides*, no solo qué son:

1. **`_PARAM_MODELS` como tabla de despacho** se decide en cuanto
   escribes la segunda tool — con una sola tool, un `if` bastaría; con
   cuatro, un diccionario nombre→modelo es lo que evita un `if/elif`
   gigante y, más importante, es lo que te da gratis el caso ①: un nombre
   que no está en el diccionario es, automáticamente, "tool desconocida".
2. **El `try/except` alrededor de la construcción de `params` (②)** es
   donde de verdad se cierra el círculo de seguridad: aquí es donde un
   `ValidationError` de la Capa 1 (sección 2) — incluida la del
   `check_raw_range`, si la llamas dentro de la función de ejecución como
   en `query_readings` — se convierte en algo que el modelo puede leer y
   corregir, en vez de un stack trace que tumba la petición HTTP entera.
3. **El `except Exception` genérico (③), con `rollback()`.** Aquí es
   donde se aprende, construyéndolo, un detalle que no es obvio la
   primera vez: si varias tool calls comparten la misma sesión de base de
   datos dentro de un mismo turno (verás esto en la Fase 6) y una falla a
   mitad de una operación, la sesión queda en un estado de transacción
   abortada — **cualquier** consulta posterior en esa misma sesión falla
   también, aunque sea una tool completamente distinta y correcta. El
   `rollback()` es lo que le devuelve a la sesión un estado utilizable
   para la siguiente llamada. Es fácil no descubrir esto hasta que
   escribes un test que encadena dos tool calls, una que falla y otra que
   no — exactamente lo que hace `test_a_failing_tool_rolls_the_session_back`.

---

## 7. Checkpoint de esta fase

**Primero, sin base de datos — el despachador con mocks:**

```bash
cd UNS_COPILOT/backend
pytest tests/test_tool_executor.py tests/test_tool_params.py -v
```

**Salida esperada:** en verde. Estos tests parchean las funciones de
ejecución (`app.tools.executor.get_latest_value`, etc.) con
`AsyncMock`, así que verifican el despachador — nombre desconocido,
argumentos inválidos, rollback tras fallo — sin tocar Postgres en
absoluto.

**Después, contra `uns_silver_postgres` real** — necesitas `UNS_SILVER`
levantado con algo de datos (aunque sea sembrados a mano para el test):

```bash
cd ../../UNS_SILVER && docker compose up -d && cd ../UNS_COPILOT/backend
SILVER_DATABASE_URL=postgresql+asyncpg://silver:silverpassword@localhost:5436/uns_silver \
  pytest tests/test_tools_integration.py -v
```

**Salida esperada:** en verde — este test inserta filas de prueba
directamente en `signal_catalog`/`silver_readings`/`silver_events` (con
`Decimal` en `range_min`/`range_max`, a propósito, para forzar el camino
de normalización de la sección 4) y comprueba que cada función de
ejecución real devuelve exactamente lo esperado, ya como tipos
JSON-nativos.

Si `test_tools_integration.py` falla con un `TypeError` mencionando
`Decimal` o `datetime`, es la señal más directa de que algo en la Capa 3
está devolviendo filas crudas sin pasar por `row_to_json_safe` — vuelve a
la sección 4.

---

## 8. Qué sigue

Con las cuatro tools completas y verificadas en aislamiento — validación,
ejecución, normalización y contrato, cada una probada por separado — la
Fase 6 las conecta con la capa LLM de la Fase 4 en el **bucle agente**:
la pieza que de verdad decide, turno a turno, cuándo llamar a una tool y
cuándo ya puede responder.
