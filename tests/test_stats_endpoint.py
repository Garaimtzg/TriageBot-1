from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'stats.db'}")
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client


def test_tickets_stats_endpoint_returns_counts(client):
    db.create_ticket(
        title="Bug de login",
        description="No entra el usuario",
        category="bug",
        priority="P1",
        tags=["auth"],
    )
    db.create_ticket(
        title="Mejora de reportes",
        description="Añadir filtros",
        category="bug",
        priority="P2",
        tags=["reporting"],
        status="in_progress",
    )
    db.create_ticket(
        title="Nueva función",
        description="Exportar CSV",
        category="feature_request",
        priority="P1",
        tags=["export"],
        status="closed",
    )

    response = client.get("/tickets/ststs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["by_category"]["bug"] == 2
    assert payload["by_category"]["feature_request"] == 1
    assert payload["by_priority"]["P1"] == 2
    assert payload["by_status"]["open"] == 1
    assert payload["by_status"]["in_progress"] == 1
    assert payload["by_status"]["closed"] == 1
