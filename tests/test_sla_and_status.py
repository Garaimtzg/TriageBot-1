"""Tests de las dos features nuevas:

1. Fecha límite (SLA) por prioridad y detección/filtro de vencidos.
2. Ciclo de vida ampliado (en curso / resuelto / reabrir) desde la UI.

No tocan ``tests/test_acceptance.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

# --- 1. SLA / vencidos (capa de persistencia) ------------------------------


@pytest.fixture()
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db()


def _make(priority: str) -> dict:
    return db.create_ticket(
        title="T", description="D", category="bug", priority=priority, tags=[]
    )


def test_due_date_follows_priority(_isolated_db):
    """P1 vence hoy, P2 mañana, P3 en dos días (desde la fecha de creación)."""
    today = datetime.now(UTC).date()
    assert _make("P1")["due_date"] == today.isoformat()
    assert _make("P2")["due_date"] == (today + timedelta(days=1)).isoformat()
    assert _make("P3")["due_date"] == (today + timedelta(days=2)).isoformat()


def test_due_date_recomputed_when_priority_changes(_isolated_db):
    """Subir la prioridad acerca la fecha límite."""
    ticket = _make("P3")
    today = datetime.now(UTC).date()
    assert ticket["due_date"] == (today + timedelta(days=2)).isoformat()

    bumped = db.update_ticket(ticket["id"], {"priority": "P1"})
    assert bumped["due_date"] == today.isoformat()


def test_overdue_flag_and_filter_ignore_terminal_status(_isolated_db):
    """Un ticket con fecha pasada está vencido; al resolverlo deja de contar."""
    ticket = _make("P1")
    assert ticket["is_overdue"] is False  # vence hoy, aún no vencido

    # Forzamos una fecha límite en el pasado.
    with db._connect() as conn:
        conn.execute(
            "UPDATE tickets SET due_date = ? WHERE id = ?", ("2000-01-01", ticket["id"])
        )
        conn.commit()

    reloaded = db.get_ticket(ticket["id"])
    assert reloaded["is_overdue"] is True
    overdue_ids = [t["id"] for t in db.list_tickets(overdue=True)]
    assert overdue_ids == [ticket["id"]]

    # Al resolver, ya no se considera vencido ni aparece en el filtro.
    db.update_ticket(ticket["id"], {"status": "resolved"})
    assert db.get_ticket(ticket["id"])["is_overdue"] is False
    assert db.list_tickets(overdue=True) == []


# --- 2. Ciclo de vida desde la UI ------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": []},
    )
    return TestClient(app)


def test_status_lifecycle_start_resolve_reopen(client):
    """open -> en curso -> resuelto -> reabrir, vía el endpoint de la UI."""
    client.post("/ui/tickets", data={"title": "Ticket ciclo", "description": "detalle"})

    # Empezar: pasa a "En curso" y dispara el refresco del tablero.
    started = client.post("/ui/tickets/1/status", data={"new_status": "in_progress"})
    assert started.status_code == 200
    assert "En curso" in started.text
    assert started.headers.get("HX-Trigger") == "ticketUpdated"
    assert db.get_ticket(1)["status"] == "in_progress"

    # Resolver.
    resolved = client.post("/ui/tickets/1/status", data={"new_status": "resolved"})
    assert "Resuelto" in resolved.text
    assert db.get_ticket(1)["status"] == "resolved"

    # Reabrir (cliente se queja de que no estaba resuelto).
    reopened = client.post("/ui/tickets/1/status", data={"new_status": "open"})
    assert "Abierto" in reopened.text
    assert db.get_ticket(1)["status"] == "open"


def test_status_endpoint_rejects_unknown_status_and_missing_ticket(client):
    client.post("/ui/tickets", data={"title": "T", "description": "d"})

    # Estado inexistente -> 422, sin tocar el ticket.
    bad = client.post("/ui/tickets/1/status", data={"new_status": "patata"})
    assert bad.status_code == 422
    assert db.get_ticket(1)["status"] == "open"

    # Ticket inexistente -> 404.
    assert client.post("/ui/tickets/999/status", data={"new_status": "resolved"}).status_code == 404
