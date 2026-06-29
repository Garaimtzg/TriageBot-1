"""Unit tests for the app.classifier module.

These tests do not hit the network: they exercise the fallback paths and the
output-validation logic, and stub the OpenAI client for the happy path.
"""

from __future__ import annotations

import json

from app.classifier import (
    FALLBACK_CLASSIFICATION,
    _coerce,
    _heuristic_classify,
    classify_ticket,
)


def test_classify_ticket_without_api_key_uses_heuristic(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    # Without a key we must NOT collapse everything into the same bucket:
    # an urgent incident and a routine question get different results.
    urgent = classify_ticket(
        "Esto urge, tenemos demo el viernes",
        "El cliente necesita una solución antes de una demo crítica",
    )
    question = classify_ticket(
        "¿Cómo solicito acceso de solo lectura?",
        "Necesito saber cómo pido ese permiso",
    )

    assert urgent["category"] == "urgent"
    assert urgent["priority"] == "P1"
    assert question["category"] == "question"
    assert question["priority"] == "P3"
    assert urgent != question


def test_heuristic_precedence_and_priorities():
    urgent = _heuristic_classify("La app no funciona", "Está caída en producción")
    bug = _heuristic_classify("El visor de PDF", "No muestra la última página")
    feature = _heuristic_classify(
        "Exportar a PDF", "Me vendría bien poder exportar informes"
    )
    question = _heuristic_classify("Política de roaming", "¿Puedo usar datos fuera?")

    assert (urgent["category"], urgent["priority"]) == ("urgent", "P1")
    assert (bug["category"], bug["priority"]) == ("bug", "P2")
    assert (feature["category"], feature["priority"]) == ("feature_request", "P3")
    assert (question["category"], question["priority"]) == ("question", "P3")


def test_heuristic_urgent_beats_bug_when_both_present():
    result = _heuristic_classify("Error grave", "La app no funciona, urge solucionarlo")

    assert result["category"] == "urgent"
    assert result["priority"] == "P1"


def test_heuristic_derives_tags():
    result = _heuristic_classify("Exportar a PDF", "Quiero exportar el informe a PDF")

    assert "pdf" in result["tags"]
    assert "export" in result["tags"]


def test_heuristic_unmatched_defaults_to_question():
    result = _heuristic_classify("Asunto", "Texto neutro sin señales claras")

    assert result["category"] == "question"
    assert result["priority"] == "P3"
    assert result["tags"] == []


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


def test_classify_ticket_falls_back_to_heuristic_on_sdk_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class _BoomClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("OpenRouter is unavailable")

    monkeypatch.setattr("openai.OpenAI", _BoomClient)

    # Even when the SDK blows up, we still differentiate via the heuristic
    # instead of returning the same flat constant for everything.
    result = classify_ticket("La app está caída", "No funciona en producción, urge")

    assert result["category"] == "urgent"
    assert result["priority"] == "P1"

    # And neutral text still degrades gracefully to the safe default.
    neutral = classify_ticket("Asunto", "Texto neutro")
    assert neutral == FALLBACK_CLASSIFICATION
