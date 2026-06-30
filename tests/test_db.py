"""Unit tests for the app.db persistence layer.

Each test points DATABASE_URL at an isolated SQLite file under tmp_path, so the
tests never touch the real database and don't interfere with each other.
"""

from __future__ import annotations

import pytest

from app import db


@pytest.fixture()
def _isolated_db(tmp_path, monkeypatch):
    """Point the persistence layer at a throwaway database for this test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    db.init_db()


def _new_ticket(**overrides) -> dict:
    fields = {
        "title": "Título original",
        "description": "Descripción original",
        "category": "bug",
        "priority": "P2",
        "tags": ["login"],
    }
    fields.update(overrides)
    return db.create_ticket(**fields)


def test_update_ticket_ignores_non_allowed_fields(_isolated_db):
    """Only status/priority/category may be updated.

    Other columns (title, description, tags, id, created_at...) must be left
    untouched even if a caller sneaks them into the fields dict, so a PATCH can
    never rewrite the ticket's content or identity.
    """
    ticket = _new_ticket()

    updated = db.update_ticket(
        ticket["id"],
        {
            "status": "closed",          # allowed -> should change
            "title": "HACKEADO",         # not allowed -> must be ignored
            "description": "otra cosa",  # not allowed -> must be ignored
            "tags": ["pwned"],           # not allowed -> must be ignored
            "id": 999,                   # not allowed -> must be ignored
            "created_at": "1970-01-01",  # not allowed -> must be ignored
        },
    )

    # The single allowed field was applied...
    assert updated["status"] == "closed"
    # ...and everything else is exactly as it was created.
    assert updated["id"] == ticket["id"]
    assert updated["title"] == "Título original"
    assert updated["description"] == "Descripción original"
    assert updated["tags"] == ["login"]
    assert updated["category"] == "bug"
    assert updated["priority"] == "P2"
    assert updated["created_at"] == ticket["created_at"]


def test_update_ticket_with_no_allowed_fields_is_a_noop(_isolated_db):
    """A dict with only disallowed/None fields leaves the ticket fully unchanged."""
    ticket = _new_ticket()

    unchanged = db.update_ticket(
        ticket["id"],
        {"title": "nope", "priority": None},
    )

    assert unchanged == ticket
