import logging
import os

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceType, Integration
from app.devices import tuya as tuya_client
from app.services.weather import is_raining

log = logging.getLogger(__name__)

_RAIN_COLOUR = "#add8e6"  # pale blue

# In-memory rain state — resets on restart (acceptable: next poll re-applies)
_raining = False
_saved: dict[int, dict] = {}  # device DB id → pre-rain snapshot


def _tuya_bulbs() -> list[Device]:
    with Session(engine) as session:
        return list(session.exec(
            select(Device).where(
                Device.integration == Integration.tuya,
                Device.type == DeviceType.bulb,
            )
        ).all())


async def _activate_rain():
    for bulb in _tuya_bulbs():
        _saved[bulb.id] = {
            "state": bulb.state,
            "color_mode": bulb.color_mode,
            "color_rgb": bulb.color_rgb,
            "brightness": bulb.brightness,
            "color_temp": bulb.color_temp,
        }
        await tuya_client.send_command(bulb, {"state": True})
        await tuya_client.send_command(bulb, {"color_rgb": _RAIN_COLOUR})


async def _deactivate_rain():
    for bulb in _tuya_bulbs():
        snap = _saved.pop(bulb.id, None)
        if snap is None:
            continue
        if not snap["state"]:
            await tuya_client.send_command(bulb, {"state": False})
        elif snap["color_mode"] == "colour" and snap["color_rgb"]:
            await tuya_client.send_command(bulb, {"color_rgb": snap["color_rgb"]})
        else:
            if snap["brightness"] is not None:
                await tuya_client.send_command(bulb, {"brightness": snap["brightness"]})
            if snap["color_temp"] is not None:
                await tuya_client.send_command(bulb, {"color_temp": snap["color_temp"]})


async def check_weather():
    global _raining
    lat = float(os.getenv("LAT", "0"))
    lon = float(os.getenv("LON", "0"))
    if lat == 0 and lon == 0:
        log.warning("LAT/LON not configured — skipping weather check")
        return
    try:
        rain_now = await is_raining(lat, lon)
    except Exception as exc:
        log.warning("Weather check failed: %s", exc)
        return

    if rain_now and not _raining:
        log.info("Rain started — switching bulbs to pale blue")
        _raining = True
        await _activate_rain()
    elif not rain_now and _raining:
        log.info("Rain stopped — restoring bulbs")
        _raining = False
        await _deactivate_rain()
