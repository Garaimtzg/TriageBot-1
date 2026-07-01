"""Puebla la base de datos con tickets de ejemplo (seed_tickets.json).

Uso:
    python scripts/seed.py                # inserta los 140 tickets del JSON
    python scripts/seed.py --count 50     # inserta 50 (cicla si pide más de los que hay)
    python scripts/seed.py --reset        # vacía tickets/asignaciones antes de insertar

Detalles pensados para que la demo luzca:
- Clasifica cada ticket con el clasificador real (o la heurística offline si no
  hay OPENROUTER_API_KEY), así salen categorías/prioridades/tags de verdad.
- Respeta la fecha de creación del JSON (marzo–junio), de modo que muchos
  quedan **vencidos** y se ve la columna "Vence" y el filtro "Solo vencidos".
- Reparte estados (abierto / en curso / resuelto / cerrado) y asigna 1–2
  responsables de un pequeño elenco de usuarios de ejemplo.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

# Permite ejecutar el script directamente (``python scripts/seed.py``) añadiendo
# la raíz del repo al path para poder importar el paquete ``app``.
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import auth, classifier, db  # noqa: E402

SEED_FILE = BASE_DIR / "seed_tickets.json"

DEMO_USERS = [
    ("Marta Iturri", "marta@triagebot.local"),
    ("Iker Sanz", "iker@triagebot.local"),
    ("Ane Gorostiza", "ane@triagebot.local"),
    ("Jon Etxeberria", "jon@triagebot.local"),
    ("Nerea Ruiz", "nerea@triagebot.local"),
]

# Estados repartidos con pesos (la mayoría abiertos / en curso).
STATUS_WEIGHTS = {"open": 45, "in_progress": 25, "resolved": 22, "closed": 8}


def _ensure_users() -> list[int]:
    """Crea los usuarios de ejemplo si no existen y devuelve sus ids."""
    ids: list[int] = []
    for name, email in DEMO_USERS:
        existing = db.get_user_by_email(email)
        if existing:
            ids.append(existing["id"])
        else:
            ids.append(db.create_user(
                name=name, email=email, password_hash=auth.hash_password("demo1234")
            )["id"])
    return ids


def _reset() -> None:
    with db._connect() as conn:
        conn.execute("DELETE FROM ticket_assignees")
        conn.execute("DELETE FROM tickets")
        conn.commit()


def _backdate(ticket_id: int, created_iso: str, priority: str, status: str) -> None:
    """Ajusta created_at/updated_at/due_date/status de un ticket ya insertado."""
    due = db._compute_due_date(created_iso, priority)
    with db._connect() as conn:
        conn.execute(
            "UPDATE tickets SET created_at = ?, updated_at = ?, due_date = ?, status = ? "
            "WHERE id = ?",
            (created_iso, created_iso, due, status, ticket_id),
        )
        conn.commit()


def seed(count: int | None = None, reset: bool = False, seed_value: int = 7) -> int:
    random.seed(seed_value)
    db.init_db()  # asegura que las tablas existen (BD nueva o antigua migrada)
    tickets = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    if count is not None:
        # Cicla el catálogo si se piden más tickets de los que hay.
        tickets = [tickets[i % len(tickets)] for i in range(count)]

    if reset:
        _reset()

    user_ids = _ensure_users()
    statuses = list(STATUS_WEIGHTS)
    weights = list(STATUS_WEIGHTS.values())

    created = 0
    for item in tickets:
        classification = classifier.classify_ticket(item["title"], item["description"])
        assignees = random.sample(user_ids, k=random.randint(1, 2))
        ticket = db.create_ticket(
            title=item["title"],
            description=item["description"],
            category=classification["category"],
            priority=classification["priority"],
            tags=classification["tags"],
            assignee_ids=assignees,
        )
        status = random.choices(statuses, weights=weights, k=1)[0]
        _backdate(ticket["id"], item["created_at"], ticket["priority"], status)
        created += 1

    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Puebla la BD con tickets de ejemplo.")
    parser.add_argument("--count", type=int, default=None, help="cuántos tickets insertar")
    parser.add_argument("--reset", action="store_true", help="vaciar tickets antes de insertar")
    args = parser.parse_args()

    n = seed(count=args.count, reset=args.reset)
    total = len(db.list_tickets())
    overdue = len(db.list_tickets(overdue=True))
    print(f"Insertados {n} tickets. Total en BD: {total} ({overdue} vencidos).")


if __name__ == "__main__":
    main()
