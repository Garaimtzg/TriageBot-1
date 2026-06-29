"""Unit tests for the app.classifier module.

These tests do not hit the network: they exercise the fallback paths and the
output-validation logic, and stub the OpenAI client for the happy path.
"""

from __future__ import annotations

import json

from app.classifier import FALLBACK_CLASSIFICATION, _coerce, classify_ticket


def test_classify_ticket_without_api_key_returns_fallback(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    result = classify_ticket("La app no carga", "Pantalla en blanco al iniciar sesión")

    assert result == FALLBACK_CLASSIFICATION
    # Must be a copy, never the shared module-level constant.
    assert result is not FALLBACK_CLASSIFICATION


def test_coerce_replaces_invalid_values_with_fallback():
    coerced = _coerce({"category": "nope", "priority": "P9", "tags": "not-a-list"})

    assert coerced == {"category": "question", "priority": "P3", "tags": []}


def test_coerce_keeps_valid_values():
    coerced = _coerce(
        {"category": "bug", "priority": "P1", "tags": ["login", "blocker"]}
    )

    assert coerced == {"category": "bug", "priority": "P1", "tags": ["login", "blocker"]}


def test_coerce_drops_non_string_tags():
    coerced = _coerce({"category": "bug", "priority": "P2", "tags": ["ok", 123]})

    assert coerced["tags"] == []


def test_classify_ticket_parses_valid_llm_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    payload = {"category": "urgent", "priority": "P1", "tags": ["demo"]}

    class _Message:
        content = json.dumps(payload)

    class _Choice:
        message = _Message()

    class _Completion:
        choices = [_Choice()]

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, *args, **kwargs):
            return _Completion()

    monkeypatch.setattr("openai.OpenAI", _FakeClient)

    result = classify_ticket("Urge demo el viernes", "Necesitamos solución crítica")

    assert result == payload


def test_classify_ticket_never_raises_on_sdk_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class _BoomClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("OpenRouter is unavailable")

    monkeypatch.setattr("openai.OpenAI", _BoomClient)

    result = classify_ticket("titulo", "descripcion")

    assert result == FALLBACK_CLASSIFICATION
