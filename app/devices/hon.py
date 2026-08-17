import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_MODE_TO_INT = {"auto": 0, "cool": 1, "dry": 2, "fan": 3, "heat": 4}
_INT_TO_MODE = {v: k for k, v in _MODE_TO_INT.items()}

_hon_ctx = None
_hon = None


async def start() -> None:
    global _hon_ctx, _hon
    email = os.getenv("HON_EMAIL", "")
    password = os.getenv("HON_PASSWORD", "")
    if not email or not password:
        log.info("HON_EMAIL/HON_PASSWORD not set — hOn integration disabled")
        return
    try:
        from pyhon import Hon
        _hon_ctx = Hon(email=email, password=password)
        _hon = await _hon_ctx.__aenter__()
        log.info("hOn connected — %d appliance(s) found", len(_hon.appliances))
    except Exception as e:
        log.warning("hOn connection failed: %s", e)


async def stop() -> None:
    global _hon_ctx, _hon
    if _hon_ctx:
        try:
            await _hon_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        _hon_ctx = None
        _hon = None


def _appliance(device_id: str):
    if not _hon:
        return None
    for a in _hon.appliances:
        uid = getattr(a, "unique_id", None) or getattr(a, "mac_address", None)
        if uid == device_id:
            return a
    return None


def _pval(params: dict, key: str, default=None) -> Any:
    if key not in params:
        return default
    v = params[key]
    return v.value if hasattr(v, "value") else v


_NO_DATA_SENTINEL = -64.0  # Haier's own "no reading" placeholder for an unwired
# channel — permanently seen on tempDefrostOutdoor/tempInAirOutdoor on this unit,
# and briefly seen on tempOutdoor during a real telemetry glitch (2026-08-17,
# self-corrected within 60s). Treat it as "no reading" rather than a real value.


def _valid_temp(raw: Any) -> float | None:
    if raw is None:
        return None
    value = float(raw)
    return None if value == _NO_DATA_SENTINEL else value


async def get_appliances() -> list:
    return _hon.appliances if _hon else []


async def get_state(device_id: str) -> dict[str, Any]:
    a = _appliance(device_id)
    if not a:
        return {"online": False, "state": False, "temperature": 22, "ac_mode": "cool", "fan_speed": 0}
    try:
        # Sending a command optimistically overwrites this same local attribute
        # cache with the intended values *before* the network call even happens
        # (pyhOn's HonCommand.send_parameters -> sync_command_to_params), so
        # reading it without forcing a real refresh reports "what we asked for,"
        # not "what's actually true" — a command that silently failed still
        # looks like it succeeded. force=True always re-fetches from the cloud.
        await a.update(force=True)
        params = a.attributes.get("parameters", {})
        return {
            "online": True,
            "state": bool(int(_pval(params, "onOffStatus", 0))),
            "temperature": int(_pval(params, "tempSel", 22)),
            "ac_mode": _INT_TO_MODE.get(int(_pval(params, "machMode", 1)), "cool"),
            "fan_speed": int(_pval(params, "windSpeed", 0)),
            "quiet": bool(int(_pval(params, "muteStatus", 0))),
            "louvre_position": int(_pval(params, "windDirectionVertical", 0)),
            "indoor_temp": _valid_temp(_pval(params, "tempIndoor")),
            "outdoor_temp": _valid_temp(_pval(params, "tempOutdoor")),
        }
    except Exception as e:
        log.warning("hOn get_state error: %s", e)
        return {"online": False, "state": False, "temperature": 22, "ac_mode": "cool", "fan_speed": 0}


async def send_command(device_id: str, command: dict) -> None:
    a = _appliance(device_id)
    if not a:
        log.warning("hOn appliance not found: %s", device_id)
        return
    try:
        # onOffStatus is locked to 1 on startProgram and to 0 on stopProgram — turning
        # the unit off isn't a parameter you set, it's a different command you call.
        cmd_name = "stopProgram" if command.get("state") is False else "startProgram"
        cmd = a.commands.get(cmd_name)
        if not cmd:
            return
        if "temperature" in command:
            cmd.parameters["tempSel"].value = command["temperature"]
        if "ac_mode" in command:
            # machMode/windSpeed/windDirectionVertical are HonParameterEnum, which
            # validates against a list of *strings* — an int value raises ValueError
            # even when it's numerically an allowed option.
            cmd.parameters["machMode"].value = str(_MODE_TO_INT.get(command["ac_mode"], 1))
        if "fan_speed" in command:
            cmd.parameters["windSpeed"].value = str(command["fan_speed"])
        if "quiet" in command:
            cmd.parameters["muteStatus"].value = 1 if command["quiet"] else 0
        if "louvre_position" in command:
            cmd.parameters["windDirectionVertical"].value = str(command["louvre_position"])
        await cmd.send()
    except Exception as e:
        log.warning("hOn send_command error: %s", e)
