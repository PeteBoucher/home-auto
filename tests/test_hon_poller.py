"""Tests for the hOn A/C background polling loop."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.devices.models import Device, DeviceType, Integration
from app.services import hon_poller


@pytest.fixture(name="hon_device")
def hon_device_fixture(session):
    device = Device(
        name="Living Room A/C",
        device_id="ac-unit-1",
        type=DeviceType.ac,
        integration=Integration.hon,
        online=False,
        state=False,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


_ONLINE_STATE = {
    "online": True, "state": True, "temperature": 22, "ac_mode": "cool", "fan_speed": 2,
    "eco": True, "quiet": False, "louvre_position": 5, "indoor_temp": 24.5, "outdoor_temp": 31.0, "ac_energy": 12.5,
}
_OFFLINE_STATE = {"online": False, "state": False, "temperature": 22, "ac_mode": "cool", "fan_speed": 0}


class TestPollHonDevices:
    def test_reachable_device_updates_and_triggers(self, engine, session, hon_device):
        with patch("app.services.hon_poller.engine", engine), \
             patch("app.services.hon_poller.hon_client.get_state", new=AsyncMock(return_value=_ONLINE_STATE)), \
             patch("app.services.hon_poller.check_state_triggers", new=AsyncMock()) as mock_triggers:
            asyncio.run(hon_poller.poll_hon_devices())

        session.refresh(hon_device)
        assert hon_device.online is True
        assert hon_device.state is True
        assert hon_device.temperature == 22
        assert hon_device.ac_mode == "cool"
        assert hon_device.fan_speed == 2
        assert hon_device.eco is True
        assert hon_device.quiet is False
        assert hon_device.louvre_position == 5
        assert hon_device.indoor_temp == 24.5
        assert hon_device.outdoor_temp == 31.0
        assert hon_device.ac_energy == 12.5
        mock_triggers.assert_awaited_once_with(hon_device.id, _ONLINE_STATE)

    def test_unreachable_device_only_marks_offline(self, engine, session, hon_device):
        hon_device.online = True
        hon_device.state = True
        session.add(hon_device)
        session.commit()

        with patch("app.services.hon_poller.engine", engine), \
             patch("app.services.hon_poller.hon_client.get_state", new=AsyncMock(return_value=_OFFLINE_STATE)), \
             patch("app.services.hon_poller.check_state_triggers", new=AsyncMock()) as mock_triggers:
            asyncio.run(hon_poller.poll_hon_devices())

        session.refresh(hon_device)
        assert hon_device.online is False
        assert hon_device.state is True  # untouched, not overwritten with the offline fallback
        mock_triggers.assert_not_awaited()

    def test_no_devices_is_a_noop(self, engine, session):
        with patch("app.services.hon_poller.engine", engine), \
             patch("app.services.hon_poller.hon_client.get_state", new=AsyncMock()) as mock_get:
            asyncio.run(hon_poller.poll_hon_devices())
        mock_get.assert_not_awaited()
