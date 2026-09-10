# Fase 7 — La capa HTTP: exponer todo lo construido

Con el bucle agente verificado (Fase 6), esta es la última capa de
código de negocio: FastAPI, routers, esquemas de request/response, y el
`main.py` completo. Es deliberadamente la penúltima fase, no la primera
— todo lo que expone ya está construido y probado; aquí solo se conecta.

---

## 1. Por qué esta capa se deja para el final

Si hubieras empezado por aquí (el error de "fuera hacia dentro" que
avisa `overview.md`), cada endpoint que escribieras estaría acoplado a
implementaciones concretas — `OllamaAdapter` en vez de `LLMProvider`,
consultas directas en vez de tools ya validadas — y solo podrías probarlo
con Postgres y Ollama reales corriendo, en cada iteración. Al dejarlo
para el final, cada router es casi mecánico: recibe una petición, valida
con Pydantic, delega en algo que ya existe y ya está probado
(`run_turn`, `execute_tool`, los modelos SQLAlchemy), y devuelve una
respuesta. La capa HTTP no tiene lógica de negocio propia — y eso es
intencionado.

---

## 2. Los esquemas HTTP: `app/schemas/chat.py`

Antes de los routers, los contratos de entrada/salida — **distintos** de
los modelos SQLAlchemy (Fase 3) y de los parámetros de tools (Fase 5),
aunque compartan nombres de campos:

```python
from pydantic import BaseModel, ConfigDict

class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class UserRead(_Base):
    id: int
    display_name: str
    created_at: datetime

class ConversationCreate(BaseModel):
    user_id: int
    title: str | None = None

class ConversationRead(_Base):
    id: int
    user_id: int
    title: str | None
    created_at: datetime
    updated_at: datetime

class MessageRead(_Base):
    id: int
    conversation_id: int
    role: str
    content: str | None
    tool_calls: list[dict] | None
    tool_call_id: str | None
    created_at: datetime

class ConversationDetailRead(ConversationRead):
    messages: list[MessageRead]

class ChatMessageCreate(BaseModel):
    text: str

class ChatMessageResponse(BaseModel):
    conversation_id: int
    reply: str
```

Por qué existen estos y no reusar directamente los modelos SQLAlchemy de
la Fase 3 como respuesta HTTP:

- **`from_attributes=True`** (en `_Base`) es lo que permite que FastAPI
  devuelva directamente un objeto `Conversation` de SQLAlchemy y Pydantic
  lo convierta a `ConversationRead` leyendo sus atributos — sin construir
  un diccionario a mano en cada endpoint. Pero necesitas la clase
  Pydantic aparte igualmente, porque **controla qué se expone**: el
  modelo SQLAlchemy podría ganar columnas internas en el futuro que no
  quieres que salgan por la API tal cual.
- **`ConversationDetailRead` hereda de `ConversationRead` y añade
  `messages`** — dos formas de "leer una conversación" (lista resumida
  vs. detalle completo con todos los mensajes) sin duplicar los cuatro
  campos comunes. La herencia aquí expresa exactamente la relación real:
  el detalle es la lista *más* algo, no algo distinto.
- **`ChatMessageCreate`/`ChatMessageResponse` son minúsculos a propósito**
  (`text` de entrada, `reply` de salida) — el cliente HTTP nunca ve
  `tool_calls`, `role`, ni nada del mecanismo interno del bucle agente en
  la respuesta directa; para ver el detalle completo (lo que sí interesa
  para depurar, o para el manual de pruebas) está el endpoint separado
  `GET /conversations/{id}` con `ConversationDetailRead`.

---

## 3. El router más simple primero: `app/routers/users.py`

Construir el más simple primero confirma que el patrón "router → sesión
→ query → schema" funciona antes de complicarlo con lógica:

```python
from fastapi import APIRouter, Depends
from sqlalchemy import select
from app.database import get_db
from app.models.chat import User
from app.schemas.chat import UserRead

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/", response_model=list[UserRead])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.display_name))
    return result.scalars().all()
```

Un único endpoint, solo lectura — coherente con la decisión de la Fase 1
(sección 6): no hay `POST /users` porque no hay registro de usuarios en
v1, solo el selector sembrado por `seed_default_users` (Fase 3).
`Depends(get_db)` es la primera vez que usas en un endpoint real la
inyección de dependencias que ya preparaste en la Fase 3 — cada petición
recibe su propia sesión, cerrada automáticamente al terminar.

**Checkpoint parcial** (antes de seguir con el siguiente router):

```bash
curl http://localhost:8002/users/
```

(una vez wireado en `main.py`, sección 6) — debe devolver los usuarios
sembrados.

---

## 4. `app/routers/conversations.py`: el primer router con lógica propia

```python
router = APIRouter(prefix="/conversations", tags=["conversations"])

@router.post("/", response_model=ConversationRead, status_code=201)
async def create_conversation(body: ConversationCreate, db: AsyncSession = Depends(get_db)):
    if not await db.get(User, body.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    conversation = Conversation(user_id=body.user_id, title=body.title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation

@router.get("/", response_model=list[ConversationRead])
async def list_conversations(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()

@router.get("/{conversation_id}", response_model=ConversationDetailRead)
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = (await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    )).scalars().all()
    return ConversationDetailRead(
        id=conversation.id, user_id=conversation.user_id, title=conversation.title,
        created_at=conversation.created_at, updated_at=conversation.updated_at, messages=messages,
    )
```

El detalle que solo se te ocurre al escribirlo, no al leerlo: **la
comprobación `if not await db.get(User, body.user_id): raise
HTTPException(404, ...)`.** Sin ella, crear una conversación con un
`user_id` que no existe no falla en Python — falla en Postgres, como una
violación de la foreign key `conversations.user_id → users.id`, que
SQLAlchemy propaga como una excepción no controlada → FastAPI la
convierte en un `500 Internal Server Error` genérico. Un `404` explícito
con un mensaje claro es mucho mejor API que un 500 opaco — pero solo lo
escribes si construyes el endpoint pensando en "¿qué pasa si me mandan un
`user_id` que no existe?" en vez de solo en el camino feliz.

`list_conversations` ordena por `updated_at.desc()` — es el endpoint que
hace útil el `_touch_conversation` de la Fase 6: sin esa función tocando
`updated_at` en cada turno, esta lista ordenaría por fecha de *creación*,
no de actividad — una conversación de hace una semana con un mensaje
nuevo hace un minuto aparecería por debajo de una creada ayer sin ninguna
actividad desde entonces.

`get_conversation` construye `ConversationDetailRead(...)` a mano, campo
a campo, en vez de dejar que `from_attributes` lo resuelva solo — porque
`messages` no es un atributo directo de `Conversation` en el modelo
SQLAlchemy (no hay una relación `relationship()` definida), así que hay
que traerlos con una query aparte y ensamblar la respuesta explícitamente.

---

## 5. `app/routers/chat.py`: donde entra el bucle agente

```python
@lru_cache
def _default_llm_provider() -> LLMProvider:
    return OllamaAdapter(base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=settings.llm_model)

def get_llm_provider() -> LLMProvider:
    return _default_llm_provider()

router = APIRouter(prefix="/conversations", tags=["chat"])

@router.post("/{conversation_id}/messages", response_model=ChatMessageResponse)
async def send_message(
    conversation_id: int, body: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    silver_db: AsyncSession = Depends(get_silver_db),
    llm: LLMProvider = Depends(get_llm_provider),
):
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    reply = await run_turn(
        db, silver_db, llm, conversation_id, body.text,
        max_iterations=settings.max_iterations, max_history_messages=settings.max_history_messages,
        row_limit=settings.query_row_limit, max_raw_range_hours=settings.raw_query_max_range_hours,
    )
    return ChatMessageResponse(conversation_id=conversation_id, reply=reply)
```

Este endpoint es la prueba de que las seis fases anteriores hicieron el
trabajo correcto: no hay ni un `try/except`, ni una línea de SQL, ni
lógica de tool-calling aquí — solo comprobar que la conversación existe,
y delegar en `run_turn` (Fase 6) con la configuración (Fase 2) como
parámetros. Toda la complejidad ya vive, probada, en capas anteriores.

Dos detalles de construcción:

- **`@lru_cache` sobre `_default_llm_provider`**: crea el `OllamaAdapter`
  (y su cliente HTTP interno) **una sola vez**, no en cada petición —
  reutiliza la conexión en vez de abrir una nueva por cada mensaje de
  chat.
- **`get_llm_provider()` como función separada, indirecta**, en vez de
  usar `_default_llm_provider` directamente en el `Depends(...)` del
  endpoint: es lo que permite a los tests sustituirlo entero con
  `app.dependency_overrides[get_llm_provider] = lambda: fake_llm` (Fase
  4, sección 5) sin tocar el caché ni el adaptador real.
- **`silver_db: AsyncSession = Depends(get_silver_db)`** se inyecta por
  separado de `db` — el endpoint recibe explícitamente las dos sesiones
  (Fase 3, sección 4) y se las pasa a `run_turn`, que a su vez solo usa
  `silver_db` dentro de `execute_tool` (Fase 5). La separación de
  sesiones llega intacta hasta el endpoint HTTP — nunca hay un punto en
  toda la cadena donde una sesión de escritura se cuele donde debería ir
  la de solo lectura.

---

## 6. `app/main.py`, ahora completo

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import AsyncSessionLocal, create_tables
from app.routers import chat, conversations, users
from app.seed import seed_default_users

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    async with AsyncSessionLocal() as session:
        await seed_default_users(session, settings.seed_users.split(","))
    yield

app = FastAPI(title="UNS Copilot", ..., lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(users.router)
app.include_router(conversations.router)
app.include_router(chat.router)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

Lo que cambió respecto a la versión mínima de la Fase 2: el `lifespan`
ahora crea las tablas (Fase 3) y siembra usuarios (Fase 3) **cada vez que
arranca** el proceso — no solo la primera. Es seguro porque ambas
operaciones son idempotentes por construcción: `create_all` no falla si
las tablas ya existen (solo crea las que faltan), y `seed_default_users`
ya comprobaba explícitamente "¿hay ya usuarios?" antes de insertar (Fase
3, sección 5). Ninguna de las dos garantías fue casualidad — se
decidieron pensando en este momento exacto, en que el `lifespan` las
ejecuta en cada arranque del contenedor, no solo la primera vez.

`conversations.router` se incluye una vez, pero **dos módulos** distintos
(`routers/conversations.py` y `routers/chat.py`) declaran rutas bajo el
mismo prefijo `/conversations` — FastAPI lo permite sin conflicto porque
las rutas exactas no colisionan (`POST /conversations/{id}/messages` es
distinta de `POST /conversations/`). Es una forma razonable de separar
"CRUD de conversaciones" de "lógica de chat" sin que ninguna de las dos
cargue con responsabilidades de la otra.

---

## 7. Checkpoint de esta fase

**Primero, con `TestClient` y `FakeLLMProvider` — sin Ollama real:**

```bash
cd UNS_COPILOT/backend
DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot \
  pytest tests/test_health.py tests/test_users_router.py tests/test_conversations_router.py tests/test_chat_router.py -v
```

**Salida esperada:** todo en verde. Fíjate especialmente en lo que
comprueba `test_chat_router.py`:
`test_run_turn_prepends_a_system_message_with_the_current_time` verifica,
end-to-end a través del endpoint HTTP, que el primer mensaje mandado al
`FakeLLMProvider` es `role: "system"` y contiene una marca de tiempo
ISO 8601 — y que ese mensaje de sistema **nunca aparece** en
`GET /conversations/{id}` después. Es la Fase 6, sección 3.1, verificada
ahora desde fuera, a través de HTTP.

**Después, extremo a extremo real, con Docker y Ollama vivos**
(anticipo de la Fase 9, pero ya puedes probarlo):

```bash
cd UNS_COPILOT && ./scripts/restart.sh
curl http://localhost:8002/users/
curl -X POST http://localhost:8002/conversations/ -H "Content-Type: application/json" -d '{"user_id": 1}'
curl -X POST http://localhost:8002/conversations/1/messages -H "Content-Type: application/json" -d '{"text": "¿qué señales conoces?"}'
curl http://localhost:8002/conversations/1
```

**Salida esperada del último `curl`:** un array `messages` con, como
mínimo, `role: "user"` → `role: "assistant"` (con `tool_calls` si el
modelo decidió llamar a `get_catalog`) → `role: "tool"` (si hubo llamada)
→ `role: "assistant"` final — el ciclo completo de la Fase 6, ahora
disparado y observado por HTTP de verdad.

---

## 8. Qué sigue

Con la API completa respondiendo, la Fase 8 añade la última pieza de
código de negocio: **observabilidad opcional con Langfuse** — diseñada,
desde el primer día, para que su ausencia o fallo nunca rompa nada de lo
que ya construiste.
