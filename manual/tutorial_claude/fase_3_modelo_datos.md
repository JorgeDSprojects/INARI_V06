# Fase 3 — El modelo de datos

Con el contenedor respondiendo (Fase 2), toca decidir qué guarda la base
de datos propia de `UNS_COPILOT` (`uns_copilot_postgres`) y cómo se crea
ese esquema. Aquí hay una decisión que rompe con el patrón que ya conoces
de `UNS_HISTORIAN`/`UNS_SILVER` — mejor verla de frente que descubrirla
por sorpresa.

---

## 1. Qué hay que guardar — se deriva de la Fase 1, no es nuevo

La Fase 1 (sección 6) ya decidió: conversaciones multi-turno persistidas,
un selector de usuario sin contraseña. Eso son, literalmente, tres
tablas:

| Tabla | Para qué |
|---|---|
| `users` | quién habla — un selector, no un sistema de login |
| `conversations` | agrupa mensajes en hilos independientes por usuario |
| `messages` | **cada** paso del bucle agente: pregunta del usuario, petición de tool del asistente, resultado de la tool, respuesta final |

El detalle que hace `messages` distinto de "una tabla de chat típica": no
guarda solo lo que el usuario ve. Guarda también los pasos internos
(`tool_calls`, resultados) — porque sin ellos no podrías reconstruir el
historial que se le manda al LLM en el turno siguiente (lo verás en la
Fase 6), ni depurar por qué respondió lo que respondió.

---

## 2. La decisión que sorprende: las tablas no las crea `init.sql`

En `UNS_HISTORIAN` y `UNS_SILVER`, el esquema vive en migraciones SQL
versionadas que Postgres ejecuta al arrancar por primera vez
(`/docker-entrypoint-initdb.d/`). Aquí, `postgres/init.sql` contiene
literalmente esto:

```sql
-- Tables are created by the backend's SQLAlchemy metadata on startup
-- (see app/database.py: create_tables). This file exists so the
-- docker-compose volume mount point is documented and ready if a raw-SQL
-- migration is ever needed later.
```

El esquema real se define en **Python**, como clases SQLAlchemy, y se
crea con `Base.metadata.create_all(...)` cuando arranca la aplicación
(lo verás cableado al `lifespan` de FastAPI en la Fase 7). ¿Por qué aquí
sí y en los otros servicios no?

- **`UNS_HISTORIAN`/`UNS_SILVER` gestionan infraestructura de datos
  crítica y compartida** (hypertables de TimescaleDB, vistas
  materializadas, particionado) — ahí una migración SQL explícita,
  versionada y revisable es la herramienta correcta, y de hecho
  necesaria (TimescaleDB no se configura solo con un ORM).
- **`UNS_COPILOT` tiene un esquema pequeño, propio del servicio, sin
  necesidades de TimescaleDB.** Definirlo como clases Python evita
  mantener dos fuentes de verdad (el `CREATE TABLE` en SQL y el modelo
  ORM en Python) para algo que cambia poco y no tiene requisitos
  especiales de motor.

**El compromiso que aceptas al elegir esto:** `create_all` crea tablas que
no existen, pero **no** migra las que ya existen si cambias una columna —
no hay "ALTER TABLE" automático. Para un MVP en desarrollo activo es
aceptable (bajas el volumen y recreas); para producción con datos reales
ya no lo sería, y ahí tocaría introducir Alembic o migraciones SQL como
las de los otros servicios. Es una decisión de alcance explícita, del
mismo tipo que las de la Fase 1, sección 6 — no un descuido.

---

## 3. `app/models/chat.py`: el esquema como clases SQLAlchemy

```python
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' | 'assistant' | 'tool'
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSONB)
    tool_call_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

Decisiones de esquema que vale la pena justificar, campo a campo:

- **`tool_calls` es `JSONB`, no una tabla relacional aparte.** Un mensaje
  puede pedir varias tools a la vez, cada una con su propia forma de
  argumentos (distinta por tool). Modelarlo como tabla relacional
  obligaría a una tabla genérica `key/value` o a una tabla por tool —
  ambas peores que guardar el array tal cual, porque de todas formas
  nunca se hace una consulta SQL *sobre el contenido* de `tool_calls`
  (solo se lee entero y se manda al LLM). JSONB es la elección correcta
  cuando el dato se escribe y se lee como unidad, y no se filtra por sus
  campos internos.
- **`tool_call_id` es la columna que correlaciona un mensaje `role="tool"`
  con la petición del asistente que lo generó** (ver el tutorial de
  lectura de código, sección 3, sobre por qué ese `id` es crítico) —
  vive como columna propia, no dentro de un JSON, porque sí se usa para
  filtrar/correlacionar en código (aunque no en SQL directamente).
- **`onupdate=func.now()` solo en `Conversation.updated_at`, no en
  `Message`.** Los mensajes son inmutables una vez creados (nunca se
  actualiza un mensaje ya persistido); la conversación sí necesita saber
  "cuándo fue la última actividad" para poder ordenar
  `GET /conversations/?user_id=` por recencia (verás esto en la Fase 7).
  Aviso importante para la Fase 6: `onupdate` solo dispara con un
  `UPDATE` explícito sobre esa fila — insertar un mensaje nuevo en
  `messages` **no** toca `updated_at` de su conversación por sí solo; hay
  que actualizarla a mano.
- **Sin `Index()` explícitos en este código**, aunque el spec de diseño
  (`docs/superpowers/specs/2026-09-06-uns-copilot-design.md`, Sección 2)
  propone `idx_conversations_user` e `idx_messages_conversation`. Esto es
  una discrepancia real entre spec y código que merece la pena que notes
  tú mismo como ejercicio: con el volumen de datos de un MVP no se nota,
  pero es exactamente el tipo de índice que añadirías antes de producción
  si las consultas por `conversation_id`/`user_id` empiezan a ser lentas.

---

## 4. `app/database.py`: dos motores, uno de ellos de solo lectura

```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Read-only against uns_silver_postgres — see Global Constraints: never
# INSERT/UPDATE/DELETE through this engine.
silver_engine = create_async_engine(settings.silver_database_url, echo=False, future=True)
SilverSessionLocal = async_sessionmaker(silver_engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

async def get_silver_db() -> AsyncSession:
    async with SilverSessionLocal() as session:
        yield session

async def create_tables() -> None:
    from app.models import chat  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

Fíjate en la separación explícita: **dos engines, dos sesiones,
completamente independientes** — `engine`/`AsyncSessionLocal` para la
Postgres propia (lectura y escritura), `silver_engine`/`SilverSessionLocal`
para `uns_silver_postgres` (comentario en el propio código: nunca
INSERT/UPDATE/DELETE ahí). Esta separación **a nivel de conexión**, no
solo de convención, es la forma de hacer cumplir en código el principio
de la Fase 1 sección 5 ("toda tool es de solo lectura") — aunque nada en
Python te impide técnicamente escribir por esa sesión, tenerla en un
objeto/variable completamente aparte, con un nombre que lo deja explícito,
hace mucho más difícil hacerlo por error. (Un endurecimiento real de esto
sería un rol de Postgres con permisos `GRANT SELECT` únicamente en
`SILVER_DATABASE_URL` — el spec lo menciona como intención; sección 6 de
la Fase 1.)

`get_db`/`get_silver_db` son *generadores async* — el patrón de
"dependency injection" de FastAPI (`Depends(get_db)`, lo verás en la
Fase 7): cada petición HTTP obtiene su propia sesión, que se cierra sola
al terminar el `async with`, incluso si la petición lanza una excepción.

`create_tables()` importa `app.models.chat` **dentro** de la función, no
en el nivel de módulo de `database.py`. No es casual: si lo importaras
arriba del todo, tendrías una importación circular (`models/chat.py`
importa `Base` desde `database.py`; si `database.py` importara `models`
al cargarse, se llamarían mutuamente antes de que ninguno de los dos
termine de definirse). Importar dentro de la función rompe el ciclo
porque para entonces `database.py` ya terminó de definir `Base`.

---

## 5. `app/seed.py`: usuarios de prueba, de forma idempotente

```python
from sqlalchemy import select
from app.models.chat import User

async def seed_default_users(session, names: list[str]) -> None:
    """Idempotent: does nothing if the users table already has any rows,
    so a restart never duplicates or resets the selector's user list."""
    existing = (await session.execute(select(User.id).limit(1))).first()
    if existing is not None:
        return
    for name in names:
        name = name.strip()
        if name:
            session.add(User(display_name=name))
    await session.commit()
```

El detalle de diseño aquí: **idempotencia por comprobación, no por
`ON CONFLICT DO NOTHING`.** Se elige comprobar "¿hay ya alguna fila?" en
vez de intentar insertar y absorber el conflicto porque `display_name`
tiene una restricción `UNIQUE` (sección 3) — un `ON CONFLICT` funcionaría
igual de bien aquí, pero el chequeo previo dice más claramente la
intención ("solo sembramos si la tabla está realmente vacía") y evita
tener que razonar sobre qué pasa si cambias la lista de `SEED_USERS` en
`.env` después del primer arranque (con este código: nada — los usuarios
ya sembrados se quedan, los nuevos de la lista no se añaden hasta que
vacíes la tabla).

---

## 6. Checkpoint de esta fase

Este es el primer checkpoint que toca la base de datos real, así que
necesitas Postgres arriba y las dependencias de este código instaladas
localmente (fuera de Docker) para poder ejecutar tests directamente:

```bash
cd UNS_COPILOT
./scripts/up.sh                      # levanta copilot_postgres (y el backend, aunque aún no lo usemos)
cd backend
pip install -r requirements.txt
```

Comprueba primero que el esquema se puede crear de verdad, con una
sesión interactiva de Python (nada de HTTP todavía):

```bash
DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot \
  python -c "
import asyncio
from app.database import create_tables
asyncio.run(create_tables())
print('tablas creadas OK')
"
```

**Salida esperada:** `tablas creadas OK`, sin excepciones. Verifícalo
también desde fuera de Python:

```bash
docker exec -it uns_copilot_postgres psql -U copilot -d uns_copilot -c "\dt"
```

**Salida esperada:** tres tablas — `users`, `conversations`, `messages`.

Con eso confirmado, corre los tests que ya existen en el repo para esta
capa (necesitan la misma variable de entorno, porque están escritos para
saltarse a sí mismos si no hay Postgres vivo — mira el
`pytest.mark.skipif(not DATABASE_URL, ...)` al principio de cada uno):

```bash
DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot \
  pytest tests/test_models.py tests/test_crud.py -v
```

**Salida esperada:** todos en verde. `test_models.py` comprueba que
`User → Conversation → Message` encadenan correctamente por
`user_id`/`conversation_id`; `test_crud.py` comprueba algo más sutil que
verás con detalle en la Fase 6 — que `load_messages` nunca devuelve un
historial que empiece con un mensaje `role="tool"` huérfano (uno cuyo
`assistant` que lo pidió quedó fuera de la ventana truncada).

---

## 7. Qué sigue

Con el modelo de datos verificado, la Fase 4 construye la **capa LLM**:
la interfaz `LLMProvider` y sus dos implementaciones (`OllamaAdapter` para
producción, `FakeLLMProvider` para tests) — la primera pieza que de
verdad tiene que ver con "hablar con un modelo", y la única de las
próximas fases que no necesita ni Postgres ni Docker para verificarse.
