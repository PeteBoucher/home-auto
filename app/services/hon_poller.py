import asyncio
import logging

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, Integration
from app.devices import hon as hon_client
from app.services.automation_engine import check_state_triggers

log = logging.getLogger(__name__)


async def poll_hon_devices() -> None:
    with Session(engine) as session:
        devices = list(session.exec(
            select(Device).where(Device.integration == Integration.hon)
        ).all())

    if not devices:
        return

    states = await asyncio.gather(
        *[hon_client.get_state(d.device_id) for d in devices],
        return_exceptions=True,
    )

    reachable: list[tuple[Device, dict]] = []
    with Session(engine) as session:
        for device, state in zip(devices, states):
            if not isinstance(state, dict):
                continue
            db_device = session.get(Device, device.id)
            if not db_device:
                continue
            if not state.get("online"):
                db_device.online = False
                session.add(db_device)
                continue
            db_device.online = True
            db_device.state = state["state"]
            db_device.temperature = state.get("temperature")
            db_device.ac_mode = state.get("ac_mode")
            db_device.fan_speed = state.get("fan_speed")
            db_device.eco = state.get("eco")
            db_device.quiet = state.get("quiet")
            db_device.louvre_position = state.get("louvre_position")
            db_device.indoor_temp = state.get("indoor_temp")
            db_device.outdoor_temp = state.get("outdoor_temp")
            db_device.ac_energy = state.get("ac_energy")
            session.add(db_device)
            reachable.append((device, state))
        session.commit()

    await asyncio.gather(
        *[check_state_triggers(d.id, s) for d, s in reachable],
        return_exceptions=True,
    )
