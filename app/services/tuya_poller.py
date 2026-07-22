import asyncio
import logging

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, Integration
from app.devices import tuya as tuya_client
from app.services.automation_engine import check_state_triggers
from app.services.groups import propagate_member_change

log = logging.getLogger(__name__)


async def poll_tuya_devices() -> None:
    with Session(engine) as session:
        devices = list(session.exec(
            select(Device).where(Device.integration == Integration.tuya)
        ).all())

    if not devices:
        return

    states = await asyncio.gather(
        *[tuya_client.get_state(d) for d in devices],
        return_exceptions=True,
    )

    with Session(engine) as session:
        for device, state in zip(devices, states):
            if not isinstance(state, dict):
                continue
            db_device = session.get(Device, device.id)
            if not db_device:
                continue
            changed = {
                "state": state["state"],
                "brightness": state["brightness"],
                "online": state["online"],
                "color_temp": state.get("color_temp"),
                "color_mode": state.get("color_mode", "white"),
                "color_rgb": state.get("color_rgb"),
            }
            for k, v in changed.items():
                setattr(db_device, k, v)
            session.add(db_device)
        session.commit()

    await asyncio.gather(
        *[check_state_triggers(d.id, s) for d, s in zip(devices, states) if isinstance(s, dict)],
        return_exceptions=True,
    )
    await asyncio.gather(
        *[propagate_member_change(d.id) for d, s in zip(devices, states) if isinstance(s, dict)],
        return_exceptions=True,
    )
