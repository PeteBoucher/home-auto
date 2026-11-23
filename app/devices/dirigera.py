"""IKEA DIRIGERA hub — local REST API (no cloud round-trip).

Auth is a long-lived bearer token minted once by pressing the hub's action
button during an OAuth/PKCE handshake (see scripts/dirigera_token.py). The hub
serves a self-signed certificate, so TLS verification is off — it's a LAN-only
call to a fixed address, same trust model as tinytuya's local protocol.

Only environment sensors (e.g. the ALPSTUGA air quality monitor, a Matter
device the hub bridges) are read for now; the hub never pushes readings to us,
so services/dirigera_poller.py polls this.
"""
import os

import httpx

_TIMEOUT = 10.0  # generous — see feedback on Pi timeouts; a slow poll beats a dropped one


def is_configured() -> bool:
    return bool(os.getenv("DIRIGERA_HOST") and os.getenv("DIRIGERA_TOKEN"))


def _round(value, digits: int):
    return round(float(value), digits) if value is not None else None


def parse_environment_sensor(raw: dict) -> dict:
    """Flatten one hub device record into the fields we store on Device."""
    attrs = raw.get("attributes", {})
    co2 = attrs.get("currentCO2")
    pm25 = attrs.get("currentPM25")
    return {
        "hub_id": raw["id"],
        "name": attrs.get("customName") or attrs.get("model") or "Air quality monitor",
        "room": (raw.get("room") or {}).get("name"),
        "online": bool(raw.get("isReachable")),
        "last_seen": raw.get("lastSeen"),
        "sensor_temperature": _round(attrs.get("currentTemperature"), 1),
        "humidity": _round(attrs.get("currentRH"), 1),
        "co2": int(co2) if co2 is not None else None,
        "pm25": int(pm25) if pm25 is not None else None,
        "window_open": attrs.get("windowOpen"),
    }


async def get_environment_sensors() -> list[dict]:
    host = os.environ["DIRIGERA_HOST"]
    token = os.environ["DIRIGERA_TOKEN"]
    async with httpx.AsyncClient(verify=False, timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"https://{host}:8443/v1/devices",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
    return [
        parse_environment_sensor(d)
        for d in resp.json()
        if d.get("deviceType") == "environmentSensor"
    ]
