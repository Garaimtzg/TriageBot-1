"""Tests del frontend de dos páginas (crear + tablero).

Cubren lo nuevo respecto a la UI anterior: paginación de 10 en 10, búsqueda por
título, el fragmento de detalle (modal), el aviso de creación (HX-Trigger) y la
asignación de responsables. La UI requiere sesión, así que el fixture registra e
inicia sesión con un usuario. No tocan ``tests/test_acceptance.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": ["x"]},
    )
    c = TestClient(app)
    # Registrarse inicia sesión (cookie de sesión queda en el cliente).
    resp = c.post(
        "/register",
        data={"name": "Tester", "email": "tester@example.com", "password": "secret123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return c


@pytest.fixture()
def assignee_id() -> int:
    """Id del usuario registrado por el fixture ``client`` (para asignarlo)."""
    return db.list_users()[0]["id"]


def _create(client, n: int, assignee_id: int, prefix: str = "Ticket") -> None:
    for i in range(n):
        resp = client.post(
            "/ui/tickets",
            data={
                "title": f"{prefix} {i}",
                "description": f"detalle {i}",
                "assignee_ids": [assignee_id],
            },
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


def test_create_returns_confirmation_with_popup_trigger(client, assignee_id):
    resp = client.post(
        "/ui/tickets",
        data={
            "title": "La app no carga",
            "description": "Pantalla en blanco",
            "assignee_ids": [assignee_id],
        },
    )
    assert resp.status_code == 200
    # La confirmación muestra el ticket creado...
    assert "La app no carga" in resp.text
    # ...y se dispara el evento para el popup de éxito.
    assert resp.headers.get("HX-Trigger") == "ticketCreated"


def test_create_requires_at_least_one_assignee(client):
    resp = client.post(
        "/ui/tickets",
        data={"title": "Sin responsable", "description": "no debería crearse"},
    )
    assert resp.status_code == 422


def test_board_paginates_ten_per_page(client, assignee_id):
    _create(client, 23, assignee_id)

    page1 = client.get("/ui/tickets", params={"page": 1})
    assert page1.status_code == 200
    assert page1.text.count('hx-get="/ui/tickets/') == 10
    assert "Página 1 de 3" in page1.text

    page3 = client.get("/ui/tickets", params={"page": 3})
    assert page3.text.count('hx-get="/ui/tickets/') == 3  # 23 = 10 + 10 + 3

    # Una página fuera de rango se acota a la última válida (no rompe).
    overflow = client.get("/ui/tickets", params={"page": 99})
    assert "Página 3 de 3" in overflow.text


def test_board_search_by_title(client, assignee_id):
    _create(client, 3, assignee_id, prefix="Exportar informe")
    _create(client, 2, assignee_id, prefix="Login roto")

    found = client.get("/ui/tickets", params={"q": "exportar"})
    assert found.text.count('hx-get="/ui/tickets/') == 3
    assert "Login roto" not in found.text

    empty = client.get("/ui/tickets", params={"q": "noexiste-zzz"})
    assert "No hay tickets" in empty.text


def test_ticket_detail_fragment_shows_all_fields(client, assignee_id):
    _create(client, 1, assignee_id, prefix="Detalle")

    detail = client.get("/ui/tickets/1")
    assert detail.status_code == 200
    # Campos no visibles en la tabla, presentes en el modal.
    assert "Descripción" in detail.text
    assert "detalle 0" in detail.text
    assert "Actualizado" in detail.text
    # El responsable asignado aparece en el detalle.
    assert "Responsables" in detail.text
    assert "Tester" in detail.text

    assert client.get("/ui/tickets/9999").status_code == 404


def test_ticket_assignees_shown_in_board(client, assignee_id):
    _create(client, 1, assignee_id, prefix="ConResponsable")
    table = client.get("/ui/tickets")
    assert "Tester" in table.text
