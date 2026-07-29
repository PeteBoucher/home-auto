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

    reachable: list[tuple[Device, dict]] = []
    with Session(engine) as session:
        for device, state in zip(devices, states):
            if not isinstance(state, dict):
                continue
            db_device = session.get(Device, device.id)
            if not db_device:
                continue
            if not state.get("online"):
                # Unreachable this cycle (LAN blip/timeout) — _get_state_sync's error
                # handler reports this as state=False, brightness=None etc, which
                # isn't a real reading. Only flip online off; leave the last-known
                # state/brightness/colour alone so a transient timeout doesn't look
                # like the device actually turned off (and, with grouped devices,
                # doesn't cascade a false "off" onto the rest of the group).
                db_device.online = False
                session.add(db_device)
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
            reachable.append((device, state))
        session.commit()

    await asyncio.gather(
        *[check_state_triggers(d.id, s) for d, s in reachable],
        return_exceptions=True,
    )
    await asyncio.gather(
        *[propagate_member_change(d.id) for d, s in reachable],
        return_exceptions=True,
    )
