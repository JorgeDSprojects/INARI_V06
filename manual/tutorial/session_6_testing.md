# Sesión 6 — Testing (unitario, integración y mocks)

En esta sesión aprenderás a **probar** cada capa de UNS Copilot de forma aislada y combinada. Veremos:
1. Tests unitarios de las *tools* usando una base de datos de prueba.
2. Tests de integración que levantan contenedores reales (Postgres + Ollama) con `docker compose`.
3. Cómo usar el **FakeLLMProvider** para simular respuestas del modelo y hacer pruebas determinísticas del bucle agente.
4. Fixtures y utilities comunes (`client`, `db_session`).

---

## 1️⃣ Estructura de tests en el proyecto
Los tests viven bajo `UNS_COPILOT/backend/tests/`.
```
tests/
 ├─ conftest.py            # fixtures de Pytest (client, DB, llm)
 ├─ test_config.py        # pruebas de configuración/env
 ├─ test_models.py        # validaciones de ORM (p.ej. relaciones FK)
 ├─ test_tools_integration.py   # herramientas contra DB real
 ├─ test_tool_params.py   # validación Pydantic de Params
 ├─ test_agent_loop.py    # bucle completo con FakeLLMProvider
 └─ ...
```
Cada archivo se ejecuta de forma independiente; Pytest descubre automáticamente los archivos que empiezan por `test_`.

---

## 2️⃣ Fixtures comunes (`conftest.py`)
```python
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app import models

# --- 1️⃣ Cliente FastAPI -------------------------------------------------
@pytest.fixture(scope="session")
def client():
    # Usa la app de FastAPI directamente (no necesita Docker)
    with TestClient(app) as c:
        yield c

# --- 2️⃣ Base de datos SQLite en memoria para unit tests ---------------
SQLITE_URL = "sqlite+pysqlite:///:memory:?check_same_thread=False"
engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    # Crea tablas en memoria y derriba al final de la prueba
    models.Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        models.Base.metadata.drop_all(bind=engine)

# --- 3️⃣ Fake LLM -------------------------------------------------------
from app.llm.fake import FakeLLMProvider
from app.llm.base import ChatResponse, ToolCall

@pytest.fixture
def fake_llm_success():
    # Simula una respuesta que hace un solo tool call y luego finaliza
    tool_call = ToolCall(id="tc_1", name="get_latest_value", arguments={"topic": "temperature", "signal_key": "room1"})
    turn1 = ChatResponse(content=None, tool_calls=[tool_call], finish_reason="tool_calls")
    turn2 = ChatResponse(content="El valor actual es 23.4°C", tool_calls=[], finish_reason="stop")
    return FakeLLMProvider([turn1, turn2])

# --- 4️⃣ Helper para crear usuarios de prueba --------------------------
from app.models import user as user_model

def create_user(db, display_name="alice"):
    u = user_model.User(display_name=display_name)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u
```
- `client` permite probar los endpoints HTTP sin levantar Docker.
- `db_session` crea una base SQLite en memoria (ideal para **unit tests** de tools y modelos). 
- `fake_llm_success` devuelve una secuencia pre‑definida de respuestas que el bucle agente consumirá.

---

## 3️⃣ Tests unitarios de herramientas (`test_tool_params.py` y `test_tools_integration.py`)
### 3.1 Validación de parámetros (Pydantic)
```python
def test_latest_value_params_validation():
    from app.tools.latest_value import Params
    # Caso válido
    p = Params(topic="temperature", signal_key="room1")
    assert p.topic == "temperature"
    # Caso inválido – falta signal_key
    with pytest.raises(ValidationError):
        Params(topic="temperature")
```
- Usa `pytest.raises` para asegurar que **Pydantic** lanza `ValidationError` cuando falten campos.

### 3.2 Ejecución contra base de datos de prueba
```python
def test_get_latest_value_success(db_session):
    from app.tools.latest_value import Params, execute
    # Preparar tabla de ejemplo (se asume que la tabla silver_latest_value existe en el schema de tests)
    db_session.execute(
        "INSERT INTO silver_latest_value (topic, signal_key, value) VALUES (:t, :k, :v)",
        {"t": "temperature", "k": "room1", "v": 23.4},
    )
    db_session.commit()

    params = Params(topic="temperature", signal_key="room1")
    result = execute(db_session, params)
    assert result == {"value": 23.4}
```
- **Nota**: la tabla `silver_latest_value` forma parte del esquema de `uns_silver_postgres`. En los tests de integración (ver sección 4) usamos el contenedor real para esa tabla.

---

## 4️⃣ Tests de integración (Docker real)
Para probar contra la base de datos real y Ollama, usamos `docker compose` con la red externa `uns_net`. El proceso típico:
1. **Levantar los contenedores** (solo Copilot y su Postgres).
   ```powershell
   docker compose -f UNS_COPILOT/docker-compose.yml up -d
   ```
2. **Esperar a que estén listos** (puedes usar `docker compose logs -f uns_copilot_postgres` y esperar a `database system is ready to accept connections`).
3. **Ejecutar los tests** con la variable de entorno que apunta al contenedor real:
   ```powershell
   set POSTGRES_URL=postgresql://copilot_user:superSecret123@localhost:5432/copilot_db
   pytest backend/tests/test_tools_integration.py -q
   ```
   - El fixture `db_session` se ajusta automáticamente si detecta la variable `POSTGRES_URL` y crea un `engine` contra esa URL en vez de SQLite.
4. **Detener** al terminar:
   ```powershell
   docker compose -f UNS_COPILOT/docker-compose.yml down -v
   ```
### Ejemplo de test de integración que usa Ollama
```python
def test_agent_full_flow_with_real_ollama(client, db_session):
    # **Prerequisitos**: Ollama con modelo qwen2.5:14b corriendo y .env configurado.
    from app.models import conversation as conv_model
    user = create_user(db_session, "alice")
    conv = conv_model.Conversation(user_id=user.id)
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    # Enviar mensaje que desencadene get_latest_value
    payload = {"text": "¿Cuál es el valor actual de temperature para room1?"}
    response = client.post(f"/conversations/{conv.id}/messages", json=payload)
    assert response.status_code == 200
    data = response.json()
    # La respuesta debería contener palabras como "°C" (dependiendo del modelo)
    assert "°C" in data["content"]
```
- Aquí usamos `client` (FastAPI TestClient) que se comunica con el backend que a su vez llama al **LLM real**.
- No se necesita mock porque Ollama está corriendo y el modelo soporta tool‑calling.

---

## 5️⃣ Bucle agente con `FakeLLMProvider` (tests determinísticos)
El objetivo es **no depender** de un modelo externo para validar la lógica del bucle.
### 5.1 Caso de éxito (una herramienta → respuesta final)
```python
def test_run_turn_success(fake_llm_success, db_session):
    from app.agent import run_turn
    # Preparar conversación y usuario
    user = create_user(db_session)
    from app.models import conversation as conv_model
    conv = conv_model.Conversation(user_id=user.id)
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    # Insertar dato en silver_latest_value (tabla de prueba) para que la herramienta lo lea
    db_session.execute(
        "INSERT INTO silver_latest_value (topic, signal_key, value) VALUES (:t, :k, :v)",
        {"t": "temperature", "k": "room1", "v": 23.4},
    )
    db_session.commit()

    answer = run_turn(
        db=db_session,
        conversation_id=conv.id,
        user_text="¿Cuál es el valor de temperature room1?",
        llm_provider=fake_llm_success,
    )
    assert "23.4" in answer
```
- `fake_llm_success` provee dos turnos predefinidos: primero solicita el tool, después devuelve la respuesta final.
- La prueba verifica que el bucle ejecuta la herramienta, persiste los mensajes y devuelve el texto esperado.

### 5.2 Caso de error de herramienta → LLM re‑intenta
```python
def test_run_turn_tool_error_and_retry():
    from app.llm.base import ChatResponse, ToolCall
    from app.llm.fake import FakeLLMProvider
    from app.agent import run_turn
    # Primer respuesta: llama a get_latest_value con parámetros erróneos (topic missing)
    tc_err = ToolCall(id="tc_1", name="get_latest_value", arguments={"signal_key": "room1"})
    turn_err = ChatResponse(content=None, tool_calls=[tc_err], finish_reason="tool_calls")
    # Segunda respuesta: modelo corrige y llama de nuevo con parámetros correctos
    tc_ok = ToolCall(id="tc_2", name="get_latest_value", arguments={"topic": "temperature", "signal_key": "room1"})
    turn_ok = ChatResponse(content=None, tool_calls=[tc_ok], finish_reason="tool_calls")
    # Tercera respuesta: final
    final = ChatResponse(content="El valor es 23.4°C", tool_calls=[], finish_reason="stop")
    fake = FakeLLMProvider([turn_err, turn_ok, final])

    # Preparar DB similar al test anterior
    # … (creación de usuario, conversación y fila en silver_latest_value)
    # … (omito por brevedad)
    answer = run_turn(db=db, conversation_id=conv.id, user_text="¿Valor?", llm_provider=fake)
    assert "23.4" in answer
```
- Demuestra que el bucle maneja el error (`{"error": …}`) retornado por la herramienta y permite al modelo volver a intentar.

---

## 6️⃣ Cobertura y buenas prácticas de testing
| Práctica | Por qué | Cómo implementarla |
|----------|----------|-------------------|
| **Separar unit y integración** | Los tests unitarios son rápidos y no dependen de Docker; los de integración garantizan que el stack completo funciona. | `pytest -m "unit"` vs `pytest -m "integration"` usando marcadores personalizados. |
| **Usar fixtures reutilizables** | Evita código duplicado (creación de usuarios, conversaciones, DB). | Definir en `conftest.py` y reutilizar con `def test_x(db_session, client): …`. |
| **Mockear LLM** | Los modelos pueden cambiar, son lentos y consumen recursos. | `FakeLLMProvider` con secuencias de `ChatResponse`. |
| **Limpiar contenedores después de tests** | Evita que datos persistan entre ejecuciones y consume menos espacio. | En `pytest` hook `pytest_sessionfinish` ejecutar `docker compose down -v`. |
| **Cobertura mínima 80 %** | Asegura que la mayor parte del código está bajo pruebas. | `pytest --cov=app --cov-report=term-missing`. |

---

## 7️⃣ **Ejercicio práctico** (antes de la siguiente sesión)
1. **Implementa un nuevo test de unidad** llamado `test_get_user_info_success` que:
   - Usa la fixture `db_session` (SQLite). 
   - Crea un usuario con `create_user`. 
   - Llama a la herramienta `get_user_info` (que añadiste en la sesión 5). 
   - Verifica que el JSON devuelto contiene los campos `id`, `display_name` y `created_at` (en formato ISO).
2. **Implementa un test de integración** llamado `test_get_user_info_integration` que:
   - Levanta los contenedores reales (`docker compose -f UNS_COPILOT/docker-compose.yml up -d`).
   - Usa la fixture `client` para crear un usuario a través del endpoint `/users` (si no existe, inserta directamente en la BD). 
   - Envía una petición a `/conversations/{id}/messages` con el texto `"¿Cuál es mi nombre?"`. 
   - En el *prompt* del LLM, incluye la instrucción: *"Si el usuario pregunta por su propio nombre, llama a la herramienta get_user_info con su user_id"* (puedes pre‑cargar un prompt simple en el `FakeLLMProvider`). 
   - Verifica que la respuesta del asistente contiene el `display_name` del usuario.
3. **Ejecuta toda la suite** y asegura que la cobertura sea > 80 %.
   ```powershell
   pytest -q
   pytest --cov=app --cov-report=term-missing
   ```
4. **Commit (opcional)** con mensaje `test(tools): add tests for get_user_info`. No push a `main`.

---

## 8️⃣ Próximos pasos (Sesión 7)
En la **Sesión 7** abordaremos **CI/CD**, cómo integrar los tests en GitHub Actions y automatizar los builds de Docker, además de generar un *badge* de cobertura.

---

**Resumen rápido**
- **Unit tests** usan SQLite en memoria y fixtures reutilizables. 
- **Integration tests** levantan los contenedores reales (`docker compose`). 
- **FakeLLMProvider** permite probar el bucle agente sin depender de un modelo externo. 
- Añadir una herramienta implica tests de params, lógica y (si aplica) integración contra `uns_silver_postgres`. 
- Buenas prácticas: separar capas, usar marcadores, limpiar contenedores y medir cobertura.

Cuando termines los ejercicios, avísame y seguimos con la **Sesión 7 — CI/CD y automatización**.
