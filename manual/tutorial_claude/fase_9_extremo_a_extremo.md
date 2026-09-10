# Fase 9 — Extremo a extremo: todo vivo, sin ningún doble

Con las ocho capas construidas y verificadas una por una — la mayoría con
`FakeLLMProvider` o mocks de sesión — esta fase es la primera en la que
**nada** está simulado: Postgres real, `uns_silver_postgres` real, Ollama
real. Es también la fase más corta de escribir, porque no hay código
nuevo que construir — es pura verificación de que el ensamblaje entero
funciona como cada fase prometió por separado.

---

## 1. Por qué esta fase va casi al final, y no fue el primer checkpoint

Si esta hubiera sido tu primera forma de probar el sistema, cualquier
fallo sería ambiguo: ¿es el bucle agente? ¿una tool? ¿el adaptador de
Ollama? ¿la base de datos? Habiendo verificado cada capa en aislamiento
(Fases 3-8), un fallo aquí tiene un espacio de búsqueda mucho más
pequeño: si todo lo anterior pasó, lo único genuinamente nuevo en esta
fase es la interacción entre piezas ya probadas por separado — y sobre
todo, el comportamiento real de un modelo real, que es la única pieza
que ninguna fase anterior pudo verificar (`FakeLLMProvider` siempre
"decide" lo que el test le programó; un modelo real decide de verdad).

Hay exactamente un test en el repo que ya cruza esta frontera sin llegar
a necesitar un LLM real: `test_e2e_chat.py`. Vale la pena entender qué
demuestra antes de pasar a la verificación completamente manual.

---

## 2. El test que ya tienes: real en todo menos en el LLM

```python
"""Every other test in this suite mocks the layer below the one under test,
so nothing else catches a failure that only appears when a genuine asyncpg
row (with datetime / Decimal values) has to be serialised into a `tool`
message. Only the LLM itself is faked here."""
```

`test_real_tool_result_is_persisted_as_valid_json` siembra una fila de
verdad en `uns_silver_postgres` (con sus tipos reales: `TIMESTAMPTZ`,
`NUMERIC`), la consulta a través del `execute_tool` real (Fase 5, sin
mockear), la persiste a través del `run_turn` real (Fase 6) vía el
endpoint HTTP real (Fase 7) — y solo sustituye el LLM por uno que ya
"sabe" que tiene que pedir `get_latest_value`. Es deliberado: es la única
pieza que **no** se puede hacer determinista sin perder el sentido de
probarla (un modelo real no siempre decide lo mismo dos veces). Todo lo
demás de la cadena es exactamente el código de producción.

```bash
cd UNS_COPILOT/backend
DATABASE_URL=postgresql+asyncpg://copilot:copilotpassword@localhost:5437/uns_copilot \
SILVER_DATABASE_URL=postgresql+asyncpg://silver:silverpassword@localhost:5436/uns_silver \
  pytest tests/test_e2e_chat.py -v
```

**Salida esperada:** en verde. Si falla, casi seguro es la Capa 3 de la
Fase 5 (normalización) — es literalmente lo que este test existe para
cazar, según su propio docstring.

---

## 3. La verificación completamente manual — con un modelo real decidiendo

Aquí ya no hay ningún `Fake` de por medio. Necesitas la pila completa
levantada, en el orden que dicta `CLAUDE.md` (cada servicio necesita la
red Docker de su aguas-arriba ya creada):

```bash
cd UNS_MANAGER && docker compose up -d
cd ../UNS_HISTORIAN && docker compose up -d
cd ../UNS_SILVER && docker compose up -d
cd ../UNS_COPILOT && cp .env.example .env && ./scripts/up.sh
```

Y al menos una señal real fluyendo por
`UNS_MANAGER → UNS_HISTORIAN → UNS_SILVER` — si no, las tools funcionan
pero no hay nada que consulten. Comprueba que hay datos antes de seguir:

```bash
docker exec uns_silver_postgres psql -U silver -d uns_silver \
  -c "SELECT topic, signal_key FROM silver_readings ORDER BY time DESC LIMIT 5;"
```

Con eso confirmado, sigue exactamente la secuencia de
`manual/es/02-probar-uns-copilot.md` (pasos 3-10 de ese manual) —
usuarios sembrados → crear conversación → preguntar por el catálogo →
valor actual → histórico → eventos → una pregunta de seguimiento sin
repetir contexto → inspeccionar el hilo completo persistido. No lo
repito aquí entero porque ya está escrito allí con el detalle de
resultado esperado en cada paso; la diferencia de perspectiva en esta
fase es que **ya sabes, capa por capa, qué código concreto produce cada
parte de lo que ves** — cuando el manual dice "el modelo debe llamar a
`get_catalog`", ahora sabes que eso pasa en `run_turn` (Fase 6) leyendo
`TOOL_SCHEMAS` (Fase 5) a través de `OllamaAdapter.chat()` (Fase 4).

**Una comprobación extra que vale la pena hacer ahora, que el manual de
uso no cubre porque asume que ya "confías" en el sistema:** fuerza
deliberadamente cada uno de los cuatro casos límite que verificaste con
mocks en la Fase 6, pero ahora con el modelo real:

| Caso a forzar | Cómo | Qué deberías ver |
|---|---|---|
| Parámetro que el modelo puede fallar | Pregunta algo ambiguo sobre una señal que no existe | Una respuesta que admite no encontrar la señal, no un error 500 — la validación de la Fase 5 rechazando con `{"error": ...}` y el modelo admitiéndolo en lenguaje natural |
| Rango demasiado amplio en crudo | Pide "todas las lecturas en crudo del último mes" de una señal | El modelo debería usar `agg='1h'` en vez de `raw`, o recibir el error del guardrail (Fase 5, `check_raw_range`) y ajustar |
| Seguimiento multi-turno | Pregunta algo, luego "¿y hace 10 minutos?" sin repetir contexto | El modelo resuelve la referencia usando el historial persistido (Fase 3/6) |
| Langfuse apagado | Con `LANGFUSE_ENABLED=false` (default) | Todo funciona igual — Fase 8, verificado ya en aislamiento, ahora confirmado que tampoco cambia nada en el camino real |

Si alguno de estos se comporta distinto de lo esperado **con un modelo
real** pero se comportaba bien con `FakeLLMProvider`, la causa casi nunca
está en tu código — está en cómo ese modelo concreto interpreta las
`description` del contrato (Fase 5, sección 5). Es la razón por la que el
spec insiste en "modelo con soporte de tool-calling" como requisito, no
cualquier modelo: no todos siguen el contrato con la misma disciplina.

---

## 4. Qué sigue

Con el sistema completo funcionando de extremo a extremo, la Fase 10 —
la última — no añade código: revisa, a propósito, todo lo que **no** se
construyó, por qué se dejó fuera deliberadamente, y cómo extenderías tú
mismo el sistema si quisieras añadirlo.
