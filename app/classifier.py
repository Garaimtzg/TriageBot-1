"""Ticket classification: LLM-first with a rule-based fallback.

Primary path uses the OpenAI SDK pointed at OpenRouter (model ``gpt-oss-120b``)
as mandated by the project stack, asking for strict JSON with ``category``,
``priority`` and ``tags``.

When the LLM is unavailable (missing ``OPENROUTER_API_KEY``, network error,
SDK not installed, malformed output, ...) we do **not** dump every ticket into
the same flat constant. Instead :func:`_heuristic_classify` derives a sensible
``category``/``priority``/``tags`` from keywords in the title/description, so an
urgent incident and a routine question no longer look identical. The constant
:data:`FALLBACK_CLASSIFICATION` remains as the last-resort default used by the
endpoint if even the heuristic somehow fails.
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata

from app.models import ALLOWED_CATEGORIES, ALLOWED_PRIORITIES

logger = logging.getLogger("triagebot.classifier")

FALLBACK_CLASSIFICATION = {"category": "question", "priority": "P3", "tags": []}

MODEL = "openai/gpt-oss-120b"
BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = (
    "Eres un clasificador de tickets de soporte. Respondes SIEMPRE con un único "
    "objeto JSON, sin texto adicional, con esta forma exacta:\n"
    '{"category": <"bug"|"feature_request"|"question"|"urgent">, '
    '"priority": <"P1"|"P2"|"P3">, "tags": [<strings cortos>]}\n'
    "P1 es la prioridad más alta. Devuelve una lista corta de tags útiles "
    "(puede estar vacía)."
)

# --- Heuristic keyword tables (accent-insensitive matching) -----------------

# Signals of a critical/blocking incident → category "urgent", priority P1.
_URGENT_KEYWORDS = (
    "urge",
    "urgent",
    "critic",
    "caido",
    "caida",
    "se cae",
    "no funciona",
    "no carga",
    "no puedo trabajar",
    "no podemos trabajar",
    "bloquea",
    "bloqueado",
    "bloqueante",
    "parado",
    "parados",
    "produccion",
    "demo",
    "cuanto antes",
    "lo antes posible",
    "inmediat",
    "perdida de datos",
    "fuga de datos",
    "brecha",
    "todos los usuarios",
)

# Signals of a defect → category "bug", priority P2.
_BUG_KEYWORDS = (
    "error",
    "fallo",
    "falla",
    "fallando",
    "bug",
    "roto",
    "rota",
    "no muestra",
    "no aparece",
    "no guarda",
    "no se guarda",
    "no deja",
    "incorrect",
    "mal calculad",
    "se cierra",
    "se congela",
    "pantalla en blanco",
    "excepcion",
    "500",
    "no responde",
    "duplicad",
)

# Signals of a feature/enhancement request → category "feature_request", P3.
_FEATURE_KEYWORDS = (
    "añadir",
    "agregar",
    "permitir",
    "poder ",
    "poder\n",
    "me vendria bien",
    "nos vendria bien",
    "seria util",
    "estaria bien",
    "estaria genial",
    "podriais",
    "se podria",
    "necesitamos poder",
    "personalizar",
    "exportar",
    "filtro por",
    "filtrar por",
    "soporte para",
    "integrar",
    "integracion",
    "mejorar",
    "sugerencia",
    "propuesta",
)

# Signals of a question → category "question", priority P3.
_QUESTION_KEYWORDS = (
    "como ",
    "como se",
    "donde ",
    "cuando ",
    "que politica",
    "se puede",
    "puedo ",
    "es posible",
    "duda",
    "consulta",
    "no se si",
    "necesito saber",
    "quisiera saber",
    "?",
)

# Optional descriptive tags derived from the text.
_TAG_KEYWORDS = {
    "pdf": "pdf",
    "exportar": "export",
    "login": "login",
    "sesion": "login",
    "contrasena": "password",
    "password": "password",
    "permiso": "permissions",
    "acceso": "access",
    "filtro": "filtros",
    "movil": "movil",
    "correo": "email",
    "email": "email",
    "informe": "informes",
    "factura": "facturacion",
    "pago": "pagos",
}


def _normalize(text: str) -> str:
    """Lowercase and strip accents so keyword matching is robust."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def _derive_tags(haystack: str) -> list[str]:
    """Return up to three descriptive tags found in the (normalized) text."""
    tags: list[str] = []
    for keyword, tag in _TAG_KEYWORDS.items():
        if keyword in haystack and tag not in tags:
            tags.append(tag)
        if len(tags) == 3:
            break
    return tags


def _heuristic_classify(title: str, description: str) -> dict:
    """Rule-based classification used when the LLM is unavailable.

    Precedence: urgent > bug > feature_request > question (default). This keeps
    blocking incidents from being buried under the generic "question" bucket.
    """
    haystack = _normalize(f"{title} {description}")
    tags = _derive_tags(haystack)

    def _has(keywords: tuple[str, ...]) -> bool:
        return any(keyword in haystack for keyword in keywords)

    if _has(_URGENT_KEYWORDS):
        return {"category": "urgent", "priority": "P1", "tags": tags}
    if _has(_BUG_KEYWORDS):
        return {"category": "bug", "priority": "P2", "tags": tags}
    if _has(_FEATURE_KEYWORDS):
        return {"category": "feature_request", "priority": "P3", "tags": tags}
    if _has(_QUESTION_KEYWORDS):
        return {"category": "question", "priority": "P3", "tags": tags}

    # Nothing matched: safe default, but keep any tags we found.
    return {"category": "question", "priority": "P3", "tags": tags}


def _coerce(result: dict) -> dict:
    """Validate the model output, falling back field-by-field when needed."""
    category = result.get("category")
    priority = result.get("priority")
    tags = result.get("tags", [])

    if category not in ALLOWED_CATEGORIES:
        category = FALLBACK_CLASSIFICATION["category"]
    if priority not in ALLOWED_PRIORITIES:
        priority = FALLBACK_CLASSIFICATION["priority"]
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        tags = []

    return {"category": category, "priority": priority, "tags": tags}


def classify_ticket(title: str, description: str) -> dict:
    """Classify a support ticket into category, priority and tags.

    LLM-first; falls back to a keyword heuristic (never the same flat constant
    for every ticket). Never raises.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.info(
            "Clasificación SIN modelo: no hay OPENROUTER_API_KEY; "
            "se usa la heurística interna."
        )
        return _heuristic_classify(title, description)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=BASE_URL)
        completion = client.chat.completions.create(
            model=MODEL,
            max_tokens=256,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Título: {title}\n\nDescripción: {description}",
                },
            ],
        )
        text = completion.choices[0].message.content or ""
        result = _coerce(json.loads(text))
        logger.info("Clasificación CON modelo: usado %s vía OpenRouter.", MODEL)
        return result
    except Exception:
        # Network failure, missing SDK, malformed JSON, etc. → heuristic fallback.
        logger.warning(
            "Clasificación SIN modelo: el modelo no está disponible; "
            "se usa la heurística interna."
        )
        return _heuristic_classify(title, description)
