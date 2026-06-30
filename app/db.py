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
from datetime import UTC, date, datetime, timedelta

from app.config import get_config

_db_cfg = get_config()["database"]
_ticket_cfg = get_config()["ticket"]
_DEFAULT_STATUS = _ticket_cfg["default_status"]
_SLA_DAYS = {str(k): int(v) for k, v in _ticket_cfg.get("sla_days", {}).items()}
_TERMINAL_STATUSES = set(_ticket_cfg.get("terminal_statuses", []))


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


def _today() -> str:
    """Today's date (UTC) as ISO ``YYYY-MM-DD`` for due-date comparisons."""
    return datetime.now(UTC).date().isoformat()


def _compute_due_date(created_iso: str, priority: str) -> str | None:
    """Derive the due date (ISO ``YYYY-MM-DD``) from the creation date + SLA.

    Returns ``None`` if the priority has no configured SLA.
    """
    if priority not in _SLA_DAYS:
        return None
    created_day = date.fromisoformat(created_iso[:10])
    return (created_day + timedelta(days=_SLA_DAYS[priority])).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_database_path())
    conn.row_factory = sqlite3.Row
    # Enforce foreign keys so ON DELETE CASCADE works for ticket assignees.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the tickets/users/assignee tables if needed, then run migrations."""
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
                updated_at TEXT NOT NULL,
                due_date TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_assignees (
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (ticket_id, user_id),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight migrations for databases created by older versions."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
    if "due_date" not in columns:
        conn.execute("ALTER TABLE tickets ADD COLUMN due_date TEXT")
    # Backfill due_date for rows that predate the SLA feature.
    for row in conn.execute(
        "SELECT id, created_at, priority FROM tickets WHERE due_date IS NULL"
    ).fetchall():
        due = _compute_due_date(row["created_at"], row["priority"])
        if due is not None:
            conn.execute(
                "UPDATE tickets SET due_date = ? WHERE id = ?", (due, row["id"])
            )


# --- Users -----------------------------------------------------------------


def _public_user(row: sqlite3.Row | dict) -> dict:
    """Project a user row to its public shape (never expose the password hash)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "created_at": row["created_at"],
    }


def create_user(*, name: str, email: str, password_hash: str) -> dict:
    """Insert a user and return its public dict. Raises on duplicate email."""
    init_db()
    now = _now()
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, email, password_hash, now),
        )
        conn.commit()
        user_id = cursor.lastrowid
    return get_user(user_id)


def get_user(user_id: int) -> dict | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public_user(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    """Return the full user row (including ``password_hash``) for authentication."""
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE").fetchall()
    return [_public_user(row) for row in rows]


def _existing_user_ids(conn: sqlite3.Connection, user_ids: list[int]) -> list[int]:
    """Filter ``user_ids`` down to the ones that actually exist (order preserved)."""
    if not user_ids:
        return []
    rows = conn.execute("SELECT id FROM users").fetchall()
    known = {row["id"] for row in rows}
    seen: set[int] = set()
    result: list[int] = []
    for uid in user_ids:
        if uid in known and uid not in seen:
            seen.add(uid)
            result.append(uid)
    return result


# --- Tickets ---------------------------------------------------------------


def _assignees_for(conn: sqlite3.Connection, ticket_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT u.id, u.name, u.email, u.created_at
        FROM ticket_assignees ta
        JOIN users u ON u.id = ta.user_id
        WHERE ta.ticket_id = ?
        ORDER BY u.name COLLATE NOCASE
        """,
        (ticket_id,),
    ).fetchall()
    return [_public_user(row) for row in rows]


def _row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    due_date = row["due_date"]
    status = row["status"]
    # Vencido = tiene fecha límite pasada y no está en un estado terminal.
    is_overdue = (
        due_date is not None and status not in _TERMINAL_STATUSES and due_date < _today()
    )
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "category": row["category"],
        "priority": row["priority"],
        "tags": json.loads(row["tags"]),
        "status": row["status"],
        "assignees": _assignees_for(conn, row["id"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "due_date": due_date,
        "is_overdue": is_overdue,
    }


def _set_assignees(conn: sqlite3.Connection, ticket_id: int, user_ids: list[int]) -> None:
    """Replace the assignees of a ticket with the given (validated) user ids."""
    valid = _existing_user_ids(conn, user_ids)
    conn.execute("DELETE FROM ticket_assignees WHERE ticket_id = ?", (ticket_id,))
    conn.executemany(
        "INSERT INTO ticket_assignees (ticket_id, user_id) VALUES (?, ?)",
        [(ticket_id, uid) for uid in valid],
    )


def create_ticket(
    *,
    title: str,
    description: str,
    category: str,
    priority: str,
    tags: list[str],
    status: str = _DEFAULT_STATUS,
    assignee_ids: list[int] | None = None,
) -> dict:
    """Insert a ticket (with optional assignees) and return it as a dict."""
    init_db()
    now = _now()
    due_date = _compute_due_date(now, priority)
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tickets
                (title, description, category, priority, tags, status,
                 created_at, updated_at, due_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title, description, category, priority, json.dumps(tags),
                status, now, now, due_date,
            ),
        )
        ticket_id = cursor.lastrowid
        _set_assignees(conn, ticket_id, assignee_ids or [])
        conn.commit()
    return get_ticket(ticket_id)


def get_ticket(ticket_id: int) -> dict | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _row_to_dict(conn, row) if row else None


def _filter_sql(
    category: str | None,
    priority: str | None,
    status: str | None,
    search: str | None,
    overdue: bool = False,
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
    if overdue:
        # Vencidos: con fecha límite pasada y fuera de los estados terminales.
        clauses.append("due_date IS NOT NULL AND due_date < ?")
        params.append(_today())
        if _TERMINAL_STATUSES:
            placeholders = ", ".join("?" for _ in _TERMINAL_STATUSES)
            clauses.append(f"status NOT IN ({placeholders})")
            params.extend(sorted(_TERMINAL_STATUSES))
    return clauses, params


def list_tickets(
    *,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    search: str | None = None,
    overdue: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """Return tickets (newest first), optionally filtered, searched and paginated.

    ``search`` matches a case-insensitive substring of the title. ``overdue``
    keeps only past-due tickets that are not in a terminal status. When ``limit``
    is given the result is paginated (``offset`` rows are skipped first); with no
    ``limit`` every matching ticket is returned (backwards-compatible default).
    """
    init_db()
    clauses, params = _filter_sql(category, priority, status, search, overdue)

    query = "SELECT * FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(conn, row) for row in rows]


def count_tickets(
    *,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    search: str | None = None,
    overdue: bool = False,
) -> int:
    """Count tickets matching the same filters as :func:`list_tickets` (for pagination)."""
    init_db()
    clauses, params = _filter_sql(category, priority, status, search, overdue)
    query = "SELECT COUNT(*) AS n FROM tickets"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with _connect() as conn:
        return int(conn.execute(query, params).fetchone()["n"])


def get_ticket_stats() -> dict:
    """Return aggregate counts of tickets by category, priority and status."""
    init_db()
    with _connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"])
        by_category = {
            row["category"]: int(row["n"])
            for row in conn.execute(
                "SELECT category, COUNT(*) AS n FROM tickets GROUP BY category ORDER BY category"
            ).fetchall()
        }
        by_priority = {
            row["priority"]: int(row["n"])
            for row in conn.execute(
                "SELECT priority, COUNT(*) AS n FROM tickets GROUP BY priority ORDER BY priority"
            ).fetchall()
        }
        by_status = {
            row["status"]: int(row["n"])
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM tickets GROUP BY status ORDER BY status"
            ).fetchall()
        }
    return {
        "total": total,
        "by_category": by_category,
        "by_priority": by_priority,
        "by_status": by_status,
    }


def update_ticket(ticket_id: int, fields: dict) -> dict | None:
    """Update the given fields of a ticket and bump updated_at. Returns the ticket.

    Only ``status``/``priority``/``category`` columns may change. ``assignee_ids``
    (when provided and not ``None``) replaces the ticket's assignees.
    """
    init_db()
    allowed = {"status", "priority", "category"}
    updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
    assignee_ids = fields.get("assignee_ids")
    reassign = assignee_ids is not None

    if not updates and not reassign:
        return get_ticket(ticket_id)

    # La fecha límite depende de la prioridad: si cambia, se recalcula desde la
    # fecha de creación del ticket.
    if "priority" in updates:
        current = get_ticket(ticket_id)
        if current is not None:
            updates["due_date"] = _compute_due_date(current["created_at"], updates["priority"])

    with _connect() as conn:
        if reassign:
            _set_assignees(conn, ticket_id, assignee_ids)
        set_clauses = [f"{key} = ?" for key in updates]
        params: list = list(updates.values())
        set_clauses.append("updated_at = ?")
        params.append(_now())
        params.append(ticket_id)
        conn.execute(
            f"UPDATE tickets SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        conn.commit()
    return get_ticket(ticket_id)
