# Fase 4 — La capa LLM: hablar con un modelo sin acoplarte a uno

Con el modelo de datos verificado (Fase 3), toca la primera pieza que
habla de verdad con un LLM. Pero **todavía no** las tools ni el bucle
agente — solo el mecanismo de "mandar mensajes, recibir una respuesta
normalizada", aislado de todo lo demás. Es la fase más corta de las diez,
y la más fácil de verificar: no necesita ni Postgres ni Docker.

---

## 1. Por qué esta capa se construye antes que las tools o el bucle

Mirando el diagrama de la Fase 1, `LLMProvider` está "en medio" — parece
que debería ir después. Se construye ahora por una razón práctica: **el
bucle agente (Fase 6) y las tools (Fase 5) necesitan poder probarse sin
un Ollama real corriendo**, y eso solo es posible si primero existe una
interfaz que se pueda sustituir por una versión falsa. Si escribieras el
bucle primero, acoplado directamente al SDK de `openai`, no tendrías
forma de testearlo sin una red y un modelo real — cada test tardaría
segundos (o minutos) y dependería de que Ollama estuviera arriba y el
modelo cargado. Construir la interfaz antes que sus consumidores es lo
que hace posible todo lo demás.

---

## 2. La interfaz: qué necesita ver el resto del programa, y nada más

Antes de mirar el código, la pregunta de diseño es: ¿cuál es el contrato
mínimo entre "el bucle agente" y "un proveedor de LLM"? Necesitas mandar
mensajes + tools disponibles, y recibir de vuelta: texto (si ya terminó)
o peticiones de tool (si no). Eso, y nada específico de ningún proveedor
concreto:

```python
# app/llm/base.py
from abc import ABC, abstractmethod
from pydantic import BaseModel

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class ChatResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] = []
    finish_reason: str  # "tool_calls" | "stop" | "length" | ...

class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse: ...
```

El comentario del archivo real explica la asimetría de diseño, que
conviene entender ahora porque no es intuitiva: la **salida**
(`ChatResponse`/`ToolCall`) es agnóstica de proveedor a propósito —
*"no adapter may leak its own wire format ... back past this boundary"*
— pero la **entrada** (`messages`, `tools`) sigue deliberadamente el
formato de OpenAI, sin neutralizar. ¿Por qué no neutralizar también la
entrada? Porque el esquema de la tabla `messages` (Fase 3) ya se diseñó
para calcar ese formato — neutralizar la entrada añadiría una capa de
traducción para un problema que no existe todavía (solo hay un proveedor
compatible con OpenAI). El día que se añada un adaptador realmente
distinto (Anthropic, con bloques `tool_use`/`tool_result` en vez de un
array `tool_calls`), esa traducción de entrada vive dentro de *ese*
adaptador nuevo — YAGNI aplicado con criterio, no evitado por pereza.

`async def chat(...)`, no `def chat(...)`: toda la aplicación es async
(SQLAlchemy async en la Fase 3, FastAPI async en la Fase 7) — una llamada
de red bloqueante a mitad de un endpoint async congelaría el worker
entero, no solo esa petición.

---

## 3. La implementación real: `OllamaAdapter`

```python
from openai import AsyncOpenAI
from app.llm.base import ChatResponse, LLMProvider, ToolCall

class OllamaAdapter(LLMProvider):
    def __init__(self, base_url: str, api_key: str, model: str):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        response = await self._client.chat.completions.create(
            model=self._model, messages=messages, tools=tools or None,
        )
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        for raw_call in (message.tool_calls or []):
            try:
                arguments = json.loads(raw_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}   # ver sección siguiente — por qué no lanzar aquí
            tool_calls.append(ToolCall(id=raw_call.id, name=raw_call.function.name, arguments=arguments))

        return ChatResponse(content=message.content, tool_calls=tool_calls, finish_reason=choice.finish_reason)
```

Punto clave de diseño, fácil de pasar por alto: **este adaptador usa el
SDK de `openai`**, no un cliente HTTP propio ni un SDK de Ollama. Es
correcto porque Ollama expone un endpoint compatible con
`/v1/chat/completions` — el mismo wire format que OpenAI. Esto significa
que este mismo adaptador, sin tocarlo, sirve también para apuntar a
OpenAI de verdad o a OpenRouter cambiando solo `base_url`/`api_key`/
`model` — el spec lo dice explícitamente (Sección 3): *"Ollama, OpenAI,
and OpenRouter all speak this wire format"*. Solo un proveedor
estructuralmente distinto (Anthropic-nativo) necesitaría un adaptador de
verdad nuevo.

`tools=tools or None`: cuando no hay tools que ofrecer (lista vacía), se
manda `None` en vez de `[]` — algunos backends OpenAI-compatibles tratan
`tools=[]` de forma distinta a "no hay tools" (algunos exigen `tools`
no-vacío o error). Un detalle pequeño que solo se descubre probando contra
un servidor real — por eso el checkpoint de esta fase incluye probar
contra tu Ollama de verdad, no solo con mocks.

**Por qué el `try/except` alrededor de `json.loads`:** `arguments` llega
del modelo como texto JSON — el modelo lo genera token a token, así que
puede "alucinar" JSON inválido (una coma de más, una comilla sin cerrar).
Si dejas que ese `json.JSONDecodeError` se propague, tumbas todo el turno
por un fallo que la siguiente capa (Fase 5, validación Pydantic) está
preparada para manejar con elegancia — degradar a `{}` y dejar que la
validación lo rechace con un mensaje claro es estrictamente mejor que
crashear aquí. Es la primera aparición de un principio que vas a ver una
y otra vez a partir de ahora: **nunca confíes en lo que manda el modelo,
en ningún punto de la cadena.**

---

## 4. La implementación falsa: `FakeLLMProvider`

```python
from app.llm.base import ChatResponse, LLMProvider

class FakeLLMProvider(LLMProvider):
    """Test double: returns one scripted ChatResponse per call, in order.
    Records every `messages` list it was called with for assertions."""

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        self.calls.append(messages)
        return self._responses.pop(0)
```

Diez líneas, y son las que hacen testeable todo lo que viene después. Dos
detalles de diseño con intención:

- **Guarda cada `messages` recibido en `self.calls`**, no solo devuelve la
  respuesta. Esto permite escribir aserciones sobre *lo que el bucle le
  mandó al modelo* — por ejemplo, "la segunda llamada debe incluir un
  mensaje `role: tool`" (lo verás en la Fase 6) — sin lo cual solo
  podrías comprobar la salida final, nunca el proceso intermedio.
- **`.pop(0)` sobre una lista de respuestas ya escritas a mano.** El test
  decide de antemano, guion en mano, qué va a "decir" el modelo en cada
  llamada — determinismo total, cero dependencia de red o de que un
  modelo concreto responda de una forma concreta. Si se agotan las
  respuestas programadas y el bucle llama una vez de más, `pop(0)` lanza
  `IndexError` — y eso es intencionadamente una señal de alarma en un
  test: significa que el bucle hizo más llamadas de las que el test
  anticipaba.

---

## 5. Cómo se conecta esto a FastAPI (adelanto de la Fase 7)

Una pieza que ya puedes anticipar, aunque el router completo se ve en la
Fase 7: en vez de instanciar `OllamaAdapter()` directamente dentro del
endpoint, se usa *inyección de dependencias* de FastAPI:

```python
def get_llm_provider() -> LLMProvider:
    return _default_llm_provider()   # OllamaAdapter real, cacheado con @lru_cache

# en el endpoint:
async def send_message(..., llm: LLMProvider = Depends(get_llm_provider)):
    ...
```

En los tests, `app.dependency_overrides[get_llm_provider] = lambda:
fake_llm` sustituye esa dependencia por el `FakeLLMProvider`, sin tocar
una línea del endpoint ni usar `unittest.mock.patch` sobre un módulo
global. Es la razón de fondo por la que se define la interfaz
`LLMProvider` como clase abstracta en vez de, por ejemplo, una simple
función `chat_with_ollama(...)`: FastAPI necesita algo inyectable, y una
interfaz con dos implementaciones intercambiables es exactamente eso.

---

## 6. Checkpoint de esta fase

Esta es la única fase de las diez que se verifica **sin Docker, sin
Postgres, sin `.env`** — solo Python y el paquete `openai` instalado:

```bash
cd UNS_COPILOT/backend
pytest tests/test_llm_fake.py -v
```

**Salida esperada:** en verde, comprobando que `FakeLLMProvider` devuelve
las respuestas en el orden programado y que registra cada llamada en
`.calls`.

**Verificación adicional, contra un Ollama real** (opcional pero
recomendable antes de seguir — aquí es donde se cazan sorpresas como la
de `tools=[] vs None` de la sección 3):

```bash
ollama pull qwen2.5:14b     # si no lo tienes ya
curl http://localhost:11434/v1/models   # confirma que el endpoint OpenAI-compatible responde
```

```python
# prueba suelta, sin la app todavía — python -i o un script
import asyncio
from app.llm.ollama_adapter import OllamaAdapter

async def main():
    adapter = OllamaAdapter(base_url="http://localhost:11434/v1", api_key="ollama", model="qwen2.5:14b")
    response = await adapter.chat(messages=[{"role": "user", "content": "di 'hola' y nada más"}], tools=[])
    print(response)

asyncio.run(main())
```

**Salida esperada:** un `ChatResponse` con `content` no vacío,
`tool_calls=[]`, `finish_reason="stop"` — confirmando que el adaptador
habla de verdad con tu Ollama antes de que el resto del sistema dependa
de él.

---

## 7. Qué sigue

Con la capa LLM verificada en ambas direcciones (falsa para tests, real
contra Ollama), la Fase 5 construye **las herramientas**: el contrato
JSON Schema que ve el modelo, la validación Pydantic que nunca confía en
sus argumentos, las funciones de consulta reales contra
`uns_silver_postgres`, y el despachador que conecta ambas cosas sin dejar
escapar jamás una excepción.
