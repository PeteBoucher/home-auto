from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.devices.models import Automation, Device, TriggerType
from app.services.automation_engine import apply_automation, remove_automation

router = APIRouter(prefix="/automations", tags=["automations"])
templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None

SessionDep = Annotated[Session, Depends(get_session)]


def _render_row(request: Request, auto: Automation, session: Session) -> str:
    devices_by_id = {d.id: d for d in session.exec(select(Device)).all()}
    return templates.env.get_template("partials/automation_row.html").render(
        request=request, auto=auto, devices_by_id=devices_by_id
    )


async def _parse_form(request: Request, auto: Automation | None = None) -> Automation:
    form = await request.form()
    if auto is None:
        auto = Automation(
            name="",
            trigger_type=TriggerType.time,
            action_device_id=int(str(form.get("action_device_id", 0))),
            action_type="set_state_on",
        )
    auto.name = str(form.get("name", "")).strip() or "Unnamed"
    auto.enabled = form.get("enabled") == "1"
    auto.trigger_type = TriggerType(str(form.get("trigger_type", "time")))
    auto.trigger_time = str(form.get("trigger_time", "")) or None
    raw_tdev = form.get("trigger_device_id")
    auto.trigger_device_id = int(str(raw_tdev)) if raw_tdev else None
    auto.trigger_field = str(form.get("trigger_field", "")) or None
    auto.trigger_operator = str(form.get("trigger_operator", "eq")) or "eq"
    auto.trigger_value = str(form.get("trigger_value", "")) or None
    auto.action_device_id = int(str(form.get("action_device_id", 0)))
    auto.action_type = str(form.get("action_type", "set_state_on"))
    auto.action_value = str(form.get("action_value", "")) or None
    return auto


@router.get("", response_class=HTMLResponse)
async def automations_page(request: Request, session: SessionDep):
    automations = list(session.exec(select(Automation)).all())
    devices = list(session.exec(select(Device)).all())
    devices_by_id = {d.id: d for d in devices}
    return templates.TemplateResponse(
        request, "automations.html",
        {"automations": automations, "devices": devices, "devices_by_id": devices_by_id}
    )


@router.get("/new", response_class=HTMLResponse)
async def new_form(request: Request, session: SessionDep):
    devices = list(session.exec(select(Device)).all())
    return templates.TemplateResponse(
        request, "partials/automation_form.html", {"auto": None, "devices": devices}
    )


@router.get("/{auto_id}/edit", response_class=HTMLResponse)
async def edit_form(auto_id: int, request: Request, session: SessionDep):
    auto = session.get(Automation, auto_id)
    if not auto:
        raise HTTPException(status_code=404)
    devices = list(session.exec(select(Device)).all())
    return templates.TemplateResponse(
        request, "partials/automation_form.html", {"auto": auto, "devices": devices}
    )


@router.post("", response_class=HTMLResponse)
async def create_automation(request: Request, session: SessionDep):
    auto = await _parse_form(request)
    session.add(auto)
    session.commit()
    session.refresh(auto)
    apply_automation(auto)
    row = _render_row(request, auto, session)
    return HTMLResponse(row + '\n<div id="automation-form" hx-swap-oob="innerHTML"></div>')


@router.post("/{auto_id}/toggle", response_class=HTMLResponse)
async def toggle_automation(auto_id: int, request: Request, session: SessionDep):
    auto = session.get(Automation, auto_id)
    if not auto:
        raise HTTPException(status_code=404)
    auto.enabled = not auto.enabled
    session.add(auto)
    session.commit()
    session.refresh(auto)
    apply_automation(auto)
    return HTMLResponse(_render_row(request, auto, session))


@router.post("/{auto_id}/delete", response_class=HTMLResponse)
async def delete_automation(auto_id: int, session: SessionDep):
    auto = session.get(Automation, auto_id)
    if not auto:
        raise HTTPException(status_code=404)
    remove_automation(auto_id)
    session.delete(auto)
    session.commit()
    return HTMLResponse("")


@router.post("/{auto_id}", response_class=HTMLResponse)
async def update_automation(auto_id: int, request: Request, session: SessionDep):
    auto = session.get(Automation, auto_id)
    if not auto:
        raise HTTPException(status_code=404)
    auto = await _parse_form(request, auto)
    session.add(auto)
    session.commit()
    session.refresh(auto)
    apply_automation(auto)
    row = _render_row(request, auto, session)
    return HTMLResponse(row + '\n<div id="automation-form" hx-swap-oob="innerHTML"></div>')
