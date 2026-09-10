# Fase 8 — Observabilidad opcional: Langfuse sin riesgo

Con la API completa (Fase 7), esta fase añade trazabilidad — qué tool se
llamó, con qué argumentos, cuánto tardó — sin que su presencia o ausencia
cambie el comportamiento del chat. Es una fase corta, pero el patrón que
enseña (una dependencia externa opcional que nunca puede romper el
camino principal) es tan importante como cualquiera de las anteriores.

---

## 1. El requisito que dicta todo el diseño de esta capa

Antes de escribir código, fija la regla que no se puede romper, en
ningún punto: **si Langfuse no está disponible (apagado, mal configurado,
la red caída), el chat tiene que seguir funcionando exactamente igual.**
Esto no es una posibilidad remota en este proyecto — el spec documenta
que `INFRAESTRUCTURA/` (donde vive Langfuse) es un prerequisito externo
que todavía **no existe** como stack desplegado (ver `CLAUDE.md`,
descripción de `UNS_COPILOT`). Si construyeras esta capa asumiendo que
Langfuse siempre responde, el servicio entero dejaría de funcionar el día
que lo despliegues, porque su dependencia opcional literalmente no está
ahí.

De este único requisito sale, sin necesidad de decidirlo aparte, la forma
que tiene que tener el código: cualquier llamada a Langfuse va envuelta
en su propio `try/except`, y el resultado de fallar es "no hay traza",
nunca "el turno falla".

---

## 2. El patrón: una interfaz con dos implementaciones — ya lo conoces

Si esto te suena al `LLMProvider` de la Fase 4, es exactamente el mismo
patrón, aplicado a un problema distinto: una interfaz mínima, una
implementación real y una que no hace nada.

```python
from typing import Protocol

class Tracer(Protocol):
    def span(self, span_name: str, **metadata) -> AsyncIterator[None]: ...

class NoOpTracer:
    """Default tracer: does nothing, costs nothing, never touches the
    network."""
    @asynccontextmanager
    async def span(self, span_name: str, **metadata) -> AsyncIterator[None]:
        yield
```

`NoOpTracer` es la implementación por defecto — y fíjate que no es un
caso especial ni un `if langfuse_enabled: ... else: pass` disperso por el
código que la usa (`run_turn`, Fase 6): es una implementación completa de
la misma interfaz, así que el código que la consume (`async with
tracer.span(...): ...`) no necesita saber ni preguntarse si Langfuse está
activo. La decisión "¿tracing real o no-op?" se toma en un único sitio
(sección 4), no repartida por todo el bucle agente.

---

## 3. La implementación real: `LangfuseTracer`, fail-open en cada punto de contacto

```python
class LangfuseTracer:
    def __init__(self, public_key: str, secret_key: str, host: str):
        from langfuse import Langfuse   # import perezoso — ver más abajo
        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    @asynccontextmanager
    async def span(self, span_name: str, **metadata) -> AsyncIterator[None]:
        trace = None
        try:
            trace = self._client.trace(name=span_name, metadata=metadata)
        except Exception as exc:
            logger.warning("Langfuse trace() failed, continuing without tracing: %s", exc)

        try:
            yield
        finally:
            if trace is not None:
                try:
                    trace.update(output="ok")
                except Exception as exc:
                    logger.warning("Langfuse trace.update() failed: %s", exc)
```

Al construir esto tú mismo, verás que el requisito de la sección 1 obliga
a **tres** puntos de fallo distintos, no uno solo — y cada uno necesita su
propio `try/except`, porque un solo `try` envolviendo todo el método
tendría un problema: si pones el `yield` dentro del mismo `try` que
envuelve `self._client.trace(...)`, una excepción real que ocurra
**dentro** del bloque `async with tracer.span(...):` (es decir, un fallo
del propio `run_turn`, no de Langfuse) también quedaría atrapada por ese
`except` — y la estarías ocultando o tratándola como "fallo de tracing".
Eso es exactamente lo que comprueba `test_noop_tracer_swallows_exceptions_raised_inside_the_body`:
la excepción que lanza el *código del usuario* dentro del `span` tiene
que propagarse tal cual — el tracer solo protege contra sus **propios**
fallos, nunca enmascara los de quien lo usa.

Los tres puntos, en orden:

1. **`self._client.trace(...)` puede fallar** (red caída, credenciales
   mal puestas) — capturado, `trace` se queda en `None`, se continúa sin
   traza.
2. **El código real del turno (`yield`) se ejecuta fuera de cualquier
   `try` de Langfuse** — si falla, la excepción sube tal cual, sin
   interferencia del tracer.
3. **`trace.update(...)` en el `finally` también puede fallar** por su
   cuenta, independientemente de si el paso 1 funcionó — capturado por
   separado.

**El import perezoso de `langfuse` dentro de `__init__`**, no al
principio del archivo: significa que **importar el módulo
`observability.py` nunca requiere tener el paquete `langfuse` instalado
y funcionando**, solo instanciar `LangfuseTracer` lo necesita — y eso
solo ocurre si `LANGFUSE_ENABLED=true`. Con Langfuse apagado (el default,
Fase 2), ese `import langfuse` nunca se ejecuta.

---

## 4. Dónde se decide cuál de las dos implementaciones usar

```python
@lru_cache
def get_tracer() -> Tracer:
    if not settings.langfuse_enabled:
        return NoOpTracer()
    try:
        return LangfuseTracer(
            public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key, host=settings.langfuse_host,
        )
    except Exception as exc:
        logger.warning("Failed to construct LangfuseTracer, falling back to NoOpTracer: %s", exc)
        return NoOpTracer()
```

El detalle que hay que construir con cuidado: **incluso construir
`LangfuseTracer()` puede fallar** (host mal formado, librería no
instalada correctamente) — y ese fallo, otra vez, cae a `NoOpTracer()` en
vez de propagarse. Es la misma disciplina de la Fase 4 y la Fase 5
("nunca confíes ciegamente"), aplicada aquí a una dependencia externa en
vez de a la entrada del modelo: **nada de lo que pueda fallar fuera de tu
control debe poder tumbar el servicio**, ni siquiera en el momento de
construir el cliente.

`@lru_cache` aquí cumple el mismo papel que en `_default_llm_provider`
(Fase 7): decide una sola vez, no en cada turno, qué tracer usar.

---

## 5. Cómo se usa desde el bucle agente (ya lo viste, ahora en contexto)

En `run_turn` (Fase 6), envolviendo el turno completo y cada llamada de
tool por separado:

```python
tracer = get_tracer()
async with tracer.span("run_turn", conversation_id=conversation_id):
    ...
    for call in response.tool_calls:
        async with tracer.span("tool_call", name=call.name, arguments=call.arguments):
            result = await execute_tool(...)
```

Dos spans anidados: uno para el turno completo, uno por cada tool call
dentro de él — así una traza en Langfuse (cuando está activo) muestra la
jerarquía real: pregunta → llamada al modelo → tool call 1 → tool call 2
→ respuesta final, con tiempos por cada paso. Fíjate que este código de
`run_turn` **no cambia en absoluto** entre `NoOpTracer` y
`LangfuseTracer` — es la prueba de que la interfaz de la sección 2 cumple
su función: el bucle agente ni sabe ni le importa cuál de las dos está
activa.

---

## 6. Checkpoint de esta fase

**Sin Langfuse en absoluto** — el caso que tiene que funcionar siempre:

```bash
cd UNS_COPILOT/backend
pytest tests/test_observability.py -v
```

**Salida esperada:** en verde. `test_noop_tracer_span_never_raises`
confirma que usar el tracer no-op no toca la red ni lanza nada;
`test_noop_tracer_swallows_exceptions_raised_inside_the_body` confirma la
distinción crítica de la sección 3 — el tracer nunca enmascara un fallo
real del código que envuelve.

**Con Langfuse desactivado, de punta a punta** (ya con `LANGFUSE_ENABLED=false`,
el valor por defecto de la Fase 2):

```bash
curl -X POST http://localhost:8002/conversations/1/messages -H "Content-Type: application/json" -d '{"text": "hola"}'
```

**Salida esperada:** una respuesta normal — nada distinto de la Fase 7,
que es exactamente el punto: la presencia de esta capa no debería
notarse en absoluto cuando está apagada.

**Si tienes acceso a un Langfuse real** (fuera del alcance de este repo —
ver `CLAUDE.md`, sección sobre `INFRAESTRUCTURA/`): activa
`LANGFUSE_ENABLED=true` y las tres claves en `.env`, reinicia, repite la
petición anterior, y confirma en la UI de Langfuse que aparece una traza
con el span `run_turn` y, si el modelo llamó a alguna tool, un span
`tool_call` anidado con el nombre y los argumentos exactos que se
usaron.

---

## 7. Qué sigue

Con las ocho capas de código construidas y verificadas cada una por
separado, la Fase 9 las prueba **todas juntas, con todo vivo**: Docker,
Postgres real, Ollama real — la primera vez en todo el tutorial en que no
hay ningún mock ni ningún `Fake` de por medio.
