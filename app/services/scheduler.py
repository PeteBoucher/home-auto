import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, Integration, Schedule
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

_Z2M_PREFIX = "zigbee2mqtt"


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
            f"{_Z2M_PREFIX}/{device.device_id}/set",
            {"state": "ON" if state else "OFF"},
        )


def _load_jobs(schedule: Schedule) -> None:
    on_h, on_m = map(int, schedule.on_time.split(":"))
    off_h, off_m = map(int, schedule.off_time.split(":"))
    if schedule.enabled:
        scheduler.add_job(
            _send, "cron", hour=on_h, minute=on_m,
            id=f"sched_{schedule.id}_on",
            args=[schedule.device_id, True],
            replace_existing=True,
        )
        scheduler.add_job(
            _send, "cron", hour=off_h, minute=off_m,
            id=f"sched_{schedule.id}_off",
            args=[schedule.device_id, False],
            replace_existing=True,
        )
    else:
        _remove_jobs(schedule.id)


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
