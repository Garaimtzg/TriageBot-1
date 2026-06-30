"""Domain models / Pydantic schemas for TriageBot."""

from __future__ import annotations

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


class TicketCreate(BaseModel):
    """Payload to create a ticket. Only title and description come from the client."""

    title: str
    description: str

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


class Ticket(BaseModel):
    """A fully classified, persisted ticket as returned by the API."""

    id: int
    title: str
    description: str
    category: str
    priority: str
    tags: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
