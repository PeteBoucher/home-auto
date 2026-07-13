import asyncio
import json
import logging

import aiomqtt
from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, Integration, PowerSample

log = logging.getLogger(__name__)

HOST = "localhost"
PORT = 1883
PREFIX = "zigbee2mqtt"


def _apply_state(friendly_name: str, payload: dict, online: bool = True) -> tuple[int, dict] | None:
    with Session(engine) as session:
        device = session.exec(
            select(Device).where(
                Device.device_id == friendly_name,
                Device.integration == Integration.zigbee2mqtt,
            )
        ).first()
        if not device:
            return None
        device.online = online
        if "state" in payload:
            device.state = str(payload["state"]).upper() == "ON"
        if "brightness" in payload:
            device.brightness = round(int(payload["brightness"]) / 2.54)
        if "power_on_behavior" in payload:
            device.power_on_behavior = str(payload["power_on_behavior"])
        if "overload_protection" in payload and isinstance(payload["overload_protection"], dict):
            device.overload_protection = json.dumps(payload["overload_protection"])
        if "power" in payload:
            device.power = round(float(payload["power"]), 1)
        if "current" in payload:
            device.current = round(float(payload["current"]), 2)
        if "voltage" in payload:
            device.voltage = round(float(payload["voltage"]), 1)
        if "energy" in payload:
            device.energy = round(float(payload["energy"]), 3)
        session.add(device)
        if any(k in payload for k in ("power", "voltage", "current")):
            session.add(PowerSample(
                device_id=device.id,
                voltage=device.voltage,
                power=device.power,
                current=device.current,
            ))
        session.commit()
        return device.id, {
            "state": device.state, "brightness": device.brightness, "online": device.online,
            "power": device.power, "current": device.current,
            "voltage": device.voltage, "energy": device.energy,
        }


async def _listen(client: aiomqtt.Client) -> None:
    from app.services.automation_engine import check_state_triggers  # deferred to avoid circular import
    async for message in client.messages:
        topic = str(message.topic)
        try:
            payload = json.loads(message.payload)
        except (json.JSONDecodeError, ValueError):
            continue
        parts = topic.split("/")
        if len(parts) < 2 or parts[0] != PREFIX or parts[1] == "bridge":
            continue
        friendly_name = parts[1]
        if len(parts) == 3 and parts[2] == "availability":
            result = _apply_state(friendly_name, {}, online=payload.get("state") == "online")
        elif len(parts) == 2:
            result = _apply_state(friendly_name, payload)
        else:
            continue
        if result:
            await check_state_triggers(*result)


async def run() -> None:
    while True:
        try:
            async with aiomqtt.Client(HOST, PORT) as client:
                await client.subscribe(f"{PREFIX}/#")
                log.warning("MQTT connected")
                await _listen(client)
        except aiomqtt.MqttError as exc:
            log.warning("MQTT error, reconnecting in 5s: %s", exc)
            await asyncio.sleep(5)
        except Exception as exc:
            log.error("MQTT listener crashed, reconnecting in 5s: %s", exc, exc_info=True)
            await asyncio.sleep(5)


async def publish(topic: str, payload: dict) -> None:
    try:
        async with aiomqtt.Client(HOST, PORT) as client:
            await client.publish(topic, json.dumps(payload))
    except aiomqtt.MqttError as e:
        log.warning("MQTT publish failed: %s", e)


async def discover_devices() -> list[dict] | None:
    """Returns None if broker unreachable, [] if no end-devices paired yet, or device list."""
    try:
        async with aiomqtt.Client(HOST, PORT) as client:
            await client.subscribe(f"{PREFIX}/bridge/devices")
            async with asyncio.timeout(3):
                async for message in client.messages:
                    data = json.loads(message.payload)
                    return [d for d in data if d.get("type") in ("EndDevice", "Router")]
            return []
    except aiomqtt.MqttError:
        return None
    except (TimeoutError, json.JSONDecodeError):
        return []
