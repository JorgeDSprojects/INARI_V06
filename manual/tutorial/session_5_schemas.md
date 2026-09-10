# Sesión 5 — Schemas y validación con Pydantic

En esta sesión nos enfocamos en los **schemas** (esquemas) que usan **Pydantic** para:
1. Serializar y deserializar datos entre la API HTTP y los modelos ORM.
2. Validar los parámetros que llegan a cada *tool* (tool‑calling).
3. Garantizar que nunca se construyan consultas SQL dinámicas.

---

## 1️⃣ Principios de Pydantic en este proyecto
| Principio | Implementación |
|-----------|----------------|
| **`orm_mode = True`** | Permite que FastAPI convierta directamente instancias SQLAlchemy a los schemas Pydantic sin crear un diccionario intermedio. |
| **Validación de entrada** | Cada endpoint declara un *request schema* (p.e. `MessageCreate`). FastAPI valida el cuerpo JSON contra ese modelo antes de entrar al código. |
| **Validación de herramientas** | Cada herramienta define su propio `Params` (subclase de `BaseModel`). Cuando el agente llama a una herramienta, los argumentos del LLM se convierten a esa clase; cualquier campo faltante o de tipo erróneo lanza `ValidationError` que se captura y se devuelve como `{"error": …}` al modelo. |
| **Serialización consistente** | Todos los modelos de respuesta (`UserRead`, `ConversationRead`, `MessageRead`) usan `BaseModel` y heredan `Config.orm_mode = True`. Las respuestas son siempre JSON con snake_case (p. ej. `created_at`). |

---

## 2️⃣ Schemas de la API (carpeta `backend/app/schemas`)
A continuación se listan los archivos más relevantes con ejemplos abreviados.

### 2.1 `user.py`
```python
from pydantic import BaseModel
from datetime import datetime

class UserRead(BaseModel):
    id: int
    display_name: str
    created_at: datetime

    class Config:
        orm_mode = True
```
- Solo **lectura** (no hay `UserCreate` porque la aplicación no permite crear usuarios por ahora). 
- `created_at` se deserializa a `datetime` automáticamente.

### 2.2 `conversation.py`
```python
from pydantic import BaseModel
from datetime import datetime

class ConversationCreate(BaseModel):
    user_id: int

class ConversationRead(BaseModel):
    id: int
    user_id: int
    title: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
```
- `ConversationCreate` valida que el cuerpo contenga **solo** `user_id`. 
- `ConversationRead` se usa para respuestas y, gracias a `orm_mode`, FastAPI puede devolver la instancia ORM directamente.

### 2.3 `message.py`
```python
from pydantic import BaseModel
from datetime import datetime

class MessageCreate(BaseModel):
    text: str

class MessageRead(BaseModel):
    id: int
    role: str               # 'user' | 'assistant' | 'tool'
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    created_at: datetime

    class Config:
        orm_mode = True
```
- `MessageCreate` se usa únicamente en el endpoint `/conversations/{id}/messages`. El campo se llama `text` para que sea más intuitivo en la API (el modelo interno lo guarda como `content`).
- `MessageRead` expone toda la información del historial, incluyendo `tool_calls` (lista de dicts) y `tool_call_id`.

---

## 3️⃣ Schemas de herramientas (carpeta `backend/app/tools`)
Cada herramienta tiene su propio módulo con dos piezas clave:
1. **`Params`** – subclase de `BaseModel` que declara los parámetros esperados.
2. **`execute(db, params)`** – función que recibe la sesión SQLAlchemy y una instancia de `Params`.

### 3.1 `catalog.py`
```python
from pydantic import BaseModel, Field
from typing import Literal

class Params(BaseModel):
    topic_filter: str | None = Field(default=None, description="Prefix to filter topics, e.g. 'energy'")
    signal_type: Literal["raw", "kpi"] | None = Field(default=None, description="Filter by raw sensor or KPI")
```
- `Literal` limita el valor a los strings permitidos.
- `Field` permite añadir descripción útil que aparecerá en la documentación OpenAPI.

### 3.2 `latest_value.py`
```python
from pydantic import BaseModel, Field

class Params(BaseModel):
    topic: str = Field(..., description="Topic name, e.g. 'temperature'")
    signal_key: str = Field(..., description="Key of the signal within the topic, e.g. 'room1'")
    unit: str | None = Field(default=None, description="Optional unit of measurement to include in the response")
```
- Hemos añadido el campo `unit` (ver ejercicio de la sesión 4). 
- Si `unit` está presente, `execute` lo incluye en el JSON devuelto.

### 3.3 `query_readings.py`
```python
from pydantic import BaseModel, Field, validator
from datetime import datetime

class Params(BaseModel):
    topic: str = Field(...)
    signal_key: str = Field(...)
    from_time: datetime = Field(..., description="ISO‑8601 datetime start")
    to_time: datetime = Field(..., description="ISO‑8601 datetime end")
    agg: Literal["raw", "1m", "1h"] = Field(default="raw")

    @validator("to_time")
    def check_range(cls, v, values):
        if "from_time" in values and v <= values["from_time"]:
            raise ValueError("to_time must be after from_time")
        return v
```
- El **validator** asegura que el rango temporal sea lógico y captura errores antes de ejecutar cualquier SQL.

### 3.4 `list_events.py`
```python
from pydantic import BaseModel, Field
from datetime import datetime

class Params(BaseModel):
    topic_filter: str | None = Field(default=None)
    event_key: str | None = Field(default=None)
    from_time: datetime = Field(...)
    to_time: datetime = Field(...)
```
- No hay validación extra; la lógica de filtrado se aplica en la consulta SQL.

---

## 4️⃣ Registro central de herramientas (`app/tools/__init__.py`)
```python
from .catalog import Params as CatalogParams, execute as catalog_exec
from .latest_value import Params as LatestParams, execute as latest_exec
from .query_readings import Params as QueryParams, execute as query_exec
from .list_events import Params as ListParams, execute as list_exec

TOOL_DEFINITIONS = [
    {
        "name": "get_catalog",
        "description": "List cataloged signals/KPIs.",
        "parameters": CatalogParams.schema(),
        "func": catalog_exec,
    },
    {
        "name": "get_latest_value",
        "description": "Current value of a specific signal.",
        "parameters": LatestParams.schema(),
        "func": latest_exec,
    },
    {
        "name": "query_readings",
        "description": "Historical time‑series for a signal.",
        "parameters": QueryParams.schema(),
        "func": query_exec,
    },
    {
        "name": "list_events",
        "description": "Discrete events for a topic.",
        "parameters": ListParams.schema(),
        "func": list_exec,
    },
]
```
- `CatalogParams.schema()` devuelve un **JSON Schema** que OpenAI‑compatible necesita para la llamada de herramientas.
- Cada definición incluye `func` que es la función real a ejecutar; el bucle agente la llama dinámicamente.

---

## 5️⃣ Cómo el agente utiliza los schemas
En `app/agent.py` (bucle `run_turn`), después de recibir la respuesta del LLM:
```python
for call in response.tool_calls:
    # 1️⃣ localizar la definición
    tool_def = next(t for t in TOOL_DEFINITIONS if t["name"] == call.name)
    # 2️⃣ validar con Pydantic
    ParamsCls = tool_def["func"].__globals__["Params"]  # schema class
    # Convertir dict de args a modelo (esto lanza ValidationError si algo falla)
    params = ParamsCls(**call.arguments)
    # 3️⃣ ejecutar
    result = tool_def["func"](db, params)
```
- Si **`ValidationError`** ocurre, el bloque `except` (no mostrado aquí) captura el error y devuelve `{"error": "..."}` como resultado de la herramienta, permitiendo al modelo re‑intentar.
- Este paso garantiza que **nadie** pueda inyectar SQL arbitraria, porque los parámetros están tipados y validados antes de pasar a la capa de consulta.

---

## 6️⃣ Validación de la API con pruebas
Los tests bajo `backend/tests/` usan `client.post(..., json={...})` y esperan que FastAPI devuelva **422 Unprocessable Entity** cuando la carga no cumple el schema.
Ejemplo (simplificado):
```python
def test_create_conversation_invalid_user(client):
    response = client.post("/conversations/", json={"user_id": "not-an-int"})
    assert response.status_code == 422
    # FastAPI incluye detalle del campo que falló
    assert "value is not a valid integer" in response.text
```
Esto asegura que el *frontend* (o cualquier consumidor) reciba errores claros antes de que el request llegue a la base de datos.

---

## 7️⃣ **Ejercicio práctico** (antes de la siguiente sesión)
1. **Añade una nueva herramienta** llamada `get_user_info` que devuelva el `display_name` y la fecha de creación de un usuario dado su `user_id`.
   - Crea el archivo `backend/app/tools/get_user_info.py` con:
     ```python
     from pydantic import BaseModel, Field
     from sqlalchemy.orm import Session
     from ..models import user as user_model

     class Params(BaseModel):
         user_id: int = Field(..., description="ID del usuario a buscar")

     def execute(db: Session, params: Params):
         u = db.get(user_model.User, params.user_id)
         if not u:
             return {"error": "User not found"}
         return {"id": u.id, "display_name": u.display_name, "created_at": u.created_at.isoformat()}
     ```
   - Registra la herramienta en `backend/app/tools/__init__.py` añadiendo una nueva entrada a `TOOL_DEFINITIONS` (nombre `get_user_info`).
2. **Reinicia** el backend (`.\UNS_COPILOT\scripts\restart.sh`).
3. **Prueba la herramienta** mediante una conversación:
   ```powershell
   # 1) crear conversación para user 1
   curl -X POST http://localhost:8002/conversations/ -H "Content-Type: application/json" -d "{\"user_id\":1}"
   # 2) enviar mensaje que invoque la herramienta
   curl -X POST http://localhost:8002/conversations/1/messages \
        -H "Content-Type: application/json" \
        -d "{\"text\":\"¿Cuál es la información del usuario con id 2?\"}"
   ```
   El modelo debería llamar a `get_user_info` y devolver algo como:
   ```json
   {"display_name":"bob","created_at":"2026-09-10T12:34:56.789Z"}
   ```
4. **Verifica en la base** que se haya guardado un mensaje `role='tool'` con el JSON del resultado:
   ```powershell
   docker exec -it uns_copilot_postgres psql -U copilot_user -d copilot_db -c "SELECT role, content FROM messages WHERE role='tool' ORDER BY created_at DESC LIMIT 1;"
   ```
5. (Opcional) **Añade pruebas** en `backend/tests/test_tools_integration.py` que:
   - Llamen directamente a `get_user_info.execute` con un `Session` de prueba y verifiquen la salida.
   - Simulen una conversación usando `FakeLLMProvider` que devuelva un `tool_call` a `get_user_info` y comprueben que `run_turn` persiste el mensaje de tipo `tool`.

---

## 8️⃣ Qué sigue
En la **Sesión 6** cubriremos **Testing** (unitario, integración, uso de `FakeLLMProvider`, fixtures de Pytest) y veremos cómo asegurar que el bucle agente se comporta correctamente.

---

**Resumen rápido**
- Pydantic garantiza que tanto la API pública como los parámetros de tool‑calling están **tipados y validados**. 
- Los schemas de la API (`UserRead`, `ConversationCreate`, `MessageCreate`, …) usan `orm_mode=True`. 
- Cada herramienta tiene su propio `Params` y `execute`, registrados en `TOOL_DEFINITIONS`. 
- El bucle agente convierte los `tool_calls` del modelo a instancias Pydantic; cualquier error de validación se envía al LLM como `{"error": …}`. 
- Añadir una herramienta sigue un patrón claro: definir `Params`, implementar `execute`, registrar en `__init__.py` y escribir tests.

Cuando finalices el ejercicio, avísame y pasaremos a la **Sesión 6 — Testing**.
