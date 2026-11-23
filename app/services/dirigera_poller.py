"""Background polling of environment sensors (air quality monitors) on the
IKEA DIRIGERA hub.

Sensors are auto-registered the first time they're seen — type `sensor`, so
the dashboard card, per-device climate chart and climate widget all pick
them up like any Zigbee temp/humidity sensor. Name/room are only taken from
the hub at registration; after that the dashboard's own rename is the source
of truth and isn't overwritten.

An unreachable sensor still gets the hub's last-known readings stored (the
card shows them under its Offline badge, same as a quiet Zigbee sensor), but
no ClimateSample is recorded from them — they're stale, not a new reading.

The hub itself only refreshes a sensor's readings every few minutes (its
`lastSeen`), so a ClimateSample is recorded only when that advances —
polling every 60s would otherwise write the same held value repeatedly.
"""
import logging

from sqlmodel import Session, select

from app.db import engine
from app.devices import dirigera as dirigera_client
from app.devices.models import ClimateSample, Device, DeviceType, Integration
from app.services.automation_engine import check_state_triggers

log = logging.getLogger(__name__)

_READING_FIELDS = ("sensor_temperature", "humidity", "co2", "pm25", "window_open")

# Device.id -> hub lastSeen of the last reading we sampled. In-memory: after a
# restart the first poll records one (possibly repeated) sample, which is harmless.
_last_sampled: dict[int, str] = {}


async def poll_dirigera_devices() -> None:
    if not dirigera_client.is_configured():
        return
    try:
        sensors = await dirigera_client.get_environment_sensors()
    except Exception as exc:
        log.warning("DIRIGERA poll failed: %s", exc)
        return

    triggers: list[tuple[int, dict]] = []
    with Session(engine) as session:
        for reading in sensors:
            device = session.exec(
                select(Device).where(Device.device_id == reading["hub_id"])
            ).first()
            if device is None:
                device = Device(
                    name=reading["name"],
                    room=reading["room"],
                    device_id=reading["hub_id"],
                    type=DeviceType.sensor,
                    integration=Integration.dirigera,
                )
                log.info("Registered DIRIGERA sensor %r (%s)", reading["name"], reading["hub_id"])

            device.online = reading["online"]
            for field in _READING_FIELDS:
                if reading[field] is not None:
                    setattr(device, field, reading[field])
            session.add(device)
            session.flush()

            if reading["online"] and reading["last_seen"] != _last_sampled.get(device.id):
                session.add(ClimateSample(
                    device_id=device.id,
                    temperature=device.sensor_temperature,
                    humidity=device.humidity,
                    co2=device.co2,
                    pm25=device.pm25,
                ))
                _last_sampled[device.id] = reading["last_seen"]

            triggers.append((device.id, {
                "online": device.online,
                **{f: getattr(device, f) for f in _READING_FIELDS},
            }))
        session.commit()

    for device_id, state in triggers:
        await check_state_triggers(device_id, state)
