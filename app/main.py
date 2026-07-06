import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session, init_db
from app.devices.models import Device, Integration, Schedule
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client
from app.devices import hon as hon_client
from app.api import devices as devices_router
from app.api import alerts as alerts_router
from app.services.automations import check_weather
from app.services.scheduler import scheduler, init_schedules

SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await hon_client.start()
    mqtt_task = asyncio.create_task(mqtt_client.run())
    scheduler.add_job(check_weather, "interval", minutes=10, next_run_time=datetime.now())
    scheduler.start()
    init_schedules()
    yield
    scheduler.shutdown()
    mqtt_task.cancel()
    try:
        await mqtt_task
    except asyncio.CancelledError:
        pass
    await hon_client.stop()


app = FastAPI(title="home-auto", lifespan=lifespan)
app.include_router(devices_router.router)
app.include_router(alerts_router.router)

templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: SessionDep):
    devices = list(session.exec(select(Device)).all())
    tuya_devices = [d for d in devices if d.integration == Integration.tuya]

    if tuya_devices:
        tuya_states = await asyncio.gather(
            *[tuya_client.get_state(d) for d in tuya_devices],
            return_exceptions=True,
        )
        for device, state in zip(tuya_devices, tuya_states):
            if isinstance(state, dict):
                device.online = state["online"]
                device.state = state["state"]
                device.brightness = state["brightness"]
                device.color_temp = state.get("color_temp")
                device.color_mode = state.get("color_mode", "white")
                device.color_rgb = state.get("color_rgb")
                session.add(device)

    hon_devices = [d for d in devices if d.integration == Integration.hon]
    if hon_devices:
        hon_states = await asyncio.gather(
            *[hon_client.get_state(d.device_id) for d in hon_devices],
            return_exceptions=True,
        )
        for device, state in zip(hon_devices, hon_states):
            if isinstance(state, dict):
                device.online = state["online"]
                device.state = state["state"]
                device.temperature = state.get("temperature")
                device.ac_mode = state.get("ac_mode")
                device.fan_speed = state.get("fan_speed")
                session.add(device)

    if tuya_devices or hon_devices:
        session.commit()
        devices = list(session.exec(select(Device)).all())

    schedules = {
        s.device_id: s
        for s in session.exec(select(Schedule)).all()
    }
    return templates.TemplateResponse(request, "index.html", {"devices": devices, "schedules": schedules})
