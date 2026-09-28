from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import SessionDep
from app.devices.models import Automation, Device, DeviceGroup, TriggerType
from app.services.automation_engine import apply_automation, remove_automation
from app.templating import templates

router = APIRouter(prefix="/automations", tags=["automations"])


def _render_row(request: Request, auto: Automation, session: Session) -> str:
    devices_by_id = {d.id: d for d in session.exec(select(Device)).all()}
    groups_by_id = {g.id: g for g in session.exec(select(DeviceGroup)).all()}
    return templates.env.get_template("partials/automation_row.html").render(
        request=request, auto=auto, devices_by_id=devices_by_id, groups_by_id=groups_by_id
    )


def _parse_action_target(form) -> tuple[int | None, int | None]:
    """Parses the form's single "action_target" field ("device:<id>" or
    "group:<id>") into (action_device_id, action_group_id) — exactly one set."""
    target_type, _, target_id = str(form.get("action_target", "")).partition(":")
    if not target_id:
        return None, None
    if target_type == "group":
        return None, int(target_id)
    return int(target_id), None


async def _parse_form(request: Request, auto: Automation | None = None) -> Automation:
    form = await request.form()
    action_device_id, action_group_id = _parse_action_target(form)
    if auto is None:
        auto = Automation(
            name="",
            trigger_type=TriggerType.time,
            action_device_id=action_device_id,
            action_group_id=action_group_id,
            action_type="set_state_on",
        )
    auto.name = str(form.get("name", "")).strip() or "Unnamed"
    auto.enabled = form.get("enabled") == "1"
    auto.trigger_type = TriggerType(str(form.get("trigger_type", "time")))
    auto.trigger_time = str(form.get("trigger_time", "")) or None
    raw_tdev = form.get("trigger_device_id")
    auto.trigger_device_id = int(str(raw_tdev)) if raw_tdev else None
    if auto.trigger_type == TriggerType.weather:
        # Fixed condition (only "raining" exists today) driven by its own
        # trigger_weather_value field — kept separate from trigger_value so it
        # doesn't collide with the device_state section's same-named input,
        # which stays present (just hidden) in the DOM either way.
        auto.trigger_field = "raining"
        auto.trigger_operator = "eq"
        auto.trigger_value = str(form.get("trigger_weather_value", "true"))
        auto.trigger_compare_field = None
    else:
        auto.trigger_field = str(form.get("trigger_field", "")) or None
        auto.trigger_operator = str(form.get("trigger_operator", "eq")) or "eq"
        auto.trigger_value = str(form.get("trigger_value", "")) or None
        auto.trigger_compare_field = str(form.get("trigger_compare_field", "")) or None
    auto.trigger_window_start = str(form.get("trigger_window_start", "")) or None
    auto.trigger_window_end = str(form.get("trigger_window_end", "")) or None
    auto.trigger_sun_event = str(form.get("trigger_sun_event", "")) or None
    raw_offset = form.get("trigger_sun_offset")
    auto.trigger_sun_offset = int(str(raw_offset)) if raw_offset not in (None, "") else 0
    auto.action_device_id = action_device_id
    auto.action_group_id = action_group_id
    auto.action_type = str(form.get("action_type", "set_state_on"))
    auto.action_value = str(form.get("action_value", "")) or None
    auto.action_snapshot_before = form.get("action_snapshot_before") == "1"
    return auto


@router.get("", response_class=HTMLResponse)
async def automations_page(request: Request, session: SessionDep):
    automations = list(session.exec(select(Automation)).all())
    devices = list(session.exec(select(Device)).all())
    devices_by_id = {d.id: d for d in devices}
    groups = list(session.exec(select(DeviceGroup)).all())
    groups_by_id = {g.id: g for g in groups}
    return templates.TemplateResponse(
        request, "automations.html",
        {"automations": automations, "devices": devices, "devices_by_id": devices_by_id, "groups_by_id": groups_by_id}
    )


@router.get("/new", response_class=HTMLResponse)
async def new_form(request: Request, session: SessionDep):
    devices = list(session.exec(select(Device)).all())
    groups = list(session.exec(select(DeviceGroup)).all())
    return templates.TemplateResponse(
        request, "partials/automation_form.html", {"auto": None, "devices": devices, "groups": groups}
    )


@router.get("/{auto_id}/edit", response_class=HTMLResponse)
async def edit_form(auto_id: int, request: Request, session: SessionDep):
    auto = session.get(Automation, auto_id)
    if not auto:
        raise HTTPException(status_code=404)
    devices = list(session.exec(select(Device)).all())
    groups = list(session.exec(select(DeviceGroup)).all())
    return templates.TemplateResponse(
        request, "partials/automation_form.html", {"auto": auto, "devices": devices, "groups": groups}
    )


@router.post("", response_class=HTMLResponse)
async def create_automation(request: Request, session: SessionDep):
    auto = await _parse_form(request)
    session.add(auto)
    session.commit()
    session.refresh(auto)
    await apply_automation(auto)
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
    await apply_automation(auto)
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
    await apply_automation(auto)
    row = _render_row(request, auto, session)
    return HTMLResponse(row + '\n<div id="automation-form" hx-swap-oob="innerHTML"></div>')
