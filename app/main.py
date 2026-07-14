import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete
from sqlmodel import Session, select

from app.db import SessionDep, engine, init_db
from app.devices.models import Device, PowerSample, Schedule
from app.templating import templates
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
from app.services.automation_engine import load_time_automations, refresh_sun_jobs
from app.services.tuya_poller import poll_tuya_devices

def _prune_power_samples() -> None:
    cutoff = datetime.utcnow() - timedelta(days=7)
    with Session(engine) as session:
        session.exec(delete(PowerSample).where(PowerSample.timestamp < cutoff))
        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await hon_client.start()
    mqtt_task = asyncio.create_task(mqtt_client.run())
    firetv_task = asyncio.create_task(firetv_client.run()) if firetv_client.ENABLED else None
    scheduler.add_job(check_weather, "interval", minutes=10, next_run_time=datetime.now())
    scheduler.add_job(poll_tuya_devices, "interval", seconds=30, next_run_time=datetime.now())
    scheduler.add_job(_prune_power_samples, "interval", hours=24)
    scheduler.add_job(refresh_sun_jobs, "interval", hours=24, next_run_time=datetime.now())
    scheduler.start()
    init_schedules()
    load_time_automations()
    yield
    scheduler.shutdown()
    for task in filter(None, (mqtt_task, firetv_task)):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await hon_client.stop()


app = FastAPI(title="home-auto", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(devices_router.router)
app.include_router(alerts_router.router)
app.include_router(automations_router.router)
app.include_router(history_router.router)
app.include_router(network_router.router)

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
