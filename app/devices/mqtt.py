import asyncio
import json
import logging
from pathlib import Path

import aiomqtt
from sqlmodel import Session, select

from app.db import engine
from app.devices.models import ClimateSample, Device, DeviceType, Integration, PowerSample
from app.devices.zigbee_color import hs_to_rgb_hex, mireds_to_pct

log = logging.getLogger(__name__)

HOST = "localhost"
PORT = 1883
PREFIX = "zigbee2mqtt"

Z2M_STATE_FILE = Path("/opt/zigbee2mqtt/data/state.json")


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
        # Sleepy sensors go "offline" between readings per the availability heartbeat,
        # but are functioning — only let a real reading (online=True) update their status.
        if online or device.type != DeviceType.sensor:
            device.online = online
        if "state" in payload:
            device.state = str(payload["state"]).upper() == "ON"
        if "brightness" in payload:
            device.brightness = round(int(payload["brightness"]) / 2.54)
        if "color_temp" in payload:
            device.color_temp = mireds_to_pct(float(payload["color_temp"]))
        if "color" in payload and isinstance(payload["color"], dict) and "hue" in payload["color"]:
            device.color_rgb = hs_to_rgb_hex(
                payload["color"]["hue"], payload["color"].get("saturation", 100),
                round((device.brightness or 100) * 2.54),
            )
        if "color_mode" in payload and payload["color_mode"] in ("color_temp", "xy", "hs"):
            device.color_mode = "white" if payload["color_mode"] == "color_temp" else "colour"
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
        if "temperature" in payload:
            device.sensor_temperature = round(float(payload["temperature"]), 1)
        if "humidity" in payload:
            device.humidity = round(float(payload["humidity"]), 1)
        if "battery" in payload:
            device.battery = int(payload["battery"])
        session.add(device)
        if any(k in payload for k in ("power", "voltage", "current")):
            session.add(PowerSample(
                device_id=device.id,
                voltage=device.voltage,
                power=device.power,
                current=device.current,
            ))
        if any(k in payload for k in ("temperature", "humidity")):
            session.add(ClimateSample(
                device_id=device.id,
                temperature=device.sensor_temperature,
                humidity=device.humidity,
            ))
        session.commit()
        return device.id, {
            "state": device.state, "brightness": device.brightness, "online": device.online,
            "power": device.power, "current": device.current,
            "voltage": device.voltage, "energy": device.energy,
            "sensor_temperature": device.sensor_temperature,
            "humidity": device.humidity,
            "battery": device.battery,
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


def _seed_from_z2m_cache() -> None:
    """Populate sensor readings from Z2M's state.json on startup.

    Z2M only republishes cached state when it restarts, not when our app
    reconnects. For sleepy sensors (SNZB-02 etc.) that may not report for
    up to an hour, this ensures the dashboard shows the last known values
    immediately after an app restart.
    """
    if not Z2M_STATE_FILE.exists():
        return
    try:
        cache = json.loads(Z2M_STATE_FILE.read_text())
    except Exception:
        return
    with Session(engine) as session:
        devices = session.exec(
            select(Device).where(
                Device.integration == Integration.zigbee2mqtt,
                Device.type == DeviceType.sensor,
            )
        ).all()
        for device in devices:
            state = cache.get(device.device_id)
            if not state:
                continue
            changed = False
            if "temperature" in state and device.sensor_temperature is None:
                device.sensor_temperature = round(float(state["temperature"]), 1)
                changed = True
            if "humidity" in state and device.humidity is None:
                device.humidity = round(float(state["humidity"]), 1)
                changed = True
            if "battery" in state and device.battery is None:
                device.battery = int(state["battery"])
                changed = True
            if changed:
                device.online = True
                session.add(device)
                log.info("Seeded %s from Z2M cache: %s", device.name, state)
        session.commit()


async def run() -> None:
    _seed_from_z2m_cache()
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


def get_zigbee_bulbs() -> list[Device]:
    with Session(engine) as session:
        return list(session.exec(
            select(Device).where(
                Device.integration == Integration.zigbee2mqtt,
                Device.type == DeviceType.bulb,
                Device.dimmable == True,  # noqa: E712 — SQLModel comparison, not a Python bool check
            )
        ).all())


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
