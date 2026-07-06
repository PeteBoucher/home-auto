import asyncio
import logging
import time

from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceType, Integration
from app.devices import tuya as tuya_client

log = logging.getLogger(__name__)

_RED = "#ff0000"
_FLASH_ON = 0.5
_FLASH_OFF = 0.5
_DURATION = 60

_active = False
_task: asyncio.Task | None = None
_saved: dict[int, dict] = {}


def _tuya_bulbs() -> list[Device]:
    with Session(engine) as session:
        return list(session.exec(
            select(Device).where(
                Device.integration == Integration.tuya,
                Device.type == DeviceType.bulb,
            )
        ).all())


async def _set_all(bulbs: list[Device], **command) -> None:
    await asyncio.gather(*[tuya_client.send_command(b, command) for b in bulbs])


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


async def _flash_loop(bulbs: list[Device]) -> None:
    global _active
    deadline = time.monotonic() + _DURATION
    try:
        while _active and time.monotonic() < deadline:
            t = time.monotonic()
            await _set_all(bulbs, state=True, color_rgb=_RED)
            await asyncio.sleep(max(0, _FLASH_ON - (time.monotonic() - t)))

            t = time.monotonic()
            await _set_all(bulbs, state=False)
            await asyncio.sleep(max(0, _FLASH_OFF - (time.monotonic() - t)))
    except asyncio.CancelledError:
        pass
    finally:
        _active = False
        await _restore(bulbs)


async def activate() -> None:
    global _active, _task
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
    _task = asyncio.create_task(_flash_loop(bulbs))


async def deactivate() -> None:
    global _active, _task
    _active = False
    if _task and not _task.done():
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
    _task = None


def is_active() -> bool:
    return _active
