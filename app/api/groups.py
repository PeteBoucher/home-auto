from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from app.db import SessionDep
from app.devices.models import Device, DeviceGroup, Integration
from app.services.groups import create_group, delete_group, send_group_command, set_group_members
from app.templating import templates

router = APIRouter(prefix="/groups", tags=["groups"])

_GROUPABLE = (Integration.tuya, Integration.zigbee2mqtt)


def _groupable_devices(session) -> list[Device]:
    return list(session.exec(select(Device).where(Device.integration.in_(_GROUPABLE))).all())


@router.get("", response_class=HTMLResponse)
async def groups_page(request: Request, session: SessionDep):
    groups = list(session.exec(select(DeviceGroup)).all())
    devices = _groupable_devices(session)
    members_by_group = {g.id: [d for d in devices if d.group_id == g.id] for g in groups}
    ungrouped = [d for d in devices if d.group_id is None]
    return templates.TemplateResponse(request, "groups.html", {
        "groups": groups,
        "members_by_group": members_by_group,
        "ungrouped": ungrouped,
        "all_devices": {d.id: d for d in devices},
    })


@router.post("", response_class=HTMLResponse)
async def create_group_route(request: Request, session: SessionDep):
    form = await request.form()
    name = str(form.get("name", "")).strip() or "Unnamed group"
    device_ids = [int(v) for v in form.getlist("device_ids")]
    await create_group(session, name, device_ids)
    return RedirectResponse(url="/groups", status_code=303)


@router.post("/{group_id}/members", response_class=HTMLResponse)
async def update_members_route(group_id: int, request: Request, session: SessionDep):
    group = session.get(DeviceGroup, group_id)
    if not group:
        raise HTTPException(status_code=404)
    form = await request.form()
    device_ids = [int(v) for v in form.getlist("device_ids")]
    await set_group_members(session, group, device_ids)
    return RedirectResponse(url="/groups", status_code=303)


@router.post("/{group_id}/delete", response_class=HTMLResponse)
async def delete_group_route(group_id: int, session: SessionDep):
    group = session.get(DeviceGroup, group_id)
    if not group:
        raise HTTPException(status_code=404)
    await delete_group(session, group)
    return HTMLResponse("")


@router.post("/{group_id}/command", response_class=HTMLResponse)
async def group_command_route(group_id: int, request: Request, session: SessionDep):
    group = session.get(DeviceGroup, group_id)
    if not group:
        raise HTTPException(status_code=404)

    form = await request.form()
    command: dict = {}
    if "state" in form:
        command["state"] = str(form["state"]).lower() == "true"
    if "brightness" in form:
        command["brightness"] = int(form["brightness"])
    if "color_temp" in form:
        command["color_temp"] = int(form["color_temp"])
    if "color_mode" in form:
        command["color_mode"] = str(form["color_mode"])
    if "color_rgb" in form:
        command["color_rgb"] = str(form["color_rgb"])

    await send_group_command(session, group, command)
    session.refresh(group)
    members = list(session.exec(select(Device).where(Device.group_id == group.id)).all())
    return templates.TemplateResponse(request, "partials/group_card.html", {"group": group, "members": members})
