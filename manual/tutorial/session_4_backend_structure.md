# Sesión 4 — Estructura del backend Python

En esta sesión desglosamos el código fuente de **UNS Copilot** bajo `UNS_COPILOT/backend/app`. Veremos cómo están organizados los routers, los modelos ORM, la abstracción del LLM, las herramientas y el bucle agente.

---

## 1️⃣ Árbol de directorios y responsabilidades
```
backend/
 ├─ app/
 │   ├─ __init__.py               # crea la instancia FastAPI
 │   ├─ main.py                   # punto de entrada (uvicorn)
 │   ├─ routers/
 │   │   ├─ __init__.py
 │   │   ├─ users.py               # /users CRUD (solo lectura)
 │   │   ├─ conversations.py       # /conversations CRUD + creación
 │   │   └─ chat.py                # /conversations/{id}/messages (bucle agente)
 │   ├─ models/
 │   │   ├─ __init__.py
 │   │   ├─ user.py                # SQLAlchemy + Pydantic schema
 │   │   ├─ conversation.py
 │   │   └─ message.py
 │   ├─ llm/
 │   │   ├─ __init__.py
 │   │   ├─ base.py                # interfaz abstracta LLMProvider
 │   │   ├─ ollama_adapter.py      # implementación real (OpenAI SDK)
 │   │   └─ fake.py                # mock para tests
 │   ├─ tools/
 │   │   ├─ __init__.py            # registro de TOOLS
 │   │   ├─ catalog.py
 │   │   ├─ latest_value.py
 │   │   ├─ query_readings.py
 │   │   └─ list_events.py
 │   ├─ observability.py          # opcional Langfuse wrapper
 │   └─ agent.py                  # bucle run_turn (core del agente)
 └─ Dockerfile                    # python:3.12‑slim + dependencias
```
### Qué hace cada capa
| Capa | Responsabilidad |
|------|-----------------|
| **Routers** | Definen los endpoints HTTP de FastAPI. Cada router importa la dependencia `get_db` y llama a funciones del dominio (p.ej. `run_turn`). |
| **Models** | Contienen **SQLAlchemy ORM** (clases que mapean a tablas) y **Pydantic schemas** (`*Read`, `*Create`) usados por FastAPI. |
| **LLM** | Abstrae el proveedor de modelo. `LLMProvider.chat(messages, tools)` devuelve `ChatResponse`. `OllamaAdapter` implementa la interfaz usando el SDK `openai`. |
| **Tools** | Cada herramienta está en su propio módulo, define un `Params` (Pydantic) y una función `execute(db, params)`. `tools/__init__.py` registra todas en `TOOL_DEFINITIONS`. |
| **Agent** | `run_turn` orquesta el bucle: carga historial, llama al LLM, persiste mensajes, ejecuta tools, repite hasta respuesta final. |
| **Observability** | Si `LANGFUSE_ENABLED=true`, los decoradores `@observe` crean spans en Langfuse; si no, se ejecutan sin efecto. |

---

## 2️⃣ FastAPI – punto de entrada y dependencia de BD
### 2.1 `app/__init__.py`
```python
from fastapi import FastAPI
from .observability import init_langfuse

app = FastAPI(title="UNS Copilot", version="0.1.0")

# Inicializa Langfuse si está habilitado
init_langfuse(app)

# Importar routers (se añaden al app)
from .routers import users, conversations, chat  # noqa: F401
```
- Crea la instancia global `app` que `uvicorn` usará.
- `init_langfuse` registra un *middleware* opcional.
- Los routers se importan al final para evitar import cycles.

### 2.2 `app/main.py`
```python
import uvicorn
from . import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```
- Docker ejecuta: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

---

## 3️⃣ Gestión de la sesión de base de datos (SQLAlchemy)
### 3.1 `app/models/__init__.py`
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

DATABASE_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql://copilot_user:superSecret123@uns_copilot_postgres:5432/copilot_db",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
```
- `engine` apunta a la cadena de conexión del contenedor.
- `SessionLocal` es la *factory* de sesiones que usamos en los routers.

### 3.2 Dependency injection en FastAPI (`app/dependencies.py` implícito en routers)
```python
from fastapi import Depends
from .models import SessionLocal

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```
- Cada router incluye `db: Session = Depends(get_db)`.

---

## 4️⃣ Routers (endpoints)
### 4.1 `users.py`
```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..models import user as user_model

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/", response_model=list[user_model.UserRead])
def list_users(db: Session = Depends(get_db)):
    return db.query(user_model.User).all()
```
- Sólo lectura; no hay creación porque los usuarios son seedados.

### 4.2 `conversations.py`
```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..models import conversation as conv_model, user as user_model
from ..schemas import ConversationCreate, ConversationRead

router = APIRouter(prefix="/conversations", tags=["conversations"])

@router.post("/", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db)):
    # validar que el user exista
    if not db.get(user_model.User, payload.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    conv = conv_model.Conversation(user_id=payload.user_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv
```
- Persiste conversación y devuelve el registro recién creado.

### 4.3 `chat.py` (bucle agente)
```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..models import conversation as conv_model, message as msg_model
from ..schemas import MessageCreate, MessageRead
from ..agent import run_turn
from ..llm.base import LLMProvider
from ..dependencies import get_llm_provider

router = APIRouter(prefix="/conversations/{conv_id}/messages", tags=["chat"])

@router.post("/", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
def post_message(
    conv_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    llm_provider: LLMProvider = Depends(get_llm_provider),
):
    # validar conversación
    conv = db.get(conv_model.Conversation, conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # delegar al agente
    answer = run_turn(
        db=db,
        conversation_id=conv_id,
        user_text=payload.text,
        llm_provider=llm_provider,
    )
    # `run_turn` ya persiste los mensajes y devuelve el texto final
    return {"content": answer, "role": "assistant"}
```
- El agente maneja todo el flujo; el endpoint solo devuelve la respuesta final.

---

## 5️⃣ Modelos ORM + Pydantic schemas
### 5.1 `models/user.py`
```python
from .. import Base
from sqlalchemy import Column, BigInteger, Text, DateTime, func

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, index=True)
    display_name = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```
### 5.2 `schemas/user.py`
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
- **Patrón**: para cada tabla hay un modelo ORM y un schema Pydantic (`*Read`, `*Create` cuando corresponde). 
- `orm_mode=True` permite que FastAPI convierta automáticamente la instancia ORM a JSON.

---

## 6️⃣ LLM Provider abstraction
### 6.1 `llm/base.py`
```python
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import List, Dict, Any

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any]

class ChatResponse(BaseModel):
    content: str | None
    tool_calls: List[ToolCall]
    finish_reason: str

class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> ChatResponse:
        ...
```
- Define la **firma** que el resto del código usa; permite cambiar de Ollama a OpenAI con otro adapter sin tocar la lógica.

### 6.2 `ollama_adapter.py`
```python
import os
from openai import OpenAI
from .base import LLMProvider, ChatResponse, ToolCall

class OllamaAdapter(LLMProvider):
    def __init__(self):
        self.client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))
        self.model = os.getenv("LLM_MODEL")

    def chat(self, messages, tools):
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
        )
        choice = resp.choices[0]
        content = choice.message.content
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
                )
        return ChatResponse(content=content, tool_calls=tool_calls, finish_reason=choice.finish_reason)
```
- Usa el SDK OpenAI porque Ollama expone una API compatible.
- Devuelve un `ChatResponse` normalizado.

### 6.3 `fake.py` (para tests)
```python
from .base import LLMProvider, ChatResponse, ToolCall

class FakeLLMProvider(LLMProvider):
    def __init__(self, scripted_responses):
        self._responses = iter(scripted_responses)

    def chat(self, messages, tools):
        return next(self._responses)
```
- Permite pre‑definir una serie de respuestas y testear el bucle sin dependencias externas.

---

## 7️⃣ Herramientas (tool‑calling)
Cada herramienta sigue la misma **firma**: `Params` (Pydantic) + `execute(db, params) -> dict`.
### 7.1 Registro central (`tools/__init__.py`)
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
- `schema()` devuelve el **JSON Schema** que OpenAI‑compatible necesita para la llamada de herramientas.
- El bucle agente importa `TOOL_DEFINITIONS` y lo pasa al LLM.

---

## 8️⃣ Bucle agente (`agent.py`)
```python
import json, os
from sqlalchemy.orm import Session
from .llm.base import LLMProvider, ChatResponse
from .tools import TOOL_DEFINITIONS
from .models import message as msg_model
from .observability import observe

MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "5"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

def _load_history(db: Session, conv_id: int) -> list[dict]:
    msgs = (
        db.query(msg_model.Message)
        .filter(msg_model.Message.conversation_id == conv_id)
        .order_by(msg_model.Message.created_at)
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    history = []
    for m in msgs:
        entry = {"role": m.role}
        if m.content:
            entry["content"] = m.content
        if m.tool_calls:
            entry["tool_calls"] = json.loads(m.tool_calls)
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        history.append(entry)
    return history

@observe("run_turn")
def run_turn(db: Session, conversation_id: int, user_text: str, llm_provider: LLMProvider) -> str:
    # Persistir mensaje del usuario
    db.add(msg_model.Message(conversation_id=conversation_id, role="user", content=user_text))
    db.commit()

    # cargar historial + mensaje del usuario
    history = _load_history(db, conversation_id)
    history.append({"role": "user", "content": user_text})

    for _ in range(MAX_ITERATIONS):
        # construir schemas de tools para el modelo
        tool_schemas = [
            {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
            for t in TOOL_DEFINITIONS
        ]
        response: ChatResponse = llm_provider.chat(messages=history, tools=tool_schemas)

        # Persistir la respuesta del asistente (incluye tool_calls)
        db.add(msg_model.Message(
            conversation_id=conversation_id,
            role="assistant",
            content=response.content,
            tool_calls=json.dumps([tc.dict() for tc in response.tool_calls]) if response.tool_calls else None,
        ))
        db.commit()

        if not response.tool_calls:
            return response.content or ""

        # ejecutar cada tool
        for call in response.tool_calls:
            tool_def = next(t for t in TOOL_DEFINITIONS if t["name"] == call.name)
            ParamsCls = tool_def["func"].__globals__["Params"]
            params = ParamsCls(**call.arguments)   # ValidationError será capturada abajo
            result = tool_def["func"](db, params)

            # Persistir resultado como mensaje de tipo 'tool'
            db.add(msg_model.Message(
                conversation_id=conversation_id,
                role="tool",
                content=json.dumps(result),
                tool_call_id=call.id,
            ))
            db.commit()
            # añadir al historial para la siguiente iteración
            history.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})

    return "Could not complete the request after several attempts – please rephrase."
```
- **Observabilidad**: el decorador `@observe` crea un span en Langfuse (si está activo). 
- **Persistencia**: cada turno (`user`, `assistant`, `tool`) se guarda en la tabla `messages`. 
- **Validación**: los `Params` de cada herramienta son Pydantic – cualquier error de validación se captura y se devuelve al modelo como `{"error": …}`.

---

## 9️⃣ Observabilidad opcional (`observability.py`)
```python
from fastapi import FastAPI
from langfuse import Langfuse
import os

def init_langfuse(app: FastAPI):
    if os.getenv("LANGFUSE_ENABLED", "false").lower() != "true":
        return
    lf = Langfuse(
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        host=os.getenv("LANGFUSE_HOST"),
    )
    @app.middleware("http")
    async def add_lf_client(request, call_next):
        request.state.langfuse = lf
        return await call_next(request)

def observe(name: str):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            request = kwargs.get("request")
            lf = getattr(request.state, "langfuse", None) if request else None
            if lf:
                with lf.trace(name=name) as trace:
                    return await func(*args, **kwargs)
            else:
                return await func(*args, **kwargs)
        return wrapper
    return decorator
```
- Si `LANGFUSE_ENABLED` es `false`, los decoradores se comportan como *no‑ops*.
- Así la aplicación nunca falla por falta de Langfuse.

---

## 10️⃣ Ejercicio práctico (antes de la siguiente sesión)
1. **Abre el proyecto en tu editor** y navega a `UNS_COPILOT/backend/app`.
2. **Añade una nueva herramienta** `get_user_info` que devuelva `display_name` y `created_at` dado `user_id`.
   - Crea el archivo `tools/get_user_info.py` con:
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
   - Registra la herramienta en `tools/__init__.py` añadiendo una nueva entrada a `TOOL_DEFINITIONS` (nombre `get_user_info`).
3. **Reinicia** el backend (`.\UNS_COPILOT\scripts\restart.sh`).
4. **Prueba la herramienta** mediante una conversación:
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
5. **Verifica en la base** que se haya guardado un mensaje `role='tool'` con el JSON del resultado:
   ```powershell
   docker exec -it uns_copilot_postgres psql -U copilot_user -d copilot_db -c "SELECT role, content FROM messages WHERE role='tool' ORDER BY created_at DESC LIMIT 1;"
   ```
6. (Opcional) **Añade pruebas** en `backend/tests/test_tools_integration.py` que:
   - Llamen directamente a `get_user_info.execute` con un `Session` de prueba y verifiquen la salida.
   - Simulen una conversación usando `FakeLLMProvider` que devuelva un `tool_call` a `get_user_info` y comprueben que `run_turn` persiste el mensaje de tipo `tool`.

---

## 11️⃣ Qué sigue
En la **Sesión 7** cubriremos **Testing** (unitario, integración, uso de `FakeLLMProvider`, fixtures de Pytest) y veremos cómo asegurar que el bucle agente se comporta correctamente.

---

**Resumen rápido**
- FastAPI (`app/__init__.py`, `main.py`).
- Dependencia DB (`SessionLocal`, `get_db`).
- Routers: `users`, `conversations`, `chat`. 
- Modelo ORM + Pydantic schemas (`User`, `Conversation`, `Message`).
- LLM abstraction (`LLMProvider`, `OllamaAdapter`, `FakeLLMProvider`).
- Herramientas tipificadas y registradas en `TOOL_DEFINITIONS`. 
- Bucle agente (`run_turn`) persiste cada paso y valida con Pydantic. 
- Observabilidad opcional con Langfuse.

Cuando termines el ejercicio, avísame y pasamos a la **Sesión 7 — Testing**.
