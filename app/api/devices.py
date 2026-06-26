import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.devices.models import Device, DeviceType, Integration
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client
from app.devices import hon as hon_client

_Z2M_PREFIX = "zigbee2mqtt"

_DEVICES_JSON = Path("devices.json")

_BULB_CATEGORIES = {"dj", "dd", "fwl", "xdd", "dc"}


def _load_devices_json() -> list[dict]:
    if not _DEVICES_JSON.exists():
        return []
    return json.loads(_DEVICES_JSON.read_text())


def _infer_type(data: dict) -> str:
    category = data.get("category", "").lower()
    name = data.get("name", "").lower()
    if category in _BULB_CATEGORIES or any(w in name for w in ("bulb", "light", "lamp")):
        return "bulb"
    return "plug"

router = APIRouter(prefix="/devices", tags=["devices"])
templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/import", response_class=HTMLResponse)
async def import_devices_page(request: Request, session: SessionDep):
    discovered = _load_devices_json()
    existing_ids = {d.device_id for d in session.exec(select(Device)).all()}
    for d in discovered:
        d["_type_guess"] = _infer_type(d)
        d["_registered"] = d["id"] in existing_ids
    return templates.TemplateResponse(
        request, "import_devices.html", {"discovered": discovered, "has_file": _DEVICES_JSON.exists()}
    )


@router.post("/import/{tuya_id}", response_class=HTMLResponse)
async def import_device(tuya_id: str, request: Request, session: SessionDep):
    data = next((d for d in _load_devices_json() if d["id"] == tuya_id), None)
    if not data:
        raise HTTPException(status_code=404)

    existing = session.exec(select(Device).where(Device.device_id == tuya_id)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Already registered")

    form = await request.form()
    device = Device(
        name=str(form.get("name", data["name"])),
        device_id=data["id"],
        local_key=data["key"],
        ip_address=str(form.get("ip_address", data.get("ip", ""))),
        type=DeviceType(str(form["type"])),
        integration=Integration.tuya,
    )
    session.add(device)
    session.commit()
    return HTMLResponse(
        f'<div class="bg-green-50 border border-green-200 rounded-xl p-4 flex items-center justify-between">'
        f'<span class="font-medium text-green-800">{device.name}</span>'
        f'<span class="text-xs text-green-600 font-medium">Added to dashboard</span>'
        f'</div>'
    )


@router.get("/hon", response_class=HTMLResponse)
async def hon_discover_page(request: Request, session: SessionDep):
    appliances = await hon_client.get_appliances()
    existing_ids = {d.device_id for d in session.exec(select(Device)).all()}
    items = []
    for a in appliances:
        uid = getattr(a, "unique_id", None) or getattr(a, "mac_address", None) or ""
        items.append({
            "uid": uid,
            "name": getattr(a, "nick_name", "") or getattr(a, "model_name", uid),
            "model": getattr(a, "model_name", ""),
            "type": getattr(a, "appliance_type", ""),
            "_registered": uid in existing_ids,
        })
    return templates.TemplateResponse(
        request, "hon_discover.html", {"appliances": items, "hon_available": bool(appliances) or hon_client._hon is not None}
    )


@router.post("/hon/{uid:path}", response_class=HTMLResponse)
async def hon_import_device(uid: str, request: Request, session: SessionDep):
    existing = session.exec(select(Device).where(Device.device_id == uid)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Already registered")
    form = await request.form()
    device = Device(
        name=str(form.get("name", uid)),
        device_id=uid,
        type=DeviceType(str(form.get("type", "ac"))),
        integration=Integration.hon,
    )
    session.add(device)
    session.commit()
    return HTMLResponse(
        f'<div class="bg-green-50 border border-green-200 rounded-xl p-4 flex items-center justify-between">'
        f'<span class="font-medium text-green-800">{device.name}</span>'
        f'<span class="text-xs text-green-600 font-medium">Added to dashboard</span>'
        f'</div>'
    )


@router.get("/z2m", response_class=HTMLResponse)
async def z2m_discover_page(request: Request, session: SessionDep):
    discovered = await mqtt_client.discover_devices()
    existing_ids = {d.device_id for d in session.exec(select(Device)).all()}
    devices = []
    if discovered:
        for d in discovered:
            d["_registered"] = d.get("friendly_name") in existing_ids
            d["_type_guess"] = _infer_type({"name": d.get("friendly_name", "")})
            devices.append(d)
    return templates.TemplateResponse(
        request, "z2m_discover.html", {"devices": devices, "broker_available": discovered is not None}
    )


@router.post("/z2m/{friendly_name:path}", response_class=HTMLResponse)
async def z2m_import_device(friendly_name: str, request: Request, session: SessionDep):
    existing = session.exec(select(Device).where(Device.device_id == friendly_name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Already registered")
    form = await request.form()
    device = Device(
        name=str(form.get("name", friendly_name)),
        device_id=friendly_name,
        type=DeviceType(str(form["type"])),
        integration=Integration.zigbee2mqtt,
    )
    session.add(device)
    session.commit()
    return HTMLResponse(
        f'<div class="bg-green-50 border border-green-200 rounded-xl p-4 flex items-center justify-between">'
        f'<span class="font-medium text-green-800">{device.name}</span>'
        f'<span class="text-xs text-green-600 font-medium">Added to dashboard</span>'
        f'</div>'
    )


@router.get("/add", response_class=HTMLResponse)
async def add_device_form(request: Request):
    return templates.TemplateResponse(request, "add_device.html")


@router.post("/add")
async def add_device(request: Request, session: SessionDep):
    form = await request.form()
    integration = Integration(str(form.get("integration", "tuya")))
    if integration == Integration.zigbee2mqtt:
        device = Device(
            name=str(form["name"]),
            device_id=str(form["z2m_topic"]),
            type=DeviceType(str(form["type"])),
            integration=Integration.zigbee2mqtt,
        )
    else:
        device = Device(
            name=str(form["name"]),
            device_id=str(form["device_id"]),
            local_key=str(form["local_key"]),
            ip_address=str(form["ip_address"]),
            type=DeviceType(str(form["type"])),
            integration=Integration.tuya,
        )
    session.add(device)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/{device_id}/command", response_class=HTMLResponse)
async def send_command(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)

    form = await request.form()
    command: dict = {}
    if "state" in form:
        command["state"] = str(form["state"]).lower() == "true"
    if "brightness" in form:
        command["brightness"] = int(form["brightness"])

    if device.integration == Integration.tuya:
        await tuya_client.send_command(device, command)
        state = await tuya_client.get_state(device)
        device.online = state["online"]
        device.state = state["state"]
        device.brightness = state["brightness"]
        session.add(device)
        session.commit()
        session.refresh(device)

    elif device.integration == Integration.hon:
        hon_command: dict = {}
        if "state" in form:
            hon_command["state"] = str(form["state"]).lower() == "true"
        if "temperature" in form:
            hon_command["temperature"] = int(form["temperature"])
        if "ac_mode" in form:
            hon_command["ac_mode"] = str(form["ac_mode"])
        if "fan_speed" in form:
            hon_command["fan_speed"] = int(form["fan_speed"])
        await hon_client.send_command(device.device_id, hon_command)
        state = await hon_client.get_state(device.device_id)
        device.online = state["online"]
        device.state = state["state"]
        device.temperature = state.get("temperature")
        device.ac_mode = state.get("ac_mode")
        device.fan_speed = state.get("fan_speed")
        session.add(device)
        session.commit()
        session.refresh(device)

    elif device.integration == Integration.zigbee2mqtt:
        payload: dict = {}
        if "state" in command:
            payload["state"] = "ON" if command["state"] else "OFF"
        if "brightness" in command:
            payload["brightness"] = round(command["brightness"] * 2.54)
        await mqtt_client.publish(f"{_Z2M_PREFIX}/{device.device_id}/set", payload)
        # Optimistic update — real confirmation arrives via MQTT subscription
        if "state" in command:
            device.state = command["state"]
        if "brightness" in command:
            device.brightness = command["brightness"]
        device.online = True
        session.add(device)
        session.commit()
        session.refresh(device)

    return templates.TemplateResponse(request, "partials/device_card.html", {"device": device})


@router.post("/{device_id}/delete")
async def delete_device(device_id: int, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    session.delete(device)
    session.commit()
    return HTMLResponse("")
