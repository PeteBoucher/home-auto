import asyncio
import colorsys
import threading
import time
from typing import Any

import tinytuya
from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceType, Integration

_TIMEOUT = 5


def _make_device(device: Device) -> tinytuya.OutletDevice:
    cls = tinytuya.BulbDevice if device.type == DeviceType.bulb else tinytuya.OutletDevice
    d = cls(device.device_id, device.ip_address, device.local_key, connection_timeout=_TIMEOUT)
    d.set_version(device.protocol_version)
    return d


def _dps_get(dps: dict, *keys: Any) -> Any:
    for key in keys:
        for k in (str(key), key):
            if k in dps:
                return dps[k]
    return None


def _hsv_hex_to_rgb_hex(hsv: str) -> str:
    """Convert Tuya DPS 24 hex (HHHH SSSS VVVV) to #rrggbb."""
    h = int(hsv[0:4], 16) / 360
    s = int(hsv[4:8], 16) / 1000
    v = int(hsv[8:12], 16) / 1000
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return f"#{round(r*255):02x}{round(g*255):02x}{round(b*255):02x}"


def _rgb_hex_to_hsv_hex(rgb: str) -> str:
    """Convert #rrggbb to Tuya DPS 24 hex (HHHH SSSS VVVV)."""
    r = int(rgb[1:3], 16) / 255
    g = int(rgb[3:5], 16) / 255
    b = int(rgb[5:7], 16) / 255
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return f"{round(h*360):04x}{round(s*1000):04x}{round(v*1000):04x}"


def _get_state_sync(device: Device) -> dict[str, Any]:
    d = _make_device(device)
    try:
        result = d.status()
        if "Error" in result:
            return {"online": False, "state": False, "brightness": None}
        dps = result.get("dps", {})
        if device.type == DeviceType.bulb:
            raw_bri = _dps_get(dps, 22)
            raw_ct = _dps_get(dps, 23)
            raw_mode = _dps_get(dps, 21) or "white"
            raw_colour = _dps_get(dps, 24)
            return {
                "online": True,
                "state": bool(_dps_get(dps, 20)),
                "brightness": int(raw_bri / 10) if raw_bri is not None else None,
                "color_temp": int(raw_ct / 10) if raw_ct is not None else None,
                "color_mode": raw_mode if raw_mode in ("white", "colour") else "white",
                "color_rgb": _hsv_hex_to_rgb_hex(raw_colour) if raw_colour and len(raw_colour) == 12 else None,
            }
        return {"online": True, "state": bool(_dps_get(dps, 1)), "brightness": None}
    except Exception:
        return {"online": False, "state": False, "brightness": None}


def _command_to_dps(device: Device, command: dict[str, Any]) -> dict[int, Any]:
    """Translate an app command into the DPS values to write, for one packet."""
    if device.type != DeviceType.bulb:
        return {1: bool(command["state"])} if "state" in command else {}
    dps: dict[int, Any] = {}
    if "state" in command:
        dps[20] = bool(command["state"])
    if "brightness" in command:
        dps[22] = max(10, command["brightness"] * 10)
    if "color_mode" in command:
        dps[21] = command["color_mode"]
    if "color_temp" in command:
        dps[21] = "white"
        dps[23] = max(0, min(1000, command["color_temp"] * 10))
    if "color_rgb" in command:
        dps[21] = "colour"
        dps[24] = _rgb_hex_to_hsv_hex(command["color_rgb"])
    return dps


def _send_command_sync(device: Device, command: dict[str, Any]) -> None:
    """Write the whole command as ONE packet over ONE connection.

    Every fresh tinytuya connection does a full protocol-3.5 session-key
    handshake, and the bulb sometimes takes ~1s to accept a new one (measured
    on the Pi 2026-11-13). The old code paid that per DPS — a colour change was
    3 connections — and `BulbDevice.turn_on/turn_off` add a hidden extra
    `status()` round trip first (`detect_bulb()`) before sending. Explicit DPS
    numbers via `set_multiple_values` skip both.
    """
    dps = _command_to_dps(device, command)
    if not dps:
        return
    d = _make_device(device)
    try:
        d.set_multiple_values(dps)
    except Exception:
        pass


def flash_sync(
    device: Device,
    stop: threading.Event,
    colour_hsv: str,
    on_s: float,
    off_s: float,
    duration: float,
) -> None:
    """Flash a bulb using a persistent LAN socket. Runs in a worker thread.

    Keeps the TCP connection open between commands so each toggle is
    milliseconds rather than a full reconnect (~200-400 ms).
    """
    d = _make_device(device)
    d.set_socketPersistent(True)
    on_payload = {20: True, 21: "colour", 24: colour_hsv}
    try:
        d.set_multiple_values(on_payload)  # turn on + set colour mode in one packet
        deadline = time.monotonic() + duration
        while not stop.is_set() and time.monotonic() < deadline:
            t = time.monotonic()
            d.turn_off()
            stop.wait(max(0, off_s - (time.monotonic() - t)))
            if stop.is_set():
                break
            t = time.monotonic()
            d.set_multiple_values(on_payload)  # re-assert colour mode on every flash
            stop.wait(max(0, on_s - (time.monotonic() - t)))
    except Exception:
        pass
    finally:
        d.set_socketPersistent(False)


async def get_state(device: Device) -> dict[str, Any]:
    return await asyncio.to_thread(_get_state_sync, device)


async def live_snapshot(device: Device) -> dict[str, Any]:
    """Return current live state for pre-automation snapshots.

    Falls back to cached DB values if the bulb is offline (switch off),
    so automations restore to the last known state rather than guessing.
    """
    state = await get_state(device)
    if state.get("online"):
        return {
            "state": state["state"],
            "color_mode": state.get("color_mode", device.color_mode),
            "color_rgb": state.get("color_rgb"),
            "brightness": state.get("brightness"),
            "color_temp": state.get("color_temp"),
        }
    return {
        "state": device.state,
        "color_mode": device.color_mode,
        "color_rgb": device.color_rgb,
        "brightness": device.brightness,
        "color_temp": device.color_temp,
    }


async def send_command(device: Device, command: dict[str, Any]) -> None:
    await asyncio.to_thread(_send_command_sync, device, command)


def get_tuya_bulbs() -> list[Device]:
    with Session(engine) as session:
        return list(session.exec(
            select(Device).where(
                Device.integration == Integration.tuya,
                Device.type == DeviceType.bulb,
            )
        ).all())


async def restore_bulb_state(bulb: Device, snap: dict) -> None:
    if not snap["state"]:
        await send_command(bulb, {"state": False})
    elif snap["color_mode"] == "colour" and snap["color_rgb"]:
        await send_command(bulb, {"state": True})
        await send_command(bulb, {"color_rgb": snap["color_rgb"]})
    else:
        await send_command(bulb, {"state": True})
        if snap["brightness"] is not None:
            await send_command(bulb, {"brightness": snap["brightness"]})
        if snap["color_temp"] is not None:
            await send_command(bulb, {"color_temp": snap["color_temp"]})
