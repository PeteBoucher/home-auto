"""Tests for red_alert's Zigbee bulb flash/restore support."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import app.services.red_alert as red_alert
from app.devices.models import Device, DeviceType, Integration


@pytest.fixture
def zigbee_bulb(session):
    d = Device(
        name="Dining Room Uplighter",
        device_id="dining_room_uplighter",
        type=DeviceType.bulb,
        integration=Integration.zigbee2mqtt,
        online=True,
        state=True,
        dimmable=True,
        brightness=80,
        color_temp=40,
        color_mode="white",
    )
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


class TestFlashZigbee:
    def setup_method(self):
        red_alert._stop.clear()

    def test_publishes_on_then_off(self, zigbee_bulb):
        with (
            patch("app.services.red_alert._FLASH_ON", 0.01),
            patch("app.services.red_alert._FLASH_OFF", 0.01),
            patch("app.services.red_alert.mqtt_client.publish", new=AsyncMock()) as mock_pub,
        ):
            asyncio.run(red_alert._flash_zigbee(zigbee_bulb, 0, 100, duration=0.015))
        topics = [c.args[0] for c in mock_pub.call_args_list]
        payloads = [c.args[1] for c in mock_pub.call_args_list]
        assert topics[0] == "zigbee2mqtt/dining_room_uplighter/set"
        assert payloads[0]["state"] == "ON"
        assert payloads[0]["color"] == {"hue": 0, "saturation": 100}

    def test_stop_event_halts_flashing(self, zigbee_bulb):
        red_alert._stop.set()
        with patch("app.services.red_alert.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(red_alert._flash_zigbee(zigbee_bulb, 0, 100, duration=1))
        mock_pub.assert_not_awaited()


class TestRestoreZigbee:
    def setup_method(self):
        red_alert._saved_zigbee.clear()

    def test_restores_white_mode(self, engine, session, zigbee_bulb):
        red_alert._saved_zigbee[zigbee_bulb.id] = {
            "state": True, "color_mode": "white", "color_rgb": None,
            "brightness": 80, "color_temp": 40,
        }
        with (
            patch("app.services.red_alert.engine", engine),
            patch("app.services.red_alert.mqtt_client.publish", new=AsyncMock()) as mock_pub,
        ):
            asyncio.run(red_alert._restore_zigbee([zigbee_bulb]))
        payload = mock_pub.call_args.args[1]
        assert payload["state"] == "ON"
        assert "color_temp" in payload
        assert "color" not in payload
        session.refresh(zigbee_bulb)
        assert zigbee_bulb.color_mode == "white"
        assert zigbee_bulb.brightness == 80

    def test_restores_colour_mode(self, engine, session, zigbee_bulb):
        red_alert._saved_zigbee[zigbee_bulb.id] = {
            "state": True, "color_mode": "colour", "color_rgb": "#00ff00",
            "brightness": 100, "color_temp": None,
        }
        with (
            patch("app.services.red_alert.engine", engine),
            patch("app.services.red_alert.mqtt_client.publish", new=AsyncMock()) as mock_pub,
        ):
            asyncio.run(red_alert._restore_zigbee([zigbee_bulb]))
        payload = mock_pub.call_args.args[1]
        assert payload["color"]["hue"] == 120
        session.refresh(zigbee_bulb)
        assert zigbee_bulb.color_rgb == "#00ff00"

    def test_restores_off_state(self, engine, session, zigbee_bulb):
        red_alert._saved_zigbee[zigbee_bulb.id] = {
            "state": False, "color_mode": "white", "color_rgb": None,
            "brightness": 80, "color_temp": 40,
        }
        with (
            patch("app.services.red_alert.engine", engine),
            patch("app.services.red_alert.mqtt_client.publish", new=AsyncMock()) as mock_pub,
        ):
            asyncio.run(red_alert._restore_zigbee([zigbee_bulb]))
        payload = mock_pub.call_args.args[1]
        assert payload == {"state": "OFF", "transition": 0}

    def test_no_snapshot_is_noop(self, engine, session, zigbee_bulb):
        with (
            patch("app.services.red_alert.engine", engine),
            patch("app.services.red_alert.mqtt_client.publish", new=AsyncMock()) as mock_pub,
        ):
            asyncio.run(red_alert._restore_zigbee([zigbee_bulb]))
        mock_pub.assert_not_awaited()


class TestActivateIncludesZigbeeBulbs:
    def setup_method(self):
        red_alert._active = False
        red_alert._saved.clear()
        red_alert._saved_zigbee.clear()

    def test_activate_snapshots_zigbee_bulb(self, engine, session, zigbee_bulb):
        async def scenario():
            await red_alert.activate()
            assert zigbee_bulb.id in red_alert._saved_zigbee
            await red_alert.deactivate()

        with (
            patch("app.services.red_alert.engine", engine),
            patch("app.devices.mqtt.engine", engine),
            patch("app.devices.tuya.engine", engine),
            patch("app.services.red_alert._DURATION", 0.02),
            patch("app.services.red_alert._FLASH_ON", 0.01),
            patch("app.services.red_alert._FLASH_OFF", 0.01),
            patch("app.services.red_alert.mqtt_client.publish", new=AsyncMock()),
        ):
            asyncio.run(scenario())
        assert red_alert.is_active() is False
