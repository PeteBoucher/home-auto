import logging
import os

from app.devices import tuya as tuya_client
from app.devices.tuya import get_tuya_bulbs, restore_bulb_state
from app.services.weather import is_raining

log = logging.getLogger(__name__)

_RAIN_COLOUR = "#add8e6"  # pale blue

# In-memory rain state — resets on restart (acceptable: next poll re-applies)
_raining = False
_saved: dict[int, dict] = {}  # device DB id → pre-rain snapshot


async def _activate_rain():
    for bulb in get_tuya_bulbs():
        _saved[bulb.id] = await tuya_client.live_snapshot(bulb)
        await tuya_client.send_command(bulb, {"state": True})
        await tuya_client.send_command(bulb, {"color_rgb": _RAIN_COLOUR})


async def _deactivate_rain():
    for bulb in get_tuya_bulbs():
        snap = _saved.pop(bulb.id, None)
        if snap is None:
            continue
        await restore_bulb_state(bulb, snap)


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
