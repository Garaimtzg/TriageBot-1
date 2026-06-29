"""LLM-based ticket classification with a safe fallback.

Uses the OpenAI SDK pointed at OpenRouter (model ``gpt-oss-120b``) as mandated by
the project stack. The model is asked to return strict JSON with ``category``,
``priority`` and ``tags``. Any problem (missing API key, network error,
malformed output, invalid values) results in :data:`FALLBACK_CLASSIFICATION` so
the API never crashes and never propagates SDK exceptions to the caller.
"""

from __future__ import annotations

import json
import os

from app.models import ALLOWED_CATEGORIES, ALLOWED_PRIORITIES

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

    Never raises: returns a copy of FALLBACK_CLASSIFICATION on any error.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return dict(FALLBACK_CLASSIFICATION)

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
        return _coerce(json.loads(text))
    except Exception:
        # Network failure, missing SDK, malformed JSON, etc. → safe fallback.
        return dict(FALLBACK_CLASSIFICATION)
