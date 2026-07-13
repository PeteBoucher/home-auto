import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, Integration, Schedule
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()



async def _send(device_id: int, state: bool) -> None:
    with Session(engine) as session:
        device = session.get(Device, device_id)
    if not device:
        log.warning("Schedule: device %d not found", device_id)
        return
    log.info("Schedule: turning %s %s", device.name, "on" if state else "off")
    if device.integration == Integration.tuya:
        await tuya_client.send_command(device, {"state": state})
    elif device.integration == Integration.zigbee2mqtt:
        await mqtt_client.publish(
            f"{mqtt_client.PREFIX}/{device.device_id}/set",
            {"state": "ON" if state else "OFF"},
        )


def _load_jobs(schedule: Schedule) -> None:
    for suffix, time_str, state in [
        ("on",  schedule.on_time,  True),
        ("off", schedule.off_time, False),
    ]:
        job_id = f"sched_{schedule.id}_{suffix}"
        existing = scheduler.get_job(job_id)
        if schedule.enabled and time_str:
            h, m = map(int, time_str.split(":"))
            scheduler.add_job(
                _send, "cron", hour=h, minute=m,
                id=job_id,
                args=[schedule.device_id, state],
                replace_existing=True,
            )
        elif existing:
            existing.remove()


def _remove_jobs(schedule_id: int) -> None:
    for suffix in ("on", "off"):
        job = scheduler.get_job(f"sched_{schedule_id}_{suffix}")
        if job:
            job.remove()


def apply_schedule(schedule: Schedule) -> None:
    _load_jobs(schedule)


def remove_schedule(schedule_id: int) -> None:
    _remove_jobs(schedule_id)


def init_schedules() -> None:
    with Session(engine) as session:
        schedules = list(session.exec(select(Schedule)).all())
    for s in schedules:
        _load_jobs(s)
