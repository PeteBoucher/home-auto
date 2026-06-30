import asyncio
import json
import logging

import aiomqtt
from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, Integration

log = logging.getLogger(__name__)

HOST = "localhost"
PORT = 1883
PREFIX = "zigbee2mqtt"


def _apply_state(friendly_name: str, payload: dict, online: bool = True) -> None:
    with Session(engine) as session:
        device = session.exec(
            select(Device).where(
                Device.device_id == friendly_name,
                Device.integration == Integration.zigbee2mqtt,
            )
        ).first()
        if not device:
            return
        device.online = online
        if "state" in payload:
            device.state = str(payload["state"]).upper() == "ON"
        if "brightness" in payload:
            device.brightness = round(int(payload["brightness"]) / 2.54)
        session.add(device)
        session.commit()


async def _listen(client: aiomqtt.Client) -> None:
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
            _apply_state(friendly_name, {}, online=payload.get("state") == "online")
        elif len(parts) == 2:
            _apply_state(friendly_name, payload)


async def run() -> None:
    while True:
        try:
            async with aiomqtt.Client(HOST, PORT) as client:
                await client.subscribe(f"{PREFIX}/#")
                log.info("MQTT connected")
                await _listen(client)
        except aiomqtt.MqttError:
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
