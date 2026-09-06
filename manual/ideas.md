# Ideas: capa de IA Generativa sobre el UNS

Notas de una sesión de brainstorming (2026-09-06) sobre posibles desarrollos
que usen el sistema UNS existente (`UNS_MANAGER` → `UNS_HISTORIAN` →
`UNS_SILVER` → `UNS_DASHBOARD`) como base práctica para formación en IA
Generativa. Pendientes de diseñar/priorizar — se guardan aquí para retomarlas
más adelante.

## Enfoque general

La base actual (bronze en `UNS_HISTORIAN`, silver versionado en
`UNS_SILVER`, jerarquía ISA-95 en `UNS_MANAGER`, dashboards en
`UNS_DASHBOARD`) ya resuelve lo más difícil para aplicar GenAI: datos
limpios, tipados y con contexto semántico (catálogo de señales con unidad,
umbrales, versión). Eso es justo lo que un LLM necesita para no alucinar.

La propuesta es pensar en una **quinta capa ("gold" / conversacional)**
apoyada sobre todo en `UNS_SILVER` (por estar tipado y versionado) y
opcionalmente en `UNS_HISTORIAN` para detalle crudo. Cada idea es un
servicio independiente desplegable con el mismo patrón que ya sigue el
repo (`docker-compose.yml` propio, `.env.example`, scripts
`up/down/logs/status`), sin tocar los servicios existentes.

## Ideas (de menor a mayor complejidad)

### 1. Chat en lenguaje natural sobre señales
**Concepto:** *tool use / function calling* + *text-to-SQL controlado*
(nunca dejar que el LLM escriba SQL libre contra producción).

- **Reutiliza:** catálogo de `UNS_SILVER` (nombres, unidades, jerarquía),
  `silver_readings`/`silver_readings_1m/1h`, event log.
- **Qué falta:** un servicio nuevo (`UNS_COPILOT`, FastAPI) con un endpoint
  de chat. En vez de generar SQL directamente, se define un set cerrado de
  tools (`get_catalog(topic_filter)`, `query_readings(signal_key, from, to,
  agg)`, `list_events(...)`) y el LLM decide qué tool llamar y con qué
  parámetros (Claude tool use / function calling). El resultado se formatea
  como tabla + narrativa, con exportación opcional a Markdown/PDF/Excel
  para informes.
- **Por qué así y no NL→SQL directo:** texto libre a SQL es un riesgo de
  seguridad e imprecisión; con tools tipados se controla exactamente qué
  puede consultar.

### 2. Búsqueda semántica del catálogo (RAG básico)
**Concepto:** *embeddings* + *búsqueda vectorial* + *RAG*.

- **Problema que resuelve:** en el chat del punto 1, el usuario no sabrá el
  `signal_key` exacto ("la temperatura del horno de la línea 2"). Sin esto,
  el LLM falla en el primer paso.
- **Qué falta:** añadir `pgvector` a Postgres de `UNS_SILVER` (extensión,
  no un motor nuevo), generar embeddings de la descripción/ubicación de
  cada señal del catálogo, y en el chat resolver primero "¿qué señal(es)
  encajan con esta frase?" antes de llamar a `query_readings`.
- Encaja como ejercicio justo después del chat: muestra por qué RAG
  importa (sin él, el copiloto solo funciona si el usuario ya conoce el ID
  exacto).

### 3. Asistente de triage de alarmas
**Concepto:** *agente con razonamiento sobre contexto temporal* + *salida
estructurada (structured output)*.

- **Reutiliza:** event/alarm log de `UNS_SILVER` + lecturas alrededor del
  evento.
- **Qué falta:** un worker que, al insertarse un evento, arma un prompt con
  contexto (catálogo, umbral, lecturas antes/después) y pide al LLM una
  salida estructurada (severidad interpretada, causa probable, acción
  sugerida), guardada o enviada a Slack/Teams/email.
- **Lección de formación:** contraste entre clasificación determinista (el
  umbral ya generó la alarma) y valor añadido del LLM (explicarla en
  lenguaje natural con contexto, no decidir el umbral).

### 4. Generador de informes de turno (agente programado)
**Concepto:** *pipelines agentic programados* + *grounding numérico
estricto* (evitar que el LLM "invente" cifras).

- **Reutiliza:** `silver_readings_1h`, event log, jerarquía ISA-95 para
  agrupar por línea/área.
- **Qué falta:** un cron/Node-RED flow que, al cierre de turno, calcule las
  métricas exactas en SQL (no se le piden al LLM) y solo le pida
  **redactar** el resumen a partir de esos números ya calculados. Práctica
  ideal para enseñar "dale al LLM los números, no le pidas que los
  calcule".

### 5. Copiloto conversacional sobre la jerarquía de `UNS_MANAGER` (con escritura)
**Concepto:** *human-in-the-loop* para acciones con efecto (no solo
lectura).

- **Reutiliza:** API CRUD de `enterprises/sites/areas/lines/cells` ya
  existente.
- **Qué falta:** el agente puede *proponer* una acción ("crear una celda X
  bajo la línea 2") pero la API real solo se llama tras confirmación
  explícita del usuario en el chat/UI — patrón *propose-then-execute*.
  Ejercicio perfecto para hablar de seguridad en agentes que escriben, no
  solo leen.

### 6. Curación asistida del catálogo silver
**Concepto:** *few-shot / in-context learning* + similaridad por embeddings
(reutiliza el punto 2).

- **Problema real del sistema:** cuando llega un topic nuevo en bronze sin
  entrada en el catálogo silver, hoy requiere alta manual.
- **Qué falta:** al detectar un topic desconocido, el LLM propone
  unidad/descripción/umbrales basándose en señales similares (vía
  embeddings) y ejemplos de valores recientes; un humano aprueba antes de
  escribir en el catálogo (mismo patrón propose-then-execute del punto 5).

### 7. Servidor MCP sobre todo el UNS (capstone)
**Concepto:** *estandarización de tools vía MCP*, para que cualquier
cliente LLM (Claude Desktop, Claude Code, etc.) hable con la planta sin
código ad-hoc.

- **Reutiliza:** las mismas tools definidas en el punto 1 (`get_catalog`,
  `query_readings`, `list_events`) más las de `UNS_DASHBOARD` (crear
  dashboards/gráficas).
- **Qué falta:** envolver esas funciones como un servidor MCP (`UNS_MCP`)
  en vez de (o además de) el endpoint de chat propio. Buen cierre de
  formación: una vez definidas bien las tools, exponerlas por API propia o
  por MCP es prácticamente el mismo trabajo, y MCP da interoperabilidad
  gratis.

### 8. Módulo de evaluación y guardrails
**Concepto:** *testing de prompts/agentes*, detección de alucinación,
coste/latencia modelo grande vs pequeño.

- Una vez exista el punto 1 o 7: crear un set de preguntas con respuesta
  esperada (ground truth ya sacado por SQL directo) y comparar la
  respuesta del agente contra ella — introduce la idea de "regression
  tests" para features de IA, no solo para código.

## Progresión sugerida para formación

1 → 2 → 3 → 4 (fundamentos: tool use, RAG, structured output, grounding
numérico)
5 → 6 (agentes que escriben, human-in-the-loop)
7 → 8 (estandarización con MCP, evaluación responsable)
