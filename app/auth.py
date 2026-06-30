"""Authentication helpers for TriageBot.

Password hashing uses passlib (bcrypt). Sessions are cookie-based via Starlette's
``SessionMiddleware`` (configured in ``app.main``); here we only deal with
hashing/verifying passwords and reading the logged-in user from the session.
"""

from __future__ import annotations

from fastapi import Request
from passlib.context import CryptContext

from app import db

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_USER_KEY = "user_id"


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def authenticate(email: str, password: str) -> dict | None:
    """Return the public user dict if the credentials are valid, else ``None``."""
    row = db.get_user_by_email(email)
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return db.get_user(row["id"])


def login_session(request: Request, user_id: int) -> None:
    request.session[SESSION_USER_KEY] = user_id


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def get_current_user(request: Request) -> dict | None:
    """Return the public dict of the logged-in user, or ``None`` if not logged in."""
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id is None:
        return None
    return db.get_user(user_id)
