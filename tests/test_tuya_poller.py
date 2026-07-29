"""Tests for the Tuya background polling loop."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.devices.models import Device, DeviceType, Integration
from app.services import tuya_poller


@pytest.fixture(name="tuya_device")
def tuya_device_fixture(session):
    device = Device(
        name="Test Bulb",
        device_id="dev_bulb_001",
        local_key="secretkey",
        ip_address="192.168.x.x",
        type=DeviceType.bulb,
        integration=Integration.tuya,
        protocol_version=3.5,
        online=True,
        state=True,
        brightness=80,
        color_temp=50,
        color_mode="white",
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


_ONLINE_STATE = {
    "online": True, "state": False, "brightness": 40,
    "color_temp": 30, "color_mode": "white", "color_rgb": None,
}
_UNREACHABLE_STATE = {"online": False, "state": False, "brightness": None}


class TestPollTuyaDevices:
    def test_reachable_device_updates_and_triggers(self, engine, session, tuya_device):
        with patch("app.services.tuya_poller.engine", engine), \
             patch("app.services.tuya_poller.tuya_client.get_state", new=AsyncMock(return_value=_ONLINE_STATE)), \
             patch("app.services.tuya_poller.check_state_triggers", new=AsyncMock()) as mock_triggers, \
             patch("app.services.tuya_poller.propagate_member_change", new=AsyncMock()) as mock_propagate:
            asyncio.run(tuya_poller.poll_tuya_devices())

        session.refresh(tuya_device)
        assert tuya_device.state is False
        assert tuya_device.brightness == 40
        assert tuya_device.online is True
        mock_triggers.assert_awaited_once_with(tuya_device.id, _ONLINE_STATE)
        mock_propagate.assert_awaited_once_with(tuya_device.id)

    def test_unreachable_device_only_marks_offline(self, engine, session, tuya_device):
        with patch("app.services.tuya_poller.engine", engine), \
             patch("app.services.tuya_poller.tuya_client.get_state", new=AsyncMock(return_value=_UNREACHABLE_STATE)), \
             patch("app.services.tuya_poller.check_state_triggers", new=AsyncMock()) as mock_triggers, \
             patch("app.services.tuya_poller.propagate_member_change", new=AsyncMock()) as mock_propagate:
            asyncio.run(tuya_poller.poll_tuya_devices())

        session.refresh(tuya_device)
        # A transient LAN timeout must not overwrite last-known state/brightness —
        # only the online flag should change.
        assert tuya_device.online is False
        assert tuya_device.state is True
        assert tuya_device.brightness == 80
        assert tuya_device.color_temp == 50
        mock_triggers.assert_not_awaited()
        mock_propagate.assert_not_awaited()

    def test_no_devices_is_a_noop(self, engine, session):
        with patch("app.services.tuya_poller.engine", engine), \
             patch("app.services.tuya_poller.tuya_client.get_state", new=AsyncMock()) as mock_get, \
             patch("app.services.tuya_poller.check_state_triggers", new=AsyncMock()) as mock_triggers:
            asyncio.run(tuya_poller.poll_tuya_devices())
        mock_get.assert_not_awaited()
        mock_triggers.assert_not_awaited()

    def test_exception_from_get_state_is_skipped(self, engine, session, tuya_device):
        with patch("app.services.tuya_poller.engine", engine), \
             patch("app.services.tuya_poller.tuya_client.get_state", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("app.services.tuya_poller.check_state_triggers", new=AsyncMock()) as mock_triggers, \
             patch("app.services.tuya_poller.propagate_member_change", new=AsyncMock()) as mock_propagate:
            asyncio.run(tuya_poller.poll_tuya_devices())

        session.refresh(tuya_device)
        assert tuya_device.online is True  # untouched — get_state raised, not just returned offline
        assert tuya_device.state is True
        mock_triggers.assert_not_awaited()
        mock_propagate.assert_not_awaited()
