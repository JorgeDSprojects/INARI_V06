# Fase 2 — El esqueleto del proyecto

Con el contrato de las cuatro tools decidido (Fase 1), toca el primer
código — pero todavía no lógica de negocio. Esta fase construye el
**andamiaje**: algo que arranca con Docker, responde a una petición HTTP
mínima, y tiene configuración externa — antes de que exista una sola tool
o una sola tabla. La razón de empezar aquí y no por el modelo de datos o
las tools: si el contenedor no arranca, nada de lo que construyas encima
se puede verificar. Primero el terreno firme, luego el edificio.

---

## 1. La estructura de carpetas objetivo

No hace falta crearla toda de golpe, pero conviene tenerla en la cabeza
para saber dónde va cada cosa en las fases siguientes:

```
UNS_COPILOT/
├── .env.example
├── docker-compose.yml
├── postgres/
│   └── init.sql              # ver sección 3 — por qué casi vacío
├── scripts/
│   ├── up.sh  down.sh  restart.sh  logs.sh  status.sh
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── app/
    │   ├── main.py            # Fase 2 (mínimo) → Fase 7 (completo)
    │   ├── config.py          # Fase 2
    │   ├── database.py        # Fase 3
    │   ├── seed.py             # Fase 3
    │   ├── crud.py             # Fase 3
    │   ├── observability.py   # Fase 8
    │   ├── models/chat.py     # Fase 3
    │   ├── schemas/chat.py    # Fase 7
    │   ├── llm/               # Fase 4
    │   ├── tools/              # Fase 5
    │   ├── agent/               # Fase 6
    │   └── routers/            # Fase 7
    └── tests/
```

Esta es exactamente la estructura real de `UNS_COPILOT/backend/app/` —
no es un ejemplo simplificado.

---

## 2. `docker-compose.yml`: dos servicios, y por qué se nombran así

```yaml
services:
  copilot_postgres:
    image: postgres:16-alpine
    container_name: uns_copilot_postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-copilot}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-copilotpassword}
      POSTGRES_DB: ${POSTGRES_DB:-uns_copilot}
    volumes:
      - copilot_postgres_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    ports:
      - "${POSTGRES_PORT:-5437}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-copilot} -d ${POSTGRES_DB:-uns_copilot}"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - copilot_net

  copilot_backend:
    build:
      context: ./backend
    container_name: uns_copilot_backend
    environment:
      DATABASE_URL: ${DATABASE_URL:-postgresql+asyncpg://...@uns_copilot_postgres:5432/...}
      SILVER_DATABASE_URL: ${SILVER_DATABASE_URL:-postgresql+asyncpg://silver:...@uns_silver_postgres:5432/uns_silver}
      LLM_BASE_URL: ${LLM_BASE_URL:-http://host.docker.internal:11434/v1}
      # ... resto de variables, ver .env.example
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "${BACKEND_PORT:-8002}:8000"
    depends_on:
      copilot_postgres:
        condition: service_healthy
    networks:
      - copilot_net
      - uns_manager_net

volumes:
  copilot_postgres_data:
    name: uns_copilot_copilot_postgres_data

networks:
  copilot_net:
    driver: bridge
    name: uns_copilot_copilot_net
  uns_manager_net:
    external: true
    name: ${UNS_MANAGER_NETWORK_NAME:-uns_manager_uns_net}
```

Cuatro decisiones aquí que no son arbitrarias:

1. **Los nombres de servicio llevan el prefijo `copilot_`**
   (`copilot_postgres`, `copilot_backend`), no `postgres`/`backend` a
   secas. El `docker-compose.yml` raíz del repo hace `include:` de los
   cuatro/cinco `docker-compose.yml` de cada servicio, y Compose fusiona
   servicios **por clave** entre todos los ficheros incluidos — si dos
   servicios de proyectos distintos se llamaran ambos `postgres`, uno
   pisaría al otro al hacer `docker compose up` desde la raíz. Prefijar
   por servicio es la única forma de que "levantar todo junto" no
   colisione.
2. **`depends_on` con `condition: service_healthy`, no solo el nombre del
   servicio.** Un `depends_on: [copilot_postgres]` a secas solo espera a
   que el contenedor *arranque*, no a que Postgres ya acepte conexiones —
   el backend fallaría en su primer intento de conectar. La condición
   `service_healthy` engancha con el `healthcheck` (`pg_isready`) de
   arriba: el backend no arranca hasta que Postgres de verdad responde.
3. **`extra_hosts: host.docker.internal:host-gateway`.** Ollama corre
   *fuera* de Docker (en la máquina host, o en otro hardware). Desde
   dentro de un contenedor, `localhost` es el propio contenedor, no tu
   máquina — necesitas `host.docker.internal`, que Docker Desktop
   resuelve solo pero Docker "a secas" en Linux no, de ahí el mapeo
   explícito.
4. **Dos redes en `copilot_backend`**: `copilot_net` (propia, para hablar
   con su Postgres) y `uns_manager_net` (externa, ya creada por
   `UNS_MANAGER`) — es cómo `copilot_backend` llegará más adelante a
   `uns_silver_postgres`, que vive en esa misma red compartida entre
   servicios (ver `CLAUDE.md`, tabla de dependencias entre servicios).

---

## 3. `.env.example` → `.env`

```bash
cp UNS_COPILOT/.env.example UNS_COPILOT/.env
```

El `.env.example` versionado documenta cada variable con un comentario
explicando *por qué* tiene ese valor por defecto — no solo qué es. Dos
ejemplos que merece la pena mirar ahora, antes de rellenar nada a ciegas:

```bash
# NOTE: this value is read from inside the copilot_backend container, where
# "localhost" is the container itself — not the Docker host.
LLM_BASE_URL=http://host.docker.internal:11434/v1
```

(la misma razón que la decisión 3 de la sección anterior, mirada desde el
lado de la variable de entorno).

```bash
# Seed users for the "who are you" selector (no passwords) — comma-separated
SEED_USERS=Operario Juan,Ingeniera Maria,Administrador
```

(anticipa la Fase 3 — el selector de usuario sin contraseña que se
decidió en la Fase 1, sección 6).

**Regla del repo, no solo de este servicio:** `.env.example` se versiona,
`.env` real nunca (`AGENTS.md`). Nunca hardcodees un secreto en
`docker-compose.yml` ni en el código — todo pasa por variable de entorno
con su default documentado aquí.

---

## 4. `app/config.py`: la configuración como código tipado, no como `os.getenv` disperso

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot"
    silver_database_url: str = "postgresql+asyncpg://silver:silverpassword@localhost:5436/uns_silver"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:14b"

    max_iterations: int = 5
    max_history_messages: int = 40
    query_row_limit: int = 1000
    raw_query_max_range_hours: int = 24

    seed_users: str = "Operario Juan,Ingeniera Maria,Administrador"

    langfuse_enabled: bool = False
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

settings = Settings()
```

Por qué esto y no leer `os.getenv("MAX_ITERATIONS", "5")` suelto en cada
archivo que lo necesite:

- **Un único sitio con el valor por defecto de cada variable** — si
  cambias el default de `max_iterations`, lo cambias en un lugar, no
  buscas todas las llamadas a `os.getenv`.
- **Tipado y coerción automáticos.** `MAX_ITERATIONS=3` en el `.env` es
  texto; `pydantic-settings` lo convierte a `int` solo, y si alguien
  escribe `MAX_ITERATIONS=cinco` falla al arrancar (fail-fast) en vez de
  fallar más tarde con un error críptico de comparación `str` vs `int`
  dentro del bucle agente.
- **Un objeto `settings` importable** (`from app.config import settings`)
  en vez de pasar veinte parámetros sueltos por todo el código.

Fíjate que **todos los valores tienen un default razonable** — el
servicio arranca sin `.env` en absoluto (útil en tests, sección 6). Solo
necesitas `.env` de verdad para valores que sí importan en tu máquina:
`LLM_BASE_URL` apuntando a tu Ollama real, sobre todo.

---

## 5. `Dockerfile` y `requirements.txt`: lo mínimo para que exista una imagen

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Dos detalles que no son solo "boilerplate de Python":

- **`gcc libpq-dev`**: `asyncpg` (el driver Postgres async que usa
  SQLAlchemy aquí) compila una extensión en la instalación; sin estas
  dos librerías de sistema, `pip install` falla dentro del contenedor
  aunque funcione perfectamente en tu máquina si ya las tienes puestas.
  Es un error clásico de "en mi máquina funciona" que solo aparece al
  dockerizar.
- **`COPY requirements.txt .` antes que `COPY app/`**: separar estas dos
  copias es lo que permite a Docker cachear la capa de `pip install`
  — si solo cambias código en `app/`, un rebuild no reinstala todas las
  dependencias desde cero, solo si `requirements.txt` cambió.

`requirements.txt` en esta fase solo necesita lo mínimo para un
`/health`: `fastapi`, `uvicorn[standard]`. El resto
(`sqlalchemy[asyncio]`, `asyncpg`, `pydantic-settings`, `openai`,
`langfuse`, `httpx`, `pytest`, `pytest-asyncio`) se va añadiendo fase a
fase, a medida que el código los necesita — no de golpe.

---

## 6. `app/main.py`: la versión más pequeña que se puede probar

```python
from fastapi import FastAPI

app = FastAPI(
    title="UNS Copilot",
    description="Natural-language chat backend over UNS_SILVER, via controlled tool-calling",
    version="1.0.0",
)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

Esta es una versión deliberadamente incompleta del `main.py` real (que en
la Fase 7 gana un `lifespan` que crea tablas, siembra usuarios y registra
tres routers, y CORS). Aquí, en la Fase 2, el objetivo único es: ¿arranca
el contenedor y responde algo por HTTP? Nada más todavía.

---

## 7. Los scripts de conveniencia

```bash
# scripts/up.sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --build
echo "UNS Copilot is starting. Use scripts/status.sh to check container health."
```

`down.sh`, `restart.sh`, `logs.sh`, `status.sh` siguen el mismo patrón:
`set -euo pipefail` (para que cualquier fallo intermedio detenga el
script con código de salida distinto de 0, en vez de seguir como si nada)
y `cd` a la raíz del servicio primero, así puedes ejecutarlos desde
cualquier sitio. Es el mismo patrón que ya usan `UNS_HISTORIAN`,
`UNS_SILVER` y `UNS_DASHBOARD` — una convención de repo, no una decisión
nueva de este servicio.

---

## 8. Checkpoint de esta fase

Con todo lo anterior en su sitio:

```bash
cd UNS_COPILOT
cp .env.example .env
./scripts/up.sh
./scripts/status.sh
```

**Salida esperada de `status.sh`:** dos contenedores, `uns_copilot_postgres`
en estado `Up (healthy)` y `uns_copilot_backend` en `Up`.

```bash
curl http://localhost:8002/health
```

**Salida esperada:** `{"status":"ok"}`.

Si `copilot_backend` no arranca, el sospechoso número uno es
`depends_on`/`healthcheck` mal escrito o el puerto `8002` ya ocupado —
revisa con `./scripts/logs.sh copilot_backend`. Si arranca pero `/health`
no responde, revisa que el `Dockerfile` copia `app/` *después* de
`main.py` existir con contenido — un error tonto pero común en este
punto es dejar `app/main.py` vacío.

---

## 9. Qué sigue

Con el contenedor respondiendo, la Fase 3 añade el **modelo de datos**:
las tablas `users`/`conversations`/`messages`, y una decisión de diseño
que sorprende a quien viene de `UNS_HISTORIAN`/`UNS_SILVER` (que sí usan
migraciones `.sql`): aquí las tablas no las crea `init.sql`.
