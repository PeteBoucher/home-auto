import asyncio
from typing import Any

import tinytuya

from app.devices.models import Device, DeviceType

_TIMEOUT = 5


def _make_device(device: Device) -> tinytuya.OutletDevice:
    cls = tinytuya.BulbDevice if device.type == DeviceType.bulb else tinytuya.OutletDevice
    d = cls(device.device_id, device.ip_address, device.local_key, connection_timeout=_TIMEOUT)
    d.set_version(3.3)
    return d


def _dps_get(dps: dict, *keys: Any) -> Any:
    """Look up a DPS value by int or string key."""
    for key in keys:
        for k in (str(key), key):
            if k in dps:
                return dps[k]
    return None


def _get_state_sync(device: Device) -> dict[str, Any]:
    d = _make_device(device)
    try:
        result = d.status()
        if "Error" in result:
            return {"online": False, "state": False, "brightness": None}
        dps = result.get("dps", {})
        if device.type == DeviceType.bulb:
            raw = _dps_get(dps, 22)
            return {
                "online": True,
                "state": bool(_dps_get(dps, 20)),
                "brightness": int(raw / 10) if raw is not None else None,
            }
        return {"online": True, "state": bool(_dps_get(dps, 1)), "brightness": None}
    except Exception:
        return {"online": False, "state": False, "brightness": None}


def _send_command_sync(device: Device, command: dict[str, Any]) -> None:
    d = _make_device(device)
    try:
        if "state" in command:
            d.turn_on() if command["state"] else d.turn_off()
        if "brightness" in command and device.type == DeviceType.bulb:
            d.set_brightness_percentage(command["brightness"])
    except Exception:
        pass


async def get_state(device: Device) -> dict[str, Any]:
    return await asyncio.to_thread(_get_state_sync, device)


async def send_command(device: Device, command: dict[str, Any]) -> None:
    await asyncio.to_thread(_send_command_sync, device, command)
