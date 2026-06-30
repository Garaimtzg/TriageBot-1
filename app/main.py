"""TriageBot FastAPI application."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import auth, classifier, db
from app.models import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    TicketCreate,
    TicketUpdate,
    UserCreate,
    UserLogin,
)

BASE_DIR = Path(__file__).resolve().parent.parent

# Load the project-local .env (if present) so OPENROUTER_API_KEY and other
# settings are picked up by default when the app starts (e.g. via uvicorn).
# Variables already set in the environment take precedence (override=False),
# so an explicit `export` still wins over the .env file.
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger("triagebot.main")

app = FastAPI(title="TriageBot")

# Cookie-based sessions for login. SESSION_SECRET comes from the environment;
# a development fallback keeps tests/local runs working without extra setup
# (set a real secret in production via .env).
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-insecure-session-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class NotAuthenticated(Exception):
    """Raised by HTML routes when there is no logged-in user."""


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def require_user(request: Request) -> dict:
    """Dependency for HTML routes: return the current user or redirect to /login."""
    user = auth.get_current_user(request)
    if user is None:
        raise NotAuthenticated()
    return user


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
        assignee_ids=payload.assignee_ids,
    )


# --- JSON API (unauthenticated programmatic contract) ----------------------


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


# --- Auth (login / register / logout) --------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if auth.get_current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"active": "login"})


@app.post("/login", response_model=None)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    creds = UserLogin(email=email, password=password)
    user = auth.authenticate(creds.email, creds.password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"active": "login", "error": "Email o contraseña incorrectos.", "email": email},
            status_code=401,
        )
    auth.login_session(request, user["id"])
    return RedirectResponse("/", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    if auth.get_current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "register.html", {"active": "register"})


@app.post("/register", response_model=None)
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    try:
        payload = UserCreate(name=name, email=email, password=password)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "active": "register",
                "error": (
                    "Revisa los datos: nombre, email válido y "
                    "contraseña de al menos 8 caracteres."
                ),
                "name": name,
                "email": email,
            },
            status_code=422,
        )

    if db.get_user_by_email(payload.email) is not None:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "active": "register",
                "error": "Ya existe un usuario con ese email.",
                "name": name,
                "email": email,
            },
            status_code=409,
        )

    user = db.create_user(
        name=payload.name,
        email=payload.email,
        password_hash=auth.hash_password(payload.password),
    )
    auth.login_session(request, user["id"])
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    auth.logout_session(request)
    return RedirectResponse("/login", status_code=303)


# --- Frontend (HTMX + Jinja2) ---------------------------------------------

PAGE_SIZE = 10


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
    assignee_id: int | None,
    scope: str,
) -> dict:
    """Shared context for the paginated board table fragment + page links."""
    category = category or None
    priority = priority or None
    status = status or None
    search = (q or "").strip() or None

    total = db.count_tickets(
        category=category, priority=priority, status=status, search=search,
        assignee_id=assignee_id,
    )
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    tickets = db.list_tickets(
        category=category,
        priority=priority,
        status=status,
        search=search,
        assignee_id=assignee_id,
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
        "scope": scope,
    }


def _scope_assignee_id(scope: str, user: dict) -> int | None:
    """Resolve the assignee filter: ``mine`` → current user, ``all`` → no filter."""
    return None if scope == "all" else user["id"]


@app.get("/", response_class=HTMLResponse)
def index(request: Request, user: dict = Depends(require_user)) -> HTMLResponse:
    """Page 1: create a ticket choosing its responsibles."""
    return templates.TemplateResponse(
        request,
        "create.html",
        {"users": db.list_users(), "current_user": user, "active": "create"},
    )


@app.get("/board", response_class=HTMLResponse)
def board(
    request: Request,
    user: dict = Depends(require_user),
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
    scope: str = "mine",
) -> HTMLResponse:
    """Page 2: paginated, filterable, searchable board.

    By default (``scope=mine``) only the current user's tickets are shown; the
    UI offers a toggle to ``scope=all`` to see everyone's tickets.
    """
    context = _board_context(
        category=category, priority=priority, status=status, q=q, page=page,
        assignee_id=_scope_assignee_id(scope, user), scope=scope,
    )
    return templates.TemplateResponse(
        request,
        "board.html",
        {**context, **_filter_options(), "current_user": user, "active": "board"},
    )


@app.get("/ui/tickets", response_class=HTMLResponse)
def ui_tickets_table(
    request: Request,
    user: dict = Depends(require_user),
    category: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
    scope: str = "mine",
) -> HTMLResponse:
    """HTML fragment with the paginated board table (HTMX live filter/search/paging)."""
    context = _board_context(
        category=category, priority=priority, status=status, q=q, page=page,
        assignee_id=_scope_assignee_id(scope, user), scope=scope,
    )
    return templates.TemplateResponse(request, "_board_table.html", context)


@app.get("/ui/tickets/{ticket_id}", response_class=HTMLResponse)
def ui_ticket_detail(
    request: Request, ticket_id: int, user: dict = Depends(require_user)
) -> HTMLResponse:
    """Modal fragment with the full ticket detail (all fields)."""
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return templates.TemplateResponse(
        request, "_ticket_detail.html", {"ticket": ticket, "users": db.list_users()}
    )


@app.post("/ui/tickets", response_class=HTMLResponse)
def ui_create_ticket(
    request: Request,
    user: dict = Depends(require_user),
    title: str = Form(...),
    description: str = Form(...),
    assignee_ids: list[int] = Form(default=[]),
) -> HTMLResponse:
    """Create a ticket from the form and return a confirmation card.

    The UI requires at least one responsible (assignee); requesting zero is a 422.
    Tickets themselves are listed on the board tab, not here. Sets the
    ``ticketCreated`` HX-Trigger so the page can show a success popup.
    """
    if not assignee_ids:
        raise HTTPException(status_code=422, detail="Debes asignar al menos un responsable")
    try:
        payload = TicketCreate(title=title, description=description, assignee_ids=assignee_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ticket = _create_ticket(payload)
    return templates.TemplateResponse(
        request,
        "_create_status.html",
        {"ticket": ticket},
        headers={"HX-Trigger": "ticketCreated"},
    )


@app.post("/ui/tickets/{ticket_id}/assignees", response_class=HTMLResponse)
def ui_update_assignees(
    request: Request,
    ticket_id: int,
    user: dict = Depends(require_user),
    assignee_ids: list[int] = Form(default=[]),
) -> HTMLResponse:
    """Reassign the responsibles of an existing ticket (from the detail modal)."""
    if db.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not assignee_ids:
        raise HTTPException(status_code=422, detail="Debes asignar al menos un responsable")
    ticket = db.update_ticket(ticket_id, {"assignee_ids": assignee_ids})
    return templates.TemplateResponse(
        request,
        "_ticket_detail.html",
        {"ticket": ticket, "users": db.list_users()},
        headers={"HX-Trigger": "ticketUpdated"},
    )
