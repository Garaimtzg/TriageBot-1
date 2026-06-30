"""Domain models / Pydantic schemas for TriageBot."""

import re
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.config import get_config

_cfg = get_config()
_ticket_cfg = _cfg["ticket"]
_validation_cfg = _cfg["validation"]

ALLOWED_CATEGORIES = set(_ticket_cfg["categories"])
ALLOWED_PRIORITIES = set(_ticket_cfg["priorities"])
ALLOWED_STATUSES = set(_ticket_cfg["statuses"])

TITLE_MAX_LEN = int(_validation_cfg["title_max_len"])
DESCRIPTION_MAX_LEN = int(_validation_cfg["description_max_len"])
NAME_MAX_LEN = int(_validation_cfg["name_max_len"])
EMAIL_MAX_LEN = int(_validation_cfg["email_max_len"])
PASSWORD_MIN_LEN = int(_validation_cfg["password_min_len"])
PASSWORD_MAX_LEN = int(_validation_cfg["password_max_len"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TicketCreate(BaseModel):
    """Payload to create a ticket.

    ``title`` and ``description`` are the user-provided content; ``assignee_ids``
    is an optional list of user ids to assign as responsibles (empty by default
    so the programmatic API can create a ticket without assignees).
    """

    title: str
    description: str
    assignee_ids: list[int] = []

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= TITLE_MAX_LEN:
            raise ValueError(f"title must be between 1 and {TITLE_MAX_LEN} characters after trim")
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= DESCRIPTION_MAX_LEN:
            raise ValueError(
                f"description must be between 1 and {DESCRIPTION_MAX_LEN} characters after trim"
            )
        return value


class TicketUpdate(BaseModel):
    """Partial update of a ticket: status, priority and/or category."""

    status: str | None = None
    priority: str | None = None
    category: str | None = None
    assignee_ids: list[int] | None = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
        return value

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(ALLOWED_PRIORITIES)}")
        return value

    @field_validator("category")
    @classmethod
    def _validate_category(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_CATEGORIES:
            raise ValueError(f"category must be one of {sorted(ALLOWED_CATEGORIES)}")
        return value


class User(BaseModel):
    """A registered user as exposed by the API (never includes the password)."""

    id: int
    name: str
    email: str
    created_at: datetime


class Ticket(BaseModel):
    """A fully classified, persisted ticket as returned by the API."""

    id: int
    title: str
    description: str
    category: str
    priority: str
    tags: list[str]
    status: str
    assignees: list[User] = []
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    """Registration payload: name, email and a plaintext password to be hashed."""

    name: str
    email: str
    password: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= NAME_MAX_LEN:
            raise ValueError(f"name must be between 1 and {NAME_MAX_LEN} characters after trim")
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) > EMAIL_MAX_LEN or not _EMAIL_RE.match(value):
            raise ValueError("email must be a valid address")
        return value

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if not PASSWORD_MIN_LEN <= len(value) <= PASSWORD_MAX_LEN:
            raise ValueError(
                f"password must be between {PASSWORD_MIN_LEN} and {PASSWORD_MAX_LEN} characters"
            )
        return value


class UserLogin(BaseModel):
    """Login payload: email + password."""

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()
