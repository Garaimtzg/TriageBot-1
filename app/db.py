"""SQLite persistence for TriageBot.

The database path is resolved at call time from the environment variable named
in ``config.yaml`` (``database.url_env``, by default ``DATABASE_URL``; format
``sqlite:///path/to.db``). Reading it fresh on every call lets the test suite
point each test at an isolated database after the app module has been imported.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime

from app.config import get_config

_db_cfg = get_config()["database"]
_DEFAULT_STATUS = get_config()["ticket"]["default_status"]


def _database_path() -> str:
    """Resolve the SQLite file path from config + env (read fresh on every call)."""
    url = os.getenv(_db_cfg["url_env"], _db_cfg["default_url"])
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
    status: str = _DEFAULT_STATUS,
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


def _filter_sql(
    category: str | None,
    priority: str | None,
    status: str | None,
    search: str | None,
) -> tuple[list[str], list[str]]:
    """Build the shared WHERE clauses/params used by list_tickets and count_tickets."""
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
    if search:
        # Case-insensitive substring match on the title.
        clauses.append("LOWER(title) LIKE ?")
        params.append(f"%{search.lower()}%")
    return clauses, params


def list_tickets(
    *,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """Return tickets (newest first), optionally filtered, searched and paginated.

    ``search`` matches a case-insensitive substring of the title. When ``limit``
    is given the result is paginated (``offset`` rows are skipped first); with no
    ``limit`` every matching ticket is returned (backwards-compatible default).
    """
    init_db()
    clauses, params = _filter_sql(category, priority, status, search)

    query = "SELECT * FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_tickets(
    *,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> int:
    """Count tickets matching the same filters as :func:`list_tickets` (for pagination)."""
    init_db()
    clauses, params = _filter_sql(category, priority, status, search)
    query = "SELECT COUNT(*) AS n FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with _connect() as conn:
        return int(conn.execute(query, params).fetchone()["n"])


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
