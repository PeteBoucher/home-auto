import asyncio
import logging
import threading

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceType, Integration
from app.devices import tuya as tuya_client
from app.devices.tuya import _rgb_hex_to_hsv_hex

log = logging.getLogger(__name__)

_RED = "#ff0000"
_FLASH_ON = 0.8
_FLASH_OFF = 0.8
_DURATION = 60

_active = False
_task: asyncio.Task | None = None
_stop = threading.Event()
_saved: dict[int, dict] = {}


def _tuya_bulbs() -> list[Device]:
    with Session(engine) as session:
        return list(session.exec(
            select(Device).where(
                Device.integration == Integration.tuya,
                Device.type == DeviceType.bulb,
            )
        ).all())


async def _restore(bulbs: list[Device]) -> None:
    for bulb in bulbs:
        snap = _saved.pop(bulb.id, None)
        if snap is None:
            continue
        if not snap["state"]:
            await tuya_client.send_command(bulb, {"state": False})
        elif snap["color_mode"] == "colour" and snap["color_rgb"]:
            await tuya_client.send_command(bulb, {"state": True})
            await tuya_client.send_command(bulb, {"color_rgb": snap["color_rgb"]})
        else:
            await tuya_client.send_command(bulb, {"state": True})
            if snap["brightness"] is not None:
                await tuya_client.send_command(bulb, {"brightness": snap["brightness"]})
            if snap["color_temp"] is not None:
                await tuya_client.send_command(bulb, {"color_temp": snap["color_temp"]})


async def _run(bulbs: list[Device]) -> None:
    global _active
    red_hsv = _rgb_hex_to_hsv_hex(_RED)
    try:
        # Each bulb gets its own thread with a persistent socket.
        await asyncio.gather(*[
            asyncio.to_thread(
                tuya_client.flash_sync, b, _stop, red_hsv, _FLASH_ON, _FLASH_OFF, _DURATION
            )
            for b in bulbs
        ])
    finally:
        _active = False
        await _restore(bulbs)


async def activate() -> None:
    global _active, _task, _stop
    if _active:
        return
    bulbs = _tuya_bulbs()
    for bulb in bulbs:
        _saved[bulb.id] = {
            "state": bulb.state,
            "color_mode": bulb.color_mode,
            "color_rgb": bulb.color_rgb,
            "brightness": bulb.brightness,
            "color_temp": bulb.color_temp,
        }
    _active = True
    _stop = threading.Event()
    _task = asyncio.create_task(_run(bulbs))


async def deactivate() -> None:
    global _task
    _stop.set()
    if _task and not _task.done():
        await _task
    _task = None


def is_active() -> bool:
    return _active
