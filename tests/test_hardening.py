"""Tests de robustez: los 10 escenarios de la ronda de QA.

Bloquean regresiones en validación, escapado (XSS), SQL parametrizada, fallback
del clasificador, IDs malformados, PATCH inválido y concurrencia.
No tocan ``tests/test_acceptance.py``.
"""

from __future__ import annotations

import concurrent.futures as cf

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """Cliente para la API JSON (sin auth), con BD aislada y clasificador mockeado."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'h.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": ["x"]},
    )
    return TestClient(app)


@pytest.fixture()
def ui(api):
    """Cliente UI con sesión iniciada (comparte la BD del fixture ``api``)."""
    c = TestClient(app)
    c.post(
        "/register",
        data={"name": "T", "email": "t@e.com", "password": "secret123"},
        follow_redirects=False,
    )
    return c


# 1 + 2. Título vacío/espacios y título de 5000 caracteres -> 422.
@pytest.mark.parametrize("title", ["", "   ", "x" * 5000])
def test_invalid_title_is_422(api, title):
    r = api.post("/tickets", json={"title": title, "description": "ok"})
    assert r.status_code == 422


# 3. Emojis y unicode no latino: se crean y se devuelven intactos.
def test_unicode_roundtrip(api):
    payload = {"title": "🚀 café ñ 日本語 Ω", "description": "emoji 😀 测试"}
    r = api.post("/tickets", json=payload)
    assert r.status_code == 201
    assert r.json()["title"] == payload["title"]
    assert r.json()["description"] == payload["description"]


# 4. HTML/JS en el contenido: se escapa al renderizar (no se ejecuta).
def test_xss_is_escaped_in_views(api, ui):
    api.post(
        "/tickets",
        json={"title": "<script>alert(1)</script>", "description": "<img src=x onerror=alert(1)>"},
    )
    for path in ("/", "/board", "/ui/tickets/1"):
        html = ui.get(path).text
        assert "<script>alert(1)</script>" not in html
        assert "<img src=x onerror=alert(1)>" not in html


# 5. Inyección SQL: las consultas son parametrizadas; nada se destruye ni filtra.
def test_sql_injection_is_neutralized(api, ui):
    api.post("/tickets", json={"title": "'); DROP TABLE tickets;--", "description": "x"})
    # La tabla sigue viva y el ticket se guarda como texto literal.
    listed = api.get("/tickets")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    # El buscador del tablero tampoco rompe ni devuelve todo por una tautología.
    r = ui.get("/ui/tickets", params={"q": "' OR '1'='1"})
    assert r.status_code == 200
    assert "No hay tickets" in r.text


# 6. LLM caído: el endpoint no propaga la excepción y aplica fallback (201).
def test_classifier_down_falls_back(api, monkeypatch):
    def boom(title, description):
        raise RuntimeError("LLM down")

    monkeypatch.setattr("app.classifier.classify_ticket", boom)
    r = api.post("/tickets", json={"title": "cae el llm", "description": "y?"})
    assert r.status_code == 201
    assert r.json()["category"] == "question"
    assert r.json()["priority"] == "P3"
    assert r.json()["tags"] == []


# 7. Mismo ticket dos veces: se permiten duplicados (dos filas distintas, sin error).
def test_duplicate_posts_create_two_tickets(api):
    p = {"title": "duplicado", "description": "mismo contenido"}
    r1, r2 = api.post("/tickets", json=p), api.post("/tickets", json=p)
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


# 8. IDs malformados: nunca un 500 (404 / 422 / 405 según el caso).
@pytest.mark.parametrize("ticket_id", ["-1", "abc", "99999999999"])
def test_malformed_ids_never_500(api, ticket_id):
    assert api.patch(f"/tickets/{ticket_id}", json={"status": "open"}).status_code in (404, 422)
    assert api.get(f"/tickets/{ticket_id}").status_code < 500


# 9. PATCH con estado inválido -> 422 y el ticket queda intacto.
def test_patch_invalid_status_is_422_and_noop(api):
    tid = api.post("/tickets", json={"title": "para patch", "description": "x"}).json()["id"]
    assert api.patch(f"/tickets/{tid}", json={"status": "inventado"}).status_code == 422
    intact = next(t for t in api.get("/tickets").json() if t["id"] == tid)
    assert intact["status"] == "open"


# 10. 20 POSTs concurrentes: todos 201, 20 filas, sin 500 ni "database is locked".
def test_concurrent_posts(api):
    def post(i):
        return api.post(
            "/tickets", json={"title": f"concurrente {i}", "description": "carga"}
        ).status_code

    with cf.ThreadPoolExecutor(max_workers=20) as ex:
        codes = list(ex.map(post, range(20)))

    assert codes == [201] * 20
    assert len(api.get("/tickets").json()) == 20
