import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.db import SessionDep
from app.devices.models import ClimateSample, Device, DeviceType, Integration, PowerSample, Schedule
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client
from app.devices import hon as hon_client
from app.devices import firetv as firetv_client
from app.devices.zigbee_color import pct_to_mireds, rgb_hex_to_hs_brightness
from app.services.scheduler import apply_schedule, remove_schedule
from app.services.automation_engine import check_state_triggers
from app.templating import templates

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
    if any(w in name for w in ("sensor", "temperature", "humidity", "temp", "thermo")):
        return "sensor"
    return "plug"

def _get_schedule(device_id: int, session: Session) -> Schedule | None:
    return session.exec(select(Schedule).where(Schedule.device_id == device_id)).first()


def _import_success(name: str) -> HTMLResponse:
    return HTMLResponse(
        f'<div class="bg-green-50 border border-green-200 rounded-xl p-4 flex items-center justify-between">'
        f'<span class="font-medium text-green-800">{name}</span>'
        f'<span class="text-xs text-green-600 font-medium">Added to dashboard</span>'
        f'</div>'
    )


router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("/grid", response_class=HTMLResponse)
async def device_grid(request: Request, session: SessionDep):
    devices = list(session.exec(select(Device)).all())
    schedules = {s.device_id: s for s in session.exec(select(Schedule)).all()}
    return templates.TemplateResponse(
        request, "partials/device_grid.html", {"devices": devices, "schedules": schedules}
    )


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
        protocol_version=float(data.get("version", 3.3)),
    )
    session.add(device)
    session.commit()
    return _import_success(device.name)


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
    return _import_success(device.name)


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


@router.post("/z2m/permit_join", response_class=HTMLResponse)
async def z2m_permit_join(request: Request):
    await mqtt_client.publish(
        f"{mqtt_client.PREFIX}/bridge/request/permit_join",
        {"value": True, "time": 120},
    )
    return HTMLResponse(
        '<button disabled '
        'class="flex items-center gap-2 bg-green-600 text-white px-3 py-2 rounded-lg text-sm font-medium">'
        '<span class="animate-pulse w-2 h-2 rounded-full bg-white inline-block"></span>'
        'Pairing open (2 min)'
        '</button>'
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
        dimmable=bool(form.get("dimmable")),
    )
    session.add(device)
    session.commit()
    return _import_success(device.name)


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


@router.get("/{device_id}/name", response_class=HTMLResponse)
async def device_name(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "partials/device_name.html", {"device": device})


@router.get("/{device_id}/rename-form", response_class=HTMLResponse)
async def rename_form(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "partials/rename_form.html", {"device": device})


@router.post("/{device_id}/rename", response_class=HTMLResponse)
async def rename_device(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if name:
        device.name = name
        device.room = str(form.get("room", "")).strip() or None
        session.add(device)
        session.commit()
        session.refresh(device)
    return templates.TemplateResponse(request, "partials/device_name.html", {"device": device})


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
    if "color_temp" in form:
        command["color_temp"] = int(form["color_temp"])
    if "color_mode" in form:
        command["color_mode"] = str(form["color_mode"])
    if "color_rgb" in form:
        command["color_rgb"] = str(form["color_rgb"])

    if device.integration == Integration.tuya:
        await tuya_client.send_command(device, command)
        state = await tuya_client.get_state(device)
        device.online = state["online"]
        device.state = state["state"]
        device.brightness = state["brightness"]
        device.color_temp = state.get("color_temp")
        device.color_mode = state.get("color_mode", "white")
        device.color_rgb = state.get("color_rgb")
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
        if "color_temp" in command:
            payload["color_temp"] = pct_to_mireds(command["color_temp"])
        if "color_rgb" in command:
            hue, saturation, brightness = rgb_hex_to_hs_brightness(command["color_rgb"])
            payload["color"] = {"hue": hue, "saturation": saturation}
            payload["brightness"] = brightness
        if payload:
            await mqtt_client.publish(f"{mqtt_client.PREFIX}/{device.device_id}/set", payload)
        # Optimistic update — real confirmation arrives via MQTT subscription
        if "state" in command:
            device.state = command["state"]
        if "brightness" in command:
            device.brightness = command["brightness"]
        if "color_temp" in command:
            device.color_temp = command["color_temp"]
            device.color_mode = "white"
        if "color_rgb" in command:
            device.color_rgb = command["color_rgb"]
            device.color_mode = "colour"
        if "color_mode" in command and "color_temp" not in command and "color_rgb" not in command:
            device.color_mode = command["color_mode"]
        device.online = True
        session.add(device)
        session.commit()
        session.refresh(device)

    schedule = _get_schedule(device.id, session)
    return templates.TemplateResponse(request, "partials/device_card.html", {"device": device, "schedule": schedule})


@router.post("/{device_id}/key", response_class=HTMLResponse)
async def send_key(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not firetv_client.ENABLED or not device or device.integration != Integration.firetv:
        raise HTTPException(status_code=404)
    form = await request.form()
    await firetv_client.send_key(str(form.get("action", "")))
    schedule = _get_schedule(device.id, session)
    return templates.TemplateResponse(request, "partials/device_card.html", {"device": device, "schedule": schedule})


@router.post("/{device_id}/schedule", response_class=HTMLResponse)
async def upsert_schedule(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    form = await request.form()
    on_time = str(form.get("on_time", "")).strip()
    off_time = str(form.get("off_time", "")).strip()
    if not on_time and not off_time:
        schedule = _get_schedule(device_id, session)
        return templates.TemplateResponse(
            request, "partials/device_schedule.html",
            {"device": device, "schedule": schedule, "error": "Set at least one time."},
        )
    schedule = _get_schedule(device_id, session)
    if not schedule:
        schedule = Schedule(device_id=device_id, on_time="", off_time="")
        session.add(schedule)
    is_new = schedule.id is None
    schedule.on_time = on_time
    schedule.off_time = off_time
    schedule.enabled = True if is_new else form.get("enabled") == "1"
    session.commit()
    session.refresh(schedule)
    apply_schedule(schedule)
    return templates.TemplateResponse(
        request, "partials/device_schedule.html", {"device": device, "schedule": schedule}
    )


@router.post("/{device_id}/schedule/delete", response_class=HTMLResponse)
async def delete_schedule(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    schedule = _get_schedule(device_id, session)
    if schedule:
        remove_schedule(schedule.id)
        session.delete(schedule)
        session.commit()
    return templates.TemplateResponse(
        request, "partials/device_schedule.html", {"device": device, "schedule": None}
    )


@router.get("/{device_id}/settings", response_class=HTMLResponse)
async def device_settings_page(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "device_settings.html", {"device": device})


@router.post("/{device_id}/settings/overload_protection", response_class=HTMLResponse)
async def set_overload_protection(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    form = await request.form()
    op: dict = {}
    for key in ("max_power", "max_current", "max_voltage", "min_voltage", "min_power", "min_current"):
        if key in form and str(form[key]).strip():
            op[key] = float(form[key])
    for key in ("enable_max_voltage", "enable_min_voltage", "enable_min_power", "enable_min_current"):
        op[key] = "ENABLE" if key in form else "DISABLE"
    await mqtt_client.publish(f"{mqtt_client.PREFIX}/{device.device_id}/set", {"overload_protection": op})
    device.overload_protection = json.dumps(op)
    session.add(device)
    session.commit()
    return HTMLResponse('<p class="text-sm text-green-600 font-medium">Overload protection saved.</p>')


@router.post("/{device_id}/settings/power_on_behavior", response_class=HTMLResponse)
async def set_power_on_behavior(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    form = await request.form()
    value = str(form["power_on_behavior"])
    if value not in ("on", "off", "previous"):
        raise HTTPException(status_code=400)
    await mqtt_client.publish(f"{mqtt_client.PREFIX}/{device.device_id}/set", {"power_on_behavior": value})
    device.power_on_behavior = value
    session.add(device)
    session.commit()
    return HTMLResponse(
        f'<p class="text-sm text-green-600 font-medium">Saved — will restore to <strong>{value}</strong> after power cut.</p>'
    )


@router.get("/{device_id}/power-chart", response_class=HTMLResponse)
async def power_chart_page(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "power_chart.html", {"device": device})


@router.get("/{device_id}/power-chart/data")
async def power_chart_data(device_id: int, session: SessionDep, hours: int = Query(default=6, ge=1, le=168)):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    samples = session.exec(
        select(PowerSample)
        .where(PowerSample.device_id == device_id, PowerSample.timestamp >= cutoff)
        .order_by(PowerSample.timestamp)
    ).all()
    return {
        "timestamps": [s.timestamp.isoformat() for s in samples],
        "voltage": [s.voltage for s in samples],
        "power": [s.power for s in samples],
        "current": [s.current for s in samples],
    }


@router.get("/{device_id}/climate-chart", response_class=HTMLResponse)
async def climate_chart_page(device_id: int, request: Request, session: SessionDep):
    device = session.get(Device, device_id)
    if not device or device.type != DeviceType.sensor:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "climate_chart.html", {"device": device})


@router.get("/{device_id}/climate-chart/data")
async def climate_chart_data(device_id: int, session: SessionDep, hours: int = Query(default=6, ge=1, le=168)):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    samples = session.exec(
        select(ClimateSample)
        .where(ClimateSample.device_id == device_id, ClimateSample.timestamp >= cutoff)
        .order_by(ClimateSample.timestamp)
    ).all()
    return {
        "timestamps": [s.timestamp.isoformat() for s in samples],
        "temperature": [s.temperature for s in samples],
        "humidity": [s.humidity for s in samples],
    }


@router.post("/{device_id}/delete")
async def delete_device(device_id: int, session: SessionDep):
    device = session.get(Device, device_id)
    if not device:
        raise HTTPException(status_code=404)
    session.delete(device)
    session.commit()
    return HTMLResponse("")
