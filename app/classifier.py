"""LLM-based ticket classification with a safe fallback.

The classifier asks Claude to return strict JSON with ``category``, ``priority``
and ``tags``. Any problem (missing API key, network error, malformed output,
invalid values) results in :data:`FALLBACK_CLASSIFICATION` so the API never
crashes and never propagates SDK exceptions to the caller.
"""

from __future__ import annotations

import json
import os

from app.models import ALLOWED_CATEGORIES, ALLOWED_PRIORITIES

FALLBACK_CLASSIFICATION = {"category": "question", "priority": "P3", "tags": []}

MODEL = "claude-haiku-4-5-20251001"

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

    Never raises: returns FALLBACK_CLASSIFICATION on any error.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return dict(FALLBACK_CLASSIFICATION)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Título: {title}\n\nDescripción: {description}",
                }
            ],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return _coerce(json.loads(text))
    except Exception:
        # Network failure, missing SDK, malformed JSON, etc. → safe fallback.
        return dict(FALLBACK_CLASSIFICATION)
