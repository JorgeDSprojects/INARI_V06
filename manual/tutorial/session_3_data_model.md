# Sesión 3 — Modelado de datos (PostgreSQL)

En esta sesión nos adentramos en la base de datos propia de **UNS Copilot** (`uns_copilot_postgres`). Veremos el esquema, cómo se inicializa, cómo inspeccionarlo y cómo añadir columnas de forma segura.

---

## 1️⃣ Esquema SQL de la base de datos de Copilot
El archivo que crea el esquema y los datos iniciales está bajo:
```
UNS_COPILOT/postgres/init.sql
```
A continuación el contenido relevante (con comentarios para claridad):
```sql
-- usuarios que pueden iniciar sesión (selector simple, sin password)
CREATE TABLE users (
    id           BIGSERIAL PRIMARY KEY,
    display_name TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cada conversación pertenece a un usuario y tiene un título opcional
CREATE TABLE conversations (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id),
    title      TEXT,                     -- primeros 50 caracteres del primer mensaje
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conversations_user ON conversations (user_id, updated_at DESC);

-- Un mensaje representa cualquier paso del agente:
--   * role='user'      → mensaje del cliente
--   * role='assistant'→ respuesta del LLM (puede contener tool_calls)
--   * role='tool'     → resultado de una herramienta
CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,        -- 'user' | 'assistant' | 'tool'
    content         TEXT,                -- texto del mensaje (NULL cuando solo hay tool_calls)
    tool_calls      JSONB,               -- array de llamadas que el asistente pidió ejecutar
    tool_call_id    TEXT,                -- id del tool_call al que este mensaje responde
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conversation ON messages (conversation_id, created_at);

-- Datos de ejemplo para pruebas rápidas
INSERT INTO users (display_name) VALUES ('alice'), ('bob');
```
### Qué representa cada tabla
| Tabla | Propósito | Campos clave |
|-------|-----------|--------------|
| `users` | Selector simple de quién está usando el copilot. No hay password ni email. | `id`, `display_name` |
| `conversations` | Agrupa un conjunto de mensajes; un usuario puede tener varias conversaciones (tipo "nuevo chat"). | `id`, `user_id` (FK a `users`) |
| `messages` | Guarda **todo** el historial de una conversación, incluidos los *tool calls* y sus resultados, para que se pueda reproducir el turno completo. | `id`, `conversation_id` (FK a `conversations`), `role`, `tool_calls`, `tool_call_id` |

---

## 2️⃣ Cómo se inicializa la base de datos
En `UNS_COPILOT/docker-compose.yml` el contenedor `uns_copilot_postgres` monta la carpeta `postgres/` dentro del contenedor en `/docker-entrypoint-initdb.d/`. PostgreSQL ejecuta automáticamente **todos** los archivos `*.sql` en esa ruta la **primera vez** que se crea el volumen de datos.
```yaml
services:
  uns_copilot_postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - ./postgres:/docker-entrypoint-initdb.d
```
- Si eliminas el contenedor **y** el volumen (`docker compose down -v`), la base se recrea y se vuelve a ejecutar `init.sql`.
- Si solo haces `docker compose restart` o `up` sin eliminar volúmenes, la base **no** vuelve a ejecutar `init.sql` (los datos persisten).

---

## 3️⃣ Inspeccionar la base de datos localmente
Puedes conectarte de dos formas:
### 3.1 Con `psql` instalado en Windows
```powershell
psql "host=localhost port=5432 dbname=copilot_db user=copilot_user password=superSecret123"
```
Una vez dentro, consultas básicas:
```sql
SELECT * FROM users;
SELECT * FROM conversations WHERE user_id = 1;
SELECT id, role, content FROM messages WHERE conversation_id = 1 ORDER BY created_at;
```
### 3.2 Usando `docker exec` (sin instalar `psql`)
```powershell
# Obtén el nombre del contenedor (probablemente "uns_copilot_postgres")
docker ps

# Entra al contenedor
docker exec -it uns_copilot_postgres psql -U copilot_user -d copilot_db
```
Y ejecuta las mismas consultas SQL.

---

## 4️⃣ Añadir nuevas columnas o tablas de forma segura
En un proyecto real usarías una herramienta de migraciones (Alembic, Flyway, etc.). En este repositorio, por simplicidad, hacemos cambios **directamente** y los versionamos manualmente.
### 4.1 Ejemplo: añadir una columna `metadata` a `messages`
1. Crea un archivo de migración en `UNS_COPILOT/postgres/2026-09-15-add-metadata-to-messages.sql` con:
   ```sql
   ALTER TABLE messages ADD COLUMN metadata JSONB DEFAULT '{}'::jsonb;
   ```
2. **Aplicar la migración** (sin recrear la base) ejecutándola dentro del contenedor:
   ```powershell
   docker exec -it uns_copilot_postgres psql -U copilot_user -d copilot_db -f /docker-entrypoint-initdb.d/2026-09-15-add-metadata-to-messages.sql
   ```
3. **Actualizar el código Python** (ORM) añadiendo el atributo a `app/models/message.py`:
   ```python
   metadata = Column(JSONB, server_default=text("'{}'::jsonb"))
   ```
4. Reinicia el backend para que el mapeo ORM se refresque.
### 4.2 Añadir una tabla nueva (ejemplo: `message_tags`)
```sql
CREATE TABLE message_tags (
    message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (message_id, tag)
);
```
- Ejecuta la sentencia como en el paso 2.
- Añade el modelo SQLAlchemy correspondiente en `app/models/message_tag.py` y registra la relación si la necesitas.

---

## 5️⃣ Verificar que la API refleja los cambios de esquema
Después de cualquier migración, **reinicia** el backend (script `restart.sh`).
```powershell
.\UNS_COPILOT\scripts\restart.sh
```
Luego prueba con `curl` que las nuevas columnas aparecen en la respuesta JSON de los endpoints.
Ejemplo, si añades `metadata` a `messages` y envías un mensaje, deberías ver `"metadata":{}` en la respuesta de `GET /conversations/1/messages`.

---

## 6️⃣ Ejercicio práctico (antes de la siguiente sesión)
1. **Conéctate** a la base con `docker exec` y ejecuta:
   ```sql
   SELECT COUNT(*) FROM messages;
   ```
   Anota el número de filas existentes.
2. **Añade la columna `metadata`** a `messages` siguiendo los pasos 4.1.
3. **Reinicia** el backend.
4. **Crea una conversación** y envía al menos **dos mensajes** usando `curl` (uno de usuario y otro que invoque una herramienta).
5. **Inspecciona** la última fila de `messages` para confirmar que `metadata` está presente y contiene `{}` por defecto:
   ```powershell
   docker exec -it uns_copilot_postgres psql -U copilot_user -d copilot_db -c "SELECT id, role, metadata FROM messages ORDER BY created_at DESC LIMIT 1;"
   ```
6. (Opcional) **Actualiza** el código del agente (`app/agent.py`) para que, justo antes de persistir cada mensaje, añada un campo `metadata` con información extra (p.ej. `{"source":"agent"}`). Reinicia y repite los pasos 4‑5 para ver el cambio.

---

## 7️⃣ Qué sigue
En la **Sesión 4** analizaremos la **estructura del backend Python** (FastAPI, routers, modelos, LLM provider, herramientas) y cómo están conectados entre sí.

---

**Resumen rápido**
- Esquema SQL (`users`, `conversations`, `messages`).
- `init.sql` se ejecuta solo la primera vez que el volumen de datos se crea.
- Inspección vía `psql` o `docker exec`. 
- Añadir columnas/tablas → archivo SQL + ejecución manual + actualización del modelo ORM. 
- Reiniciar el backend para que los cambios se reflejen en la API.

Cuando hayas completado el ejercicio, avísame y pasaremos a la **Sesión 4 — Estructura del backend Python**.
