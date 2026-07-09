import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session, init_db
from app.devices.models import Device, Schedule
from app.devices import mqtt as mqtt_client
from app.devices import hon as hon_client
from app.devices import firetv as firetv_client
from app.api import devices as devices_router
from app.api import alerts as alerts_router
from app.api import automations as automations_router
from app.api import history as history_router
from app.api import network as network_router
from app.services.automations import check_weather
from app.services.scheduler import scheduler, init_schedules
from app.services.automation_engine import load_time_automations
from app.services.tuya_poller import poll_tuya_devices

SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await hon_client.start()
    mqtt_task = asyncio.create_task(mqtt_client.run())
    firetv_task = asyncio.create_task(firetv_client.run())
    scheduler.add_job(check_weather, "interval", minutes=10, next_run_time=datetime.now())
    scheduler.add_job(poll_tuya_devices, "interval", seconds=30, next_run_time=datetime.now())
    scheduler.start()
    init_schedules()
    load_time_automations()
    yield
    scheduler.shutdown()
    for task in (mqtt_task, firetv_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await hon_client.stop()


app = FastAPI(title="home-auto", lifespan=lifespan)
app.include_router(devices_router.router)
app.include_router(alerts_router.router)
app.include_router(automations_router.router)
app.include_router(history_router.router)
app.include_router(network_router.router)

templates = Jinja2Templates(directory="app/templates")
templates.env.cache = None


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: SessionDep):
    devices = list(session.exec(select(Device)).all())
    schedules = {
        s.device_id: s
        for s in session.exec(select(Schedule)).all()
    }
    return templates.TemplateResponse(request, "index.html", {
        "devices": devices,
        "schedules": schedules,
        "roachcam_url": os.getenv("ROACHCAM_URL", "").rstrip("/"),
    })
