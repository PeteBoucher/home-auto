from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, desc

from app.db import get_session
from app.devices.models import Event
from typing import Annotated
from fastapi import Depends

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, session: SessionDep):
    events = list(session.exec(
        select(Event).order_by(desc(Event.timestamp)).limit(200)
    ).all())
    return templates.TemplateResponse(request, "history.html", {"events": events})


@router.get("/history/rows", response_class=HTMLResponse)
async def history_rows(request: Request, session: SessionDep):
    events = list(session.exec(
        select(Event).order_by(desc(Event.timestamp)).limit(200)
    ).all())
    return templates.TemplateResponse(request, "partials/history_rows.html", {"events": events})
