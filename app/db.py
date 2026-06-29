"""SQLite persistence for TriageBot.

The database path is resolved at call time from the ``DATABASE_URL`` environment
variable (format ``sqlite:///path/to.db``). This lets the test suite point each
test at an isolated database after the app module has been imported.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime

DEFAULT_DATABASE_URL = "sqlite:///triagebot.db"


def _database_path() -> str:
    """Resolve the SQLite file path from DATABASE_URL (read fresh on every call)."""
    url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        return url[len("sqlite://") :]
    return url


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_database_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the tickets table if it does not exist yet."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "category": row["category"],
        "priority": row["priority"],
        "tags": json.loads(row["tags"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_ticket(
    *,
    title: str,
    description: str,
    category: str,
    priority: str,
    tags: list[str],
    status: str = "open",
) -> dict:
    """Insert a ticket and return it as a dict."""
    init_db()
    now = _now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tickets
                (title, description, category, priority, tags, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, description, category, priority, json.dumps(tags), status, now, now),
        )
        conn.commit()
        ticket_id = cursor.lastrowid
    return get_ticket(ticket_id)


def get_ticket(ticket_id: int) -> dict | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_tickets(
    *,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Return tickets (newest first) optionally filtered by category/priority/status."""
    init_db()
    clauses: list[str] = []
    params: list[str] = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)
    if status:
        clauses.append("status = ?")
        params.append(status)

    query = "SELECT * FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_ticket(ticket_id: int, fields: dict) -> dict | None:
    """Update the given fields of a ticket and bump updated_at. Returns the ticket."""
    init_db()
    allowed = {"status", "priority", "category"}
    updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not updates:
        return get_ticket(ticket_id)

    set_clause = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values())
    params.append(_now())  # updated_at
    params.append(ticket_id)

    with _connect() as conn:
        conn.execute(
            f"UPDATE tickets SET {set_clause}, updated_at = ? WHERE id = ?",
            params,
        )
        conn.commit()
    return get_ticket(ticket_id)
