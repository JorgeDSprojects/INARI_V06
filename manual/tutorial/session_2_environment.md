# Sesión 2 — Entorno de desarrollo y despliegue

En esta sesión preparamos todo lo necesario para ejecutar **UNS Copilot** en tu máquina local.

---

## 1️⃣ Prerrequisitos del host
| Herramienta | Versión mínima | Cómo instalar (Windows) |
|-------------|----------------|--------------------------|
| **Docker Desktop** | 26.0+ (incluye Compose v2) | Descarga https://www.docker.com/products/docker-desktop/ y sigue el instalador. Habilita *Use WSL 2 based engine*. |
| **PowerShell 5.1** | — | Ya viene con Windows. |
| **Python 3.12** | 3.12.x | `winget install Python.Python.3.12` o instala desde python.org. Sólo se usa para pruebas unitarias, la imagen Docker ya incluye Python. |
| **Ollama** (modelo con tool‑calling) | 0.3+ | Descarga https://ollama.com/download/OllamaSetup.exe, instala y ejecuta `ollama serve`. |
| **Git** (opcional) | 2.40+ | `winget install Git.Git`. |

> **Tip**: después de instalar Docker, abre una terminal PowerShell y ejecuta `docker version` y `docker compose version` para confirmar que todo está bien.

---

## 2️⃣ Variables de entorno
UNS Copilot usa un archivo `.env`. Copia el ejemplo y edita los valores.
```powershell
# Desde la raíz del proyecto
cp UNS_COPILOT\.env.example UNS_COPILOT\.env
notepad UNS_COPILOT\.env   # edita con tus valores
```
### Parámetros críticos que debes ajustar
| Variable | Qué contiene | Ejemplo típico |
|----------|--------------|----------------|
| `POSTGRES_USER` | Usuario de la BD de Copilot | `copilot_user` |
| `POSTGRES_PASSWORD` | Contraseña del BD de Copilot | `superSecret123` |
| `POSTGRES_DB` | Nombre de la BD de Copilot | `copilot_db` |
| `SILVER_DATABASE_URL` | Cadena de conexión **solo lectura** a `uns_silver_postgres` | `postgresql://silver_user:silver_pwd@uns_silver_postgres:5432/silver_db` |
| `LLM_BASE_URL` | URL del endpoint OpenAI‑compatible de Ollama. Desde Docker se usa `http://host.docker.internal:11434/v1`. |
| `LLM_MODEL` | Modelo con tool‑calling (p.ej. `qwen2.5:14b`). |
| `LLM_API_KEY` | Ollama no necesita clave, pero el SDK de OpenAI exige un valor; usa `dummy`. |
| `LANGFUSE_ENABLED` | `true`/`false` – activar trazas opcionalmente. |
| `MAX_ITERATIONS`, `MAX_HISTORY_MESSAGES` | Límites del bucle agente; valores por defecto (5 y 20) funcionan. |

Guarda y cierra el archivo.

---

## 3️⃣ Levantar la pila completa con scripts auxiliares
En `UNS_COPILOT/scripts` encontrarás *sh* scripts que siguen la convención del repositorio (`set -euo pipefail`).
### 3.1 Iniciar todo (backend + su base de datos)
```powershell
# Desde la raíz del proyecto
.\UNS_COPILOT\scripts\up.sh
```
Esto ejecuta:
```text
docker compose -f ../docker-compose.yml -f UNS_COPILOT/docker-compose.yml up -d
```
- Levanta `uns_copilot_postgres` y `uns_copilot_backend`.
- El backend se une a la red `uns_net` (para leer `uns_silver_postgres`).
### 3.2 Verificar estado
```powershell
.\UNS_COPILOT\scripts\status.sh
```
Deberías ver algo como:
```
Name                     Command               State          Ports
-------------------------------------------------------------------
uns_copilot_backend      "uvicorn app.main …"   Up      0.0.0.0:8002->8000/tcp
uns_copilot_postgres     "docker-entrypoint.s…" Up      5432/tcp
```
Si algún contenedor está `Exited`, inspecciónalo con:
```powershell
.\UNS_COPILOT\scripts\logs.sh
```
### 3.3 Detener y reiniciar (cuando necesites)
```powershell
.\UNS_COPILOT\scripts\down.sh      # para limpiar
.\UNS_COPILOT\scripts\restart.sh   # down + up
```

---

## 4️⃣ Configurar y probar Ollama
1. **Instalar y lanzar Ollama** (si no lo hiciste ya). En una terminal distinta:
   ```powershell
   ollama serve   # deja el proceso corriendo
   ```
2. **Descargar un modelo que soporte tool‑calling**:
   ```powershell
   ollama pull qwen2.5:14b   # o llama a otro modelo compatible
   ```
3. **Comprobar que el endpoint está disponible**:
   ```powershell
   curl http://localhost:11434/v1/models
   ```
   Deberías obtener un JSON con el modelo que descargaste.
4. **Actualizar `.env`** con `LLM_MODEL=qwen2.5:14b` y `LLM_BASE_URL=http://host.docker.internal:11434/v1`.
5. **Reiniciar el backend** para que lea los cambios:
   ```powershell
   .\UNS_COPILOT\scripts\restart.sh
   ```

---

## 5️⃣ Primeras pruebas de la API (curl)
Con los contenedores arriba y Ollama listo, prueba los endpoints básicos.
### 5.1 Listar usuarios (seed de `init.sql` crea 2 usuarios)
```powershell
curl http://localhost:8002/users/
```
Respuesta esperada (JSON):
```json
[{"id":1,"display_name":"alice"},{"id":2,"display_name":"bob"}]
```
### 5.2 Crear una conversación para el usuario 1
```powershell
curl -X POST http://localhost:8002/conversations/ \
     -H "Content-Type: application/json" \
     -d "{\"user_id\":1}"
```
Devolverá algo como:
```json
{"id":1,"user_id":1,"title":null,"created_at":"2026-09-10T12:34:56.789Z","updated_at":"2026-09-10T12:34:56.789Z"}
```
### 5.3 Enviar un mensaje que invoque una herramienta
```powershell
curl -X POST http://localhost:8002/conversations/1/messages \
     -H "Content-Type: application/json" \
     -d "{\"text\":\"¿Cuál es el valor actual del sensor temperature?\"}"
```
Deberías recibir una respuesta JSON con `assistant` que contiene el valor (p.ej. `"23.4 °C"`).

---

## 6️⃣ Opcional: habilitar Langfuse para trazas
Si dispones del stack *Langfuse* (en la carpeta `INFRAESTRUCTURA/` de otro repo), habilítalo:
```text
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://host.docker.internal:3000
LANGFUSE_PUBLIC_KEY=your_pub_key
LANGFUSE_SECRET_KEY=your_sec_key
```
Reinicia el backend y abre la UI de Langfuse; verás un trace por cada turno (`run_turn`, `execute_tool`). Si el endpoint no está disponible, la aplicación sigue funcionando sin trazas.

---

## 7️⃣ Resumen rápido de los comandos
```powershell
# 1. Copiar y editar .env
cp UNS_COPILOT\.env.example UNS_COPILOT\.env
notepad UNS_COPILOT\.env

# 2. Levantar stack
.\UNS_COPILOT\scripts\up.sh
.\UNS_COPILOT\scripts\status.sh

# 3. Probar API
curl http://localhost:8002/users/
curl -X POST http://localhost:8002/conversations/ -H "Content-Type: application/json" -d "{\"user_id\":1}"
curl -X POST http://localhost:8002/conversations/1/messages -H "Content-Type: application/json" -d "{\"text\":\"¿qué señales de temperature existen?\"}"

# 4. Detener / reiniciar
.\UNS_COPILOT\scripts\down.sh
.\UNS_COPILOT\scripts\restart.sh
```

---

## 8️⃣ Qué sigue
En la **Sesión 3** estudiaremos el **modelado de datos** de la base de datos propia de Copilot (`users`, `conversations`, `messages`). Verás el script `init.sql`, cómo se inicializa y cómo añadir nuevas columnas de forma segura.

---

**Conclusión**
- Prerrequisitos instalados.  
- `.env` configurado.  
- Contenedores levantados con `up.sh`.  
- Ollama con modelo tool‑calling listo.  
- Primeras pruebas de la API funcionan.

Cuando estés listo, avísame y pasamos a la **Sesión 3 — Modelado de datos**.
