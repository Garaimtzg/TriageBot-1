# TriageBot Template

Repo plantilla para el bootcamp **Prompt & Commit: Desarrollo de aplicaciones con IA Generativa**.

Durante el bootcamp construiréis una aplicación web interna para clasificar tickets de soporte usando IA generativa.

## Qué vais a construir

TriageBot permite:

- Registrar usuarios e iniciar sesión (email + contraseña). **Toda la app
  requiere sesión**: lo primero es crear una cuenta o iniciar sesión.
- Crear tickets con `title` y `description`, **asignando uno o varios
  responsables** (usuarios registrados; mínimo uno desde el formulario).
- Clasificarlos automáticamente con un LLM (gpt-oss-120b vía OpenRouter) en:
  - `category`: `bug`, `feature_request`, `question`, `urgent`
  - `priority`: `P1`, `P2`, `P3`
  - `tags`: lista de etiquetas cortas.
- Persistirlos en SQLite.
- Verlos en un tablero web con filtros, incluyendo la columna de responsables, y
  reasignar responsables desde el detalle del ticket.
- Ejecutar tests automáticos y CI en GitHub Actions.

## Stack obligatorio

| Capa | Herramienta |
|---|---|
| Lenguaje | Python 3.11+ |
| Backend | FastAPI |
| Datos | SQLite |
| Frontend | HTML + HTMX + Tailwind CDN |
| LLM | gpt-oss-120b vía OpenRouter (SDK de OpenAI) |
| Tests | pytest |
| CI/CD | GitHub Actions |
| IDE + IA | VS Code + Claude Code |

## Setup local

```bash
git clone https://github.com/<tu-usuario>/triagebot-template.git
cd triagebot-template
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` y añade tu API key y el secreto de sesión:

```bash
OPENROUTER_API_KEY=sk-or-...
# Secreto para firmar las cookies de sesión (login). Genera uno aleatorio:
#   python -c "import secrets; print(secrets.token_hex(32))"
SESSION_SECRET=...
```

> Si no defines `SESSION_SECRET` la app arranca con un valor de desarrollo por
> defecto (suficiente para tests/local, **no** para producción).

Comprueba que `.env` está ignorado por Git:

```bash
git status
```

`.env` **no debe aparecer**.

## Configuración (`config.yaml`)

Todos los parámetros ajustables viven en [`config.yaml`](config.yaml) — **no hay
valores hardcodeados en el código** (ni siquiera el *prompt* del clasificador):

- `ticket`: catálogo de `categories`, `priorities`, `statuses` y `default_status`.
- `validation`: longitudes máximas de `title`/`description` y reglas de los
  usuarios (`name_max_len`, `email_max_len`, `password_min_len`, `password_max_len`).
- `database`: nombre de la env var de la URL (`url_env`) y `default_url`.
- `classifier`: `model`, `base_url`, `max_tokens`, `system_prompt`, valor de
  `fallback` y la heurística por palabras clave. `api_key_env` indica el
  **nombre** de la variable de entorno con la API key.

`config.yaml` **no contiene secretos**: la API key se sigue leyendo de la
variable de entorno indicada en `classifier.api_key_env` (por defecto
`OPENROUTER_API_KEY`). La ruta del fichero puede sobreescribirse con la env var
`CONFIG_PATH`.

## Ejecutar tests

```bash
pytest -v
```

Al clonar el repo plantilla, los tests de aceptación deben fallar. Eso es lo esperado: todavía no habéis implementado TriageBot.

## Ejecutar la app

```bash
uvicorn app.main:app --reload
```

Abre:

```text
http://127.0.0.1:8000
```

## Contrato mínimo del producto

Los detalles obligatorios están en:

- [`BRIEF.md`](BRIEF.md): briefing del cliente.
- [`SPEC.md`](SPEC.md): contrato funcional recomendado.
- [`CLAUDE.md`](CLAUDE.md): instrucciones del repo para Claude Code.
- [`tests/test_acceptance.py`](tests/test_acceptance.py): los 5 tests obligatorios.

## Reglas del bootcamp

1. Lo que no acaba en GitHub no existe.
2. No se commitean API keys.
3. Commit pequeño cada 20–30 minutos.
4. Leed el diff antes de aceptar cambios de la IA.
5. Si Claude propone una dependencia, verificad que existe antes de instalarla.
6. Los tests son la red de seguridad.

## Equipo

Nombres: Garai, Manel

Metodología: `Vibe`

## Código

`234567892`
