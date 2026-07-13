from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select, desc

from app.db import SessionDep
from app.devices.models import Event
from app.templating import templates

router = APIRouter()


def _latest_events(session: Session) -> list[Event]:
    return list(session.exec(
        select(Event).order_by(desc(Event.timestamp)).limit(200)
    ).all())


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, session: SessionDep):
    return templates.TemplateResponse(request, "history.html", {"events": _latest_events(session)})


@router.get("/history/rows", response_class=HTMLResponse)
async def history_rows(request: Request, session: SessionDep):
    return templates.TemplateResponse(request, "partials/history_rows.html", {"events": _latest_events(session)})
