import asyncio
import logging
import threading
import time

from sqlmodel import Session

from app.db import engine
from app.devices.models import Device
from app.devices import tuya as tuya_client
from app.devices import mqtt as mqtt_client
from app.devices.tuya import _rgb_hex_to_hsv_hex, get_tuya_bulbs, restore_bulb_state
from app.devices.mqtt import get_zigbee_bulbs
from app.devices.zigbee_color import pct_to_mireds, rgb_hex_to_hs_brightness

log = logging.getLogger(__name__)

_RED = "#ff0000"
_FLASH_ON = 1.5
_FLASH_OFF = 1.5
_DURATION = 60

_active = False
_task: asyncio.Task | None = None
_stop = threading.Event()
_saved: dict[int, dict] = {}
_saved_zigbee: dict[int, dict] = {}


async def _restore(bulbs: list[Device]) -> None:
    for bulb in bulbs:
        snap = _saved.pop(bulb.id, None)
        if snap is None:
            continue
        await restore_bulb_state(bulb, snap)
        # Write snapshot back to DB so the dashboard card is correct immediately
        with Session(engine) as session:
            db_bulb = session.get(Device, bulb.id)
            if db_bulb:
                db_bulb.state = snap["state"]
                db_bulb.color_mode = snap["color_mode"] or "white"
                db_bulb.color_rgb = snap["color_rgb"]
                db_bulb.brightness = snap["brightness"]
                db_bulb.color_temp = snap["color_temp"]
                session.add(db_bulb)
                session.commit()


async def _flash_zigbee(bulb: Device, hue: int, saturation: int, duration: float) -> None:
    deadline = time.monotonic() + duration
    # transition: 0 forces an instant snap rather than the bulb's default fade,
    # so rapid flashing doesn't get smeared into a lingering blend of the two states.
    on_payload = {"state": "ON", "color": {"hue": hue, "saturation": saturation}, "brightness": 254, "transition": 0}
    off_payload = {"state": "OFF", "transition": 0}
    topic = f"{mqtt_client.PREFIX}/{bulb.device_id}/set"
    while not _stop.is_set() and time.monotonic() < deadline:
        await mqtt_client.publish(topic, on_payload)
        await asyncio.sleep(_FLASH_ON)
        if _stop.is_set() or time.monotonic() >= deadline:
            break
        await mqtt_client.publish(topic, off_payload)
        await asyncio.sleep(_FLASH_OFF)


async def _restore_zigbee(bulbs: list[Device]) -> None:
    for bulb in bulbs:
        snap = _saved_zigbee.pop(bulb.id, None)
        if snap is None:
            continue
        payload: dict = {"state": "ON" if snap["state"] else "OFF", "transition": 0}
        if snap["state"]:
            if snap["color_mode"] == "colour" and snap["color_rgb"]:
                hue, saturation, brightness = rgb_hex_to_hs_brightness(snap["color_rgb"])
                payload["color"] = {"hue": hue, "saturation": saturation}
                payload["brightness"] = brightness
            else:
                if snap["color_temp"] is not None:
                    payload["color_temp"] = pct_to_mireds(snap["color_temp"])
                if snap["brightness"] is not None:
                    payload["brightness"] = round(snap["brightness"] * 2.54)
        await mqtt_client.publish(f"{mqtt_client.PREFIX}/{bulb.device_id}/set", payload)
        # Write snapshot back to DB so the dashboard card is correct immediately
        with Session(engine) as session:
            db_bulb = session.get(Device, bulb.id)
            if db_bulb:
                db_bulb.state = snap["state"]
                db_bulb.color_mode = snap["color_mode"] or "white"
                db_bulb.color_rgb = snap["color_rgb"]
                db_bulb.brightness = snap["brightness"]
                db_bulb.color_temp = snap["color_temp"]
                session.add(db_bulb)
                session.commit()


async def _run(tuya_bulbs: list[Device], zigbee_bulbs: list[Device]) -> None:
    global _active
    red_hsv = _rgb_hex_to_hsv_hex(_RED)
    hue, saturation, _brightness = rgb_hex_to_hs_brightness(_RED)
    try:
        await asyncio.gather(
            # Each Tuya bulb gets its own thread with a persistent socket.
            *[
                asyncio.to_thread(
                    tuya_client.flash_sync, b, _stop, red_hsv, _FLASH_ON, _FLASH_OFF, _DURATION
                )
                for b in tuya_bulbs
            ],
            *[_flash_zigbee(b, hue, saturation, _DURATION) for b in zigbee_bulbs],
        )
    finally:
        _active = False
        await _restore(tuya_bulbs)
        await _restore_zigbee(zigbee_bulbs)


async def activate() -> None:
    global _active, _task, _stop
    if _active:
        return
    tuya_bulbs = get_tuya_bulbs()
    zigbee_bulbs = get_zigbee_bulbs()
    for bulb in tuya_bulbs:
        _saved[bulb.id] = await tuya_client.live_snapshot(bulb)
    for bulb in zigbee_bulbs:
        _saved_zigbee[bulb.id] = {
            "state": bulb.state,
            "color_mode": bulb.color_mode,
            "color_rgb": bulb.color_rgb,
            "brightness": bulb.brightness,
            "color_temp": bulb.color_temp,
        }
    _active = True
    _stop = threading.Event()
    _task = asyncio.create_task(_run(tuya_bulbs, zigbee_bulbs))


async def deactivate() -> None:
    global _task
    _stop.set()
    if _task and not _task.done():
        await _task
    _task = None


def is_active() -> bool:
    return _active
