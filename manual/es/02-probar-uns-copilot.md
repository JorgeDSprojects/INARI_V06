# Tutorial: probar UNS Copilot a mano (chat sobre datos de UNS_SILVER)

## 0. Qué es exactamente lo implementado

`UNS_COPILOT` es un backend de chat (FastAPI, sin interfaz gráfica propia
todavía) que responde preguntas en lenguaje natural sobre los datos de
`UNS_SILVER`, llamando a un LLM con **tool calling / function calling**.

Lo concreto que hay construido ahora mismo:

- **Un LLM decide qué herramienta llamar, nunca escribe SQL.** El modelo
  (por defecto, `qwen2.5:14b` vía Ollama, local — no depende de ningún
  proveedor concreto, hay una interfaz `LLMProvider` para poder añadir
  otros más adelante) recibe la pregunta del usuario y un set **cerrado**
  de 4 herramientas tipadas:

  | Herramienta | Para qué sirve | Tabla que consulta |
  |---|---|---|
  | `get_catalog` | Listar qué señales/KPIs están catalogados | `signal_catalog` |
  | `get_latest_value` | Valor actual de una señal concreta | `silver_latest_value` |
  | `query_readings` | Histórico de una señal entre dos instantes (crudo, 1m o 1h) | `silver_readings` / `_1m` / `_1h` |
  | `list_events` | Alarmas/eventos discretos en un rango de tiempo | `silver_events` |

- **Todo el acceso a `uns_silver_postgres` es de solo lectura**, y cada
  parámetro se valida (Pydantic) antes de tocar la base — nunca se
  interpola nada en SQL. `query_readings`/`list_events` tienen límites de
  filas y de rango máximo para evitar que una pregunta ambigua traiga
  semanas de datos.
- **Conversaciones multi-turno persistidas**: cada turno (pregunta del
  usuario, llamada a herramienta, resultado de la herramienta, respuesta
  final) se guarda en `uns_copilot_postgres`, así que puedes hacer
  preguntas de seguimiento ("¿y hace 5 minutos?") dentro de la misma
  conversación.
- **Selector de usuario sin contraseña** (no hay autenticación real
  todavía) — simplemente eliges quién eres de una lista.
- **Si una herramienta falla** (DB caída, parámetro inválido...), el
  fallo se le pasa al modelo como resultado de la herramienta, nunca
  rompe la conversación — el modelo puede reintentar o admitir que no
  puede responder.

Lo que **no** está construido todavía (a propósito, quedó fuera de esta
ronda): interfaz de chat gráfica, exportación a Markdown/PDF/Excel,
trazabilidad con Langfuse activada por defecto (existe el enganche, pero
apagado hasta que exista la infraestructura compartida), otros
proveedores de LLM además de Ollama.

## 1. Prerrequisitos

1. La pila completa levantada desde la raíz del repo:
   ```bash
   docker compose up -d
   ```
   Como mínimo necesitas `UNS_MANAGER` (EMQX), `UNS_HISTORIAN` y
   `UNS_SILVER` corriendo antes que `UNS_COPILOT` — ver `CLAUDE.md` para
   el orden de arranque.
2. Un Ollama accesible en `LLM_BASE_URL` (por defecto
   `http://host.docker.internal:11434/v1`) con un modelo con soporte de
   *tool calling* ya descargado (`ollama pull qwen2.5:14b`, o el que
   tengas configurado en `UNS_COPILOT/.env`).
3. **Al menos una señal real fluyendo** por
   `UNS_MANAGER → UNS_HISTORIAN → UNS_SILVER` — si no, las herramientas
   funcionan pero no hay nada que consultar. Comprueba que hay datos:
   ```bash
   docker exec uns_silver_postgres psql -U silver -d uns_silver \
     -c "SELECT topic, signal_key FROM silver_readings ORDER BY time DESC LIMIT 5;"
   ```
   Si esto no devuelve filas, revisa antes `manual/es/01-verificar-guardado-historian-pgadmin.md`
   y los logs del normalizador (`docker compose logs silver_normalizer`).

## 2. Abre la documentación interactiva (Swagger UI)

Abre `http://localhost:8002/docs`. Todos los pasos siguientes se hacen
ahí con el botón **Try it out** de cada endpoint — no hace falta usar
`curl` a mano, aunque también funciona si lo prefieres.

## 3. Comprueba los usuarios sembrados

`GET /users/` → **Try it out** → **Execute**.

**Resultado esperado:** una lista con 3 usuarios (`Operario Juan`,
`Ingeniera Maria`, `Administrador`), cada uno con un `id` numérico. Anota
el `id` de uno cualquiera — lo necesitas en el siguiente paso.

## 4. Crea una conversación

`POST /conversations/` → **Try it out** → body:
```json
{ "user_id": 1 }
```
(usa el `id` que anotaste). **Execute**.

**Resultado esperado:** `201 Created` con un objeto
`{ "id": ..., "user_id": 1, "title": null, ... }`. Anota este `id` de
conversación — lo usas en todos los pasos siguientes.

## 5. Pregunta por el catálogo

`POST /conversations/{conversation_id}/messages` → body:
```json
{ "text": "¿Qué señales conoces?" }
```

**Resultado esperado (con la pila recién levantada, sin catalogar nada
a mano):** una respuesta diciendo que el catálogo está vacío. **Esto es
correcto, no es un fallo** — `get_catalog` lee `signal_catalog`, que solo
se rellena cuando llega un mensaje MQTT en el topic `..._descriptive`
con metadatos (unidad, umbrales, descripción). Si tu simulador solo
publica en `_informative`/`_analytical` (como el de
`UNS_MANAGER/nodered/flows.seed.json`), nunca habrá entradas de catálogo
— pero las lecturas igualmente se guardan en `silver_readings`, sin
catalogar. Los siguientes pasos funcionan igual sin catálogo.

## 6. Pregunta por el valor actual de una señal conocida

Usa el topic y `signal_key` reales que viste en el paso 1 (o los del
simulador de ejemplo: topic
`GALERNA_ENERGY/SPAIN/GALICIA_COSTA_MORTE/T01/GENERATOR`, señal
`Gen_RPM_Avg`):

```json
{ "text": "¿Cuál es el valor actual de Gen_RPM_Avg en el topic GALERNA_ENERGY/SPAIN/GALICIA_COSTA_MORTE/T01/GENERATOR?" }
```

**Resultado esperado:** una frase con un valor numérico y una marca de
tiempo reciente, por ejemplo:
> *"El valor actual de la señal Gen_RPM_Avg en el topic ... es 1561.4 RPM en el instante 2026-09-09T20:24:44Z."*

(El número exacto variará — el simulador genera una onda senoidal con
ruido.)

## 7. Pregunta por el histórico

```json
{ "text": "Muéstrame las lecturas de Gen_RPM_Avg de los últimos 5 minutos" }
```

**Resultado esperado:** una lista de varias lecturas con su timestamp y
valor, resumida en texto (el modelo no te va a pegar una tabla de 150
filas, suele mostrar algunas y resumir la tendencia).

## 8. Pregunta por alarmas/eventos

```json
{ "text": "¿Ha habido alguna alarma en los últimos 5 minutos?" }
```

**Resultado esperado:** depende de si el simulador ha cruzado algún
umbral en ese rango — puede que responda que no hay alarmas activas, o
(con el simulador de ejemplo, que sí genera picos de RPM/temperatura por
encima de umbral) una respuesta detallando qué señal, qué severidad
(`WARNING`/`CRITICAL`) y desde cuándo.

## 9. Pregunta de seguimiento (memoria multi-turno)

En la **misma conversación**, manda un mensaje corto sin repetir
contexto:
```json
{ "text": "¿y hace 10 minutos?" }
```

**Resultado esperado:** el modelo entiende a qué señal/topic te refieres
sin que lo repitas — porque el historial completo de la conversación se
manda al modelo en cada turno.

## 10. Mira el hilo completo persistido

`GET /conversations/{conversation_id}` → **Execute**.

**Resultado esperado:** un array `messages` con todos los turnos en
orden: `role: "user"` (tu pregunta) → `role: "assistant"` con
`tool_calls` (qué herramienta pidió llamar y con qué parámetros) →
`role: "tool"` con el resultado crudo (JSON) → `role: "assistant"` con
la respuesta final en lenguaje natural. Esto es literalmente lo que pasa
"por dentro" en cada pregunta.

## 11. Si algo no funciona

- **La respuesta dice "hubo un problema técnico"**: mira los logs —
  ```bash
  docker compose logs copilot_backend --tail 50
  ```
  Busca líneas `WARNING app.tools.executor: Tool ... failed: ...` — el
  error real de la herramienta está ahí.
- **Comprueba que Ollama responde:**
  ```bash
  curl http://localhost:11434/api/tags
  ```
- **Comprueba que hay datos que consultar** (ver paso 1) — sin lecturas
  reales en `UNS_SILVER`, el modelo no tiene nada que devolver por muy
  bien que funcione el tool-calling.
