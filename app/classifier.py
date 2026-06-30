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

from app.config import get_config
from app.models import ALLOWED_CATEGORIES, ALLOWED_PRIORITIES

logger = logging.getLogger("triagebot.classifier")

_cfg = get_config()["classifier"]

API_KEY_ENV = _cfg["api_key_env"]
MODEL = _cfg["model"]
BASE_URL = _cfg["base_url"]
MAX_TOKENS = int(_cfg["max_tokens"])
SYSTEM_PROMPT = _cfg["system_prompt"]

FALLBACK_CLASSIFICATION = dict(_cfg["fallback"])

# Heuristic settings (keyword tables, precedence and defaults) come from config.
_HEURISTIC = _cfg["heuristic"]
_HEURISTIC_RULES = _HEURISTIC["rules"]
_HEURISTIC_DEFAULT = _HEURISTIC["default"]
_MAX_TAGS = int(_HEURISTIC["max_tags"])
_TAG_KEYWORDS = _HEURISTIC["tag_keywords"]


def _normalize(text: str) -> str:
    """Lowercase and strip accents so keyword matching is robust."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def _derive_tags(haystack: str) -> list[str]:
    """Return up to ``_MAX_TAGS`` descriptive tags found in the (normalized) text."""
    tags: list[str] = []
    for keyword, tag in _TAG_KEYWORDS.items():
        if keyword in haystack and tag not in tags:
            tags.append(tag)
        if len(tags) == _MAX_TAGS:
            break
    return tags


def _heuristic_classify(title: str, description: str) -> dict:
    """Rule-based classification used when the LLM is unavailable.

    Rules and their precedence come from ``config.yaml`` (the first matching rule
    wins), keeping blocking incidents from being buried under "question".
    """
    haystack = _normalize(f"{title} {description}")
    tags = _derive_tags(haystack)

    for rule in _HEURISTIC_RULES:
        if any(keyword in haystack for keyword in rule["keywords"]):
            return {"category": rule["category"], "priority": rule["priority"], "tags": tags}

    # Nothing matched: safe default, but keep any tags we found.
    return {
        "category": _HEURISTIC_DEFAULT["category"],
        "priority": _HEURISTIC_DEFAULT["priority"],
        "tags": tags,
    }


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
    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        logger.info(
            "Clasificación SIN modelo: no hay %s; se usa la heurística interna.",
            API_KEY_ENV,
        )
        return _heuristic_classify(title, description)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=BASE_URL)
        completion = client.chat.completions.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
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
