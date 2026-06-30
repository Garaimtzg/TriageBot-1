"""Tests del frontend de dos páginas (crear + tablero).

Cubren lo nuevo respecto a la UI anterior: paginación de 10 en 10, búsqueda por
título, el fragmento de detalle (modal) y el aviso de creación (HX-Trigger).
No tocan ``tests/test_acceptance.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": ["x"]},
    )
    return TestClient(app)


def _create(client, n: int, prefix: str = "Ticket") -> None:
    for i in range(n):
        resp = client.post(
            "/ui/tickets",
            data={"title": f"{prefix} {i}", "description": f"detalle {i}"},
        )
        assert resp.status_code == 200


def test_create_page_and_board_page_render(client):
    home = client.get("/")
    assert home.status_code == 200
    assert "TriageBot" in home.text
    assert 'hx-post="/ui/tickets"' in home.text  # formulario de creación

    board = client.get("/board")
    assert board.status_code == 200
    assert "Tablero" in board.text
    assert 'hx-get="/ui/tickets"' in board.text  # filtros/búsqueda


def test_create_returns_recent_list_with_popup_trigger(client):
    resp = client.post(
        "/ui/tickets",
        data={"title": "La app no carga", "description": "Pantalla en blanco"},
    )
    assert resp.status_code == 200
    # El nuevo ticket aparece en el listado reciente...
    assert "La app no carga" in resp.text
    # ...y se dispara el evento para el popup de éxito.
    assert resp.headers.get("HX-Trigger") == "ticketCreated"


def test_board_paginates_ten_per_page(client):
    _create(client, 23)

    page1 = client.get("/ui/tickets", params={"page": 1})
    assert page1.status_code == 200
    assert page1.text.count('hx-get="/ui/tickets/') == 10
    assert "Página 1 de 3" in page1.text

    page3 = client.get("/ui/tickets", params={"page": 3})
    assert page3.text.count('hx-get="/ui/tickets/') == 3  # 23 = 10 + 10 + 3

    # Una página fuera de rango se acota a la última válida (no rompe).
    overflow = client.get("/ui/tickets", params={"page": 99})
    assert "Página 3 de 3" in overflow.text


def test_board_search_by_title(client):
    _create(client, 3, prefix="Exportar informe")
    _create(client, 2, prefix="Login roto")

    found = client.get("/ui/tickets", params={"q": "exportar"})
    assert found.text.count('hx-get="/ui/tickets/') == 3
    assert "Login roto" not in found.text

    empty = client.get("/ui/tickets", params={"q": "noexiste-zzz"})
    assert "No hay tickets" in empty.text


def test_ticket_detail_fragment_shows_all_fields(client):
    _create(client, 1, prefix="Detalle")

    detail = client.get("/ui/tickets/1")
    assert detail.status_code == 200
    # Campos no visibles en la tabla, presentes en el modal.
    assert "Descripción" in detail.text
    assert "detalle 0" in detail.text
    assert "Actualizado" in detail.text

    assert client.get("/ui/tickets/9999").status_code == 404
