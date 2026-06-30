"""TriageBot FastAPI application."""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
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

BASE_DIR = Path(__file__).resolve().parent.parent

# Load the project-local .env (if present) so OPENROUTER_API_KEY and other
# settings are picked up by default when the app starts (e.g. via uvicorn).
# Variables already set in the environment take precedence (override=False),
# so an explicit `export` still wins over the .env file.
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger("triagebot.main")

app = FastAPI(title="TriageBot")

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
        logger.warning(
            "Clasificación SIN modelo: fallo inesperado del clasificador; "
            "se usa el valor por defecto."
        )
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

PAGE_SIZE = 10
RECENT_LIMIT = 6


def _filter_options() -> dict:
    return {
        "categories": sorted(ALLOWED_CATEGORIES),
        "priorities": sorted(ALLOWED_PRIORITIES),
        "statuses": sorted(ALLOWED_STATUSES),
    }


def _board_context(
    *,
    category: str | None,
    priority: str | None,
    status: str | None,
    q: str | None,
    page: int,
) -> dict:
    """Shared context for the paginated board table fragment + page links."""
    category = category or None
    priority = priority or None
    status = status or None
    search = (q or "").strip() or None

    total = db.count_tickets(
        category=category, priority=priority, status=status, search=search
    )
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    tickets = db.list_tickets(
        category=category,
        priority=priority,
        status=status,
        search=search,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    return {
        "tickets": tickets,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        # Echo filters back so pagination links preserve them.
        "f_category": category or "",
        "f_priority": priority or "",
        "f_status": status or "",
        "f_q": search or "",
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Page 1: create a ticket and see it appear in the recent list."""
    recent = db.list_tickets(limit=RECENT_LIMIT)
    return templates.TemplateResponse(
        request,
        "create.html",
        {"recent": recent, "active": "create"},
    )


@app.get("/board", response_class=HTMLResponse)
def board(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Page 2: paginated, filterable, searchable board."""
    context = _board_context(
        category=category, priority=priority, status=status, q=q, page=page
    )
    return templates.TemplateResponse(
        request,
        "board.html",
        {**context, **_filter_options(), "active": "board"},
    )


@app.get("/ui/tickets", response_class=HTMLResponse)
def ui_tickets_table(
    request: Request,
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """HTML fragment with the paginated board table (HTMX live filter/search/paging)."""
    context = _board_context(
        category=category, priority=priority, status=status, q=q, page=page
    )
    return templates.TemplateResponse(request, "_board_table.html", context)


@app.get("/ui/tickets/{ticket_id}", response_class=HTMLResponse)
def ui_ticket_detail(request: Request, ticket_id: int) -> HTMLResponse:
    """Modal fragment with the full ticket detail (all fields)."""
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return templates.TemplateResponse(
        request, "_ticket_detail.html", {"ticket": ticket}
    )


@app.post("/ui/tickets", response_class=HTMLResponse)
def ui_create_ticket(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
) -> HTMLResponse:
    """Create a ticket from the form and return the refreshed recent-tickets list.

    Sets the ``ticketCreated`` HX-Trigger so the page can show a success popup.
    """
    try:
        payload = TicketCreate(title=title, description=description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _create_ticket(payload)
    recent = db.list_tickets(limit=RECENT_LIMIT)
    return templates.TemplateResponse(
        request,
        "_recent_list.html",
        {"recent": recent},
        headers={"HX-Trigger": "ticketCreated"},
    )
