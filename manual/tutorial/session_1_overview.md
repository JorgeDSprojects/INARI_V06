# Sesión 1 — Visión general & arquitectura

En esta primera sesión damos una visión de alto nivel del proyecto **UNS Copilot** (backend de chat con IA). Se describen los objetivos, los componentes principales y el flujo de una petición.

---

## 1️⃣ Problema que resuelve UNS Copilot
- Los datos de **UNS Silver** son una colección de señales (temperatura, presión, KPIs…) almacenadas en PostgreSQL.
- Los usuarios quieren preguntar en lenguaje natural sin conocer la estructura de la tabla ni escribir SQL.
- Permitir que el modelo genere *cualquier* SQL sería un riesgo de seguridad y de consistencia.

### Solución propuesta
- Un **agente** que solo puede llamar a una **capa de herramientas tipificadas** (tool‑calling) que ejecutan consultas *pre‑definidas* y controladas.
- Cada herramienta valida sus parámetros con **Pydantic**, luego ejecuta una **consulta parametrizada** contra `uns_silver_postgres`.
- El resto del flujo (historial, persistencia, trazas) es agnóstico al modelo y puede cambiarse sin tocar la lógica de negocio.

---

## 2️⃣ Diagrama de arquitectura de alto nivel
```
┌─────────────────────┐   Docker network: uns_net   ┌───────────────────────┐
│ uns_silver_postgres │←─ read‑only ──►│ uns_copilot_backend   │
│ (catálogo, lecturas)│                │ (FastAPI + bucle IA)  │
└─────────────────────┘                └───────▲───────────────┘
                                             │
                                             │   opcional Langfuse (infra_net)
                                             │
                                   ┌─────────┴─────────┐
                                   │ uns_copilot_postgres│
                                   │ (users, chats)    │
                                   └───────────────────┘
```
- **`uns_copilot_backend`** (FastAPI) expone la API REST y contiene el *bucle agente* que coordina LLM y herramientas.
- **`uns_copilot_postgres`** guarda usuarios, conversaciones y cada mensaje (incluido *tool call* y su resultado).
- **`uns_silver_postgres`** es el origen de datos (catálogo, lecturas, eventos) y se accede **solo en modo lectura**.
- **`infra_net`** conecta opcionalmente a un contenedor Langfuse para trazas de LLM.

---

## 3️⃣ Flujo de una petición típica
1. **Cliente** → `POST /conversations/{id}/messages` con texto del usuario.
2. **FastAPI** persiste el mensaje (`role='user'`).
3. **`run_turn`** carga el historial (últimos *N* mensajes) y lo envía al LLM mediante `LLMProvider.chat()`.
4. LLM devuelve **tool_calls** o una respuesta final.
5. Por cada `tool_call`:
   - Se valida con su **Pydantic schema**.
   - Se ejecuta una **consulta parametrizada** contra `uns_silver_postgres`.
   - El resultado se persiste como mensaje `role='tool'` y se añade al historial.
6. El bucle repite hasta que el LLM ya no pide más herramientas (máximo `MAX_ITERATIONS`).
7. Respuesta final del asistente se devuelve al cliente.

---

## 4️⃣ Principios de diseño que guían todo el proyecto
| Principio | Aplicación concreta |
|-----------|---------------------|
| **Seguridad primero** | Sólo consultas *pre‑definidas*, siempre parametrizadas, sin interpolación de strings. |
| **Independencia del modelo** | Interfaz `LLMProvider`; el único punto que cambia al pasar de Ollama a OpenAI o Anthropic. |
| **Observabilidad opcional** | Langfuse envuelto en decoradores, nunca bloquea la respuesta si falla. |
| **Extensibilidad de tools** | Cada herramienta es una función + schema Pydantic; añadir una nueva es solo crear el módulo y registrarlo. |
| **Persistencia de historial** | Cada turno (usuario, asistente, tool, tool‑result) se guarda en `messages`, permitiendo reproducir y depurar conversaciones completas. |

---

## 5️⃣ Qué sigue
En la **Sesión 2** configuraremos el entorno de desarrollo (Docker, variables `.env`, Ollama) y levantaremos los contenedores. Después revisaremos el modelado de datos, la estructura del código Python, los schemas, pruebas y más.

---

**Resumen rápido**
- UNS Copilot es un backend FastAPI que habilita chat con herramientas tipificadas contra UNS Silver.
- El agente ejecuta un bucle LLM ↔ tool ↔ DB, persiste todo el historial y opcionalmente traza con Langfuse.
- Seguridad y extensibilidad se logran mediante Pydantic, SQL parametrizado y la abstracción `LLMProvider`.

Cuando estés listo, avísame y pasamos a la **Sesión 2 — Entorno de desarrollo y despliegue**.
