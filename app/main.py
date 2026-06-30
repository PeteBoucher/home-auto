import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import engine, init_db
from app.devices.models import Device, Integration, DeviceType
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client
from app.devices import hon as hon_client
from app.api import devices as devices_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await hon_client.start()
    mqtt_task = asyncio.create_task(mqtt_client.run())
    yield
    mqtt_task.cancel()
    try:
        await mqtt_task
    except asyncio.CancelledError:
        pass
    await hon_client.stop()


app = FastAPI(title="home-auto", lifespan=lifespan)
app.include_router(devices_router.router)

templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    with Session(engine) as session:
        devices = list(session.exec(select(Device)).all())
        tuya_devices = [d for d in devices if d.integration == Integration.tuya]

        polls = []
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

    return templates.TemplateResponse(request, "index.html", {"devices": devices})
