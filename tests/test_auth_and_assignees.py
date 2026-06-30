"""Tests de gestión de usuarios (login/registro) y asignación de responsables.

No tocan ``tests/test_acceptance.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app


@pytest.fixture()
def anon(tmp_path, monkeypatch):
    """Cliente sin sesión con BD aislada."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": ["x"]},
    )
    return TestClient(app)


def _register(client, name="Ada", email="ada@example.com", password="secret123"):
    return client.post(
        "/register",
        data={"name": name, "email": email, "password": password},
        follow_redirects=False,
    )


# --- Hashing ---------------------------------------------------------------


def test_password_hash_roundtrip():
    h = auth.hash_password("secret123")
    assert h != "secret123"
    assert auth.verify_password("secret123", h)
    assert not auth.verify_password("wrong", h)


# --- Registro / login ------------------------------------------------------


def test_register_logs_in_and_grants_access(anon):
    resp = _register(anon)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # Ya con sesión, la home carga.
    assert anon.get("/").status_code == 200


def test_register_rejects_duplicate_email(anon):
    assert _register(anon).status_code == 303
    anon.post("/logout", follow_redirects=False)
    dup = _register(anon, name="Otra", password="secret123")
    assert dup.status_code == 409


def test_register_rejects_short_password(anon):
    resp = _register(anon, password="short")
    assert resp.status_code == 422


def test_login_with_valid_and_invalid_credentials(anon):
    _register(anon)
    anon.post("/logout", follow_redirects=False)

    bad = anon.post(
        "/login",
        data={"email": "ada@example.com", "password": "nope"},
        follow_redirects=False,
    )
    assert bad.status_code == 401

    good = anon.post(
        "/login",
        data={"email": "ada@example.com", "password": "secret123"},
        follow_redirects=False,
    )
    assert good.status_code == 303
    assert good.headers["location"] == "/"


def test_logout_revokes_access(anon):
    _register(anon)
    assert anon.get("/", follow_redirects=False).status_code == 200
    anon.post("/logout", follow_redirects=False)
    redirected = anon.get("/", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/login"


# --- Asignación de responsables -------------------------------------------


def test_create_ticket_with_multiple_assignees(anon):
    _register(anon, name="Ada", email="ada@example.com")
    anon.post("/logout", follow_redirects=False)
    _register(anon, name="Linus", email="linus@example.com")
    users = db.list_users()
    ids = [u["id"] for u in users]
    assert len(ids) == 2

    resp = anon.post(
        "/ui/tickets",
        data={"title": "Multi", "description": "varios responsables", "assignee_ids": ids},
    )
    assert resp.status_code == 200

    ticket = db.list_tickets()[0]
    assert {a["id"] for a in ticket["assignees"]} == set(ids)
    assert {a["name"] for a in ticket["assignees"]} == {"Ada", "Linus"}


def test_reassign_assignees_endpoint(anon):
    _register(anon, name="Ada", email="ada@example.com")
    anon.post("/logout", follow_redirects=False)
    _register(anon, name="Linus", email="linus@example.com")
    ada, linus = (u["id"] for u in db.list_users())

    anon.post(
        "/ui/tickets",
        data={"title": "Reasignar", "description": "x", "assignee_ids": [ada]},
    )
    ticket_id = db.list_tickets()[0]["id"]

    # Reasignar a Linus únicamente.
    resp = anon.post(
        f"/ui/tickets/{ticket_id}/assignees",
        data={"assignee_ids": [linus]},
    )
    assert resp.status_code == 200
    ticket = db.get_ticket(ticket_id)
    assert {a["id"] for a in ticket["assignees"]} == {linus}

    # Reasignar a cero responsables no está permitido.
    empty = anon.post(f"/ui/tickets/{ticket_id}/assignees", data={})
    assert empty.status_code == 422


def test_create_returns_confirmation_card_not_a_list(anon):
    _register(anon, name="Ada", email="ada@example.com")
    ada = db.list_users()[0]["id"]
    resp = anon.post(
        "/ui/tickets",
        data={"title": "Pantalla en blanco", "description": "x", "assignee_ids": [ada]},
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Trigger") == "ticketCreated"
    # La confirmación muestra el ticket creado y enlaza al tablero.
    assert "creado" in resp.text
    assert "Pantalla en blanco" in resp.text
    assert "/board" in resp.text


def test_board_defaults_to_my_tickets_with_toggle_for_all(anon):
    # Ada se registra y crea un ticket asignado SOLO a Linus.
    _register(anon, name="Ada", email="ada@example.com")
    anon.post("/logout", follow_redirects=False)
    _register(anon, name="Linus", email="linus@example.com")
    linus = db.get_user_by_email("linus@example.com")["id"]
    anon.post("/logout", follow_redirects=False)

    # Sesión de Ada: crea un ticket cuyo único responsable es Linus.
    anon.post(
        "/login",
        data={"email": "ada@example.com", "password": "secret123"},
        follow_redirects=False,
    )
    anon.post(
        "/ui/tickets",
        data={"title": "Para Linus", "description": "x", "assignee_ids": [linus]},
    )

    # Por defecto (scope=mine) Ada no ve el ticket: no es responsable.
    mine = anon.get("/ui/tickets")
    assert "Para Linus" not in mine.text
    assert "No hay tickets" in mine.text

    # Con el botón "Todos" (scope=all) sí lo ve.
    all_tickets = anon.get("/ui/tickets", params={"scope": "all"})
    assert "Para Linus" in all_tickets.text


def test_assignees_ignore_unknown_user_ids(anon):
    _register(anon, name="Ada", email="ada@example.com")
    ada = db.list_users()[0]["id"]

    anon.post(
        "/ui/tickets",
        data={"title": "Filtra", "description": "x", "assignee_ids": [ada, 9999]},
    )
    ticket = db.list_tickets()[0]
    # El id inexistente se descarta; sólo queda el válido.
    assert {a["id"] for a in ticket["assignees"]} == {ada}
