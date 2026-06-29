"""TriageBot FastAPI application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import classifier, db
from app.models import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    TicketCreate,
    TicketUpdate,
)

app = FastAPI(title="TriageBot")

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _classify_safely(title: str, description: str) -> dict:
    """Run the classifier, never letting an SDK error reach the endpoint.

    Note: ``classifier.classify_ticket`` is referenced through the module so the
    test suite can monkeypatch it.
    """
    try:
        result = classifier.classify_ticket(title, description)
        return {
            "category": result["category"],
            "priority": result["priority"],
            "tags": result.get("tags", []),
        }
    except Exception:
        return dict(classifier.FALLBACK_CLASSIFICATION)


def _create_ticket(payload: TicketCreate) -> dict:
    classification = _classify_safely(payload.title, payload.description)
    return db.create_ticket(
        title=payload.title,
        description=payload.description,
        category=classification["category"],
        priority=classification["priority"],
        tags=classification["tags"],
    )


@app.post("/tickets", status_code=201)
def create_ticket(payload: TicketCreate) -> JSONResponse:
    ticket = _create_ticket(payload)
    return JSONResponse(status_code=201, content=ticket)


@app.get("/tickets")
def list_tickets(
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> list[dict]:
    return db.list_tickets(category=category, priority=priority, status=status)


@app.patch("/tickets/{ticket_id}")
def update_ticket(ticket_id: int, payload: TicketUpdate) -> dict:
    if db.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return db.update_ticket(ticket_id, payload.model_dump(exclude_unset=True))


# --- Frontend (HTMX + Jinja2) ---------------------------------------------


def _filter_options() -> dict:
    return {
        "categories": sorted(ALLOWED_CATEGORIES),
        "priorities": sorted(ALLOWED_PRIORITIES),
        "statuses": sorted(ALLOWED_STATUSES),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    tickets = db.list_tickets()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"tickets": tickets, **_filter_options()},
    )


@app.get("/ui/tickets", response_class=HTMLResponse)
def ui_tickets_table(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    """HTML fragment with the tickets table, used by HTMX for live filtering."""
    tickets = db.list_tickets(
        category=category or None,
        priority=priority or None,
        status=status or None,
    )
    return templates.TemplateResponse(
        request, "_tickets_table.html", {"tickets": tickets}
    )


@app.post("/ui/tickets", response_class=HTMLResponse)
def ui_create_ticket(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
) -> HTMLResponse:
    """Create a ticket from the HTML form and return the refreshed table fragment."""
    try:
        payload = TicketCreate(title=title, description=description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _create_ticket(payload)
    tickets = db.list_tickets()
    return templates.TemplateResponse(
        request, "_tickets_table.html", {"tickets": tickets}
    )
