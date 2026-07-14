"""API endpoint tests using an in-memory SQLite database."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import select

from app.devices.models import Device, Schedule


_TUYA_STATE_ON = {
    "online": True, "state": True, "brightness": 50,
    "color_temp": 50, "color_mode": "white", "color_rgb": None,
}
_TUYA_STATE_OFF = {
    "online": True, "state": False, "brightness": 50,
    "color_temp": 50, "color_mode": "white", "color_rgb": None,
}


class TestDashboard:
    def test_empty_dashboard(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_shows_device_names(self, client, tuya_bulb, z2m_plug):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Test Bulb" in resp.text
        assert "Living Room Socket" in resp.text

    def test_sensor_card_shows_readings(self, client, z2m_sensor):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Bedroom Sensor" in resp.text
        assert "21.5°" in resp.text
        assert "55.2%" in resp.text


class TestTuyaCommands:
    def test_toggle_on(self, client, tuya_bulb):
        with (
            patch("app.api.devices.tuya_client.send_command", new=AsyncMock()),
            patch("app.api.devices.tuya_client.get_state", new=AsyncMock(return_value=_TUYA_STATE_ON)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"state": "true"})
        assert resp.status_code == 200
        assert "On" in resp.text

    def test_toggle_off(self, client, tuya_bulb):
        with (
            patch("app.api.devices.tuya_client.send_command", new=AsyncMock()),
            patch("app.api.devices.tuya_client.get_state", new=AsyncMock(return_value=_TUYA_STATE_OFF)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"state": "false"})
        assert resp.status_code == 200
        assert "Off" in resp.text

    def test_brightness(self, client, tuya_bulb):
        state = {**_TUYA_STATE_ON, "brightness": 30}
        with (
            patch("app.api.devices.tuya_client.send_command", new=AsyncMock()),
            patch("app.api.devices.tuya_client.get_state", new=AsyncMock(return_value=state)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"brightness": "30"})
        assert resp.status_code == 200
        assert 'name="brightness"' in resp.text
        assert 'value="30"' in resp.text

    def test_color_temp(self, client, tuya_bulb):
        state = {**_TUYA_STATE_ON, "color_temp": 25}
        with (
            patch("app.api.devices.tuya_client.send_command", new=AsyncMock()),
            patch("app.api.devices.tuya_client.get_state", new=AsyncMock(return_value=state)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"color_temp": "25"})
        assert resp.status_code == 200
        assert 'name="color_temp"' in resp.text

    def test_switch_to_colour_mode(self, client, tuya_bulb):
        state = {**_TUYA_STATE_ON, "color_mode": "colour", "color_rgb": "#ff0000"}
        with (
            patch("app.api.devices.tuya_client.send_command", new=AsyncMock()),
            patch("app.api.devices.tuya_client.get_state", new=AsyncMock(return_value=state)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"color_mode": "colour"})
        assert resp.status_code == 200
        # colour mode: picker visible, white-mode controls hidden
        assert 'name="color_rgb"' in resp.text
        assert 'name="color_temp"' not in resp.text
        assert 'name="brightness"' not in resp.text

    def test_colour_picker(self, client, tuya_bulb):
        state = {**_TUYA_STATE_ON, "color_mode": "colour", "color_rgb": "#ff0000"}
        with (
            patch("app.api.devices.tuya_client.send_command", new=AsyncMock()),
            patch("app.api.devices.tuya_client.get_state", new=AsyncMock(return_value=state)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"color_rgb": "#ff0000"})
        assert resp.status_code == 200
        assert 'value="#ff0000"' in resp.text

    def test_unknown_device_returns_404(self, client):
        resp = client.post("/devices/9999/command", data={"state": "true"})
        assert resp.status_code == 404


class TestZ2MCommands:
    def test_toggle_on(self, client, z2m_plug):
        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            resp = client.post(f"/devices/{z2m_plug.id}/command", data={"state": "true"})
        assert resp.status_code == 200
        assert "On" in resp.text
        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/living_room_socket/set", {"state": "ON"}
        )

    def test_toggle_off(self, client, z2m_plug):
        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            resp = client.post(f"/devices/{z2m_plug.id}/command", data={"state": "false"})
        assert resp.status_code == 200
        assert "Off" in resp.text
        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/living_room_socket/set", {"state": "OFF"}
        )

    def test_optimistic_online_after_command(self, client, z2m_plug, session):
        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()):
            client.post(f"/devices/{z2m_plug.id}/command", data={"state": "true"})
        session.refresh(z2m_plug)
        assert z2m_plug.online is True
        assert z2m_plug.state is True


class TestFireTVKeys:
    def test_sends_key_action(self, client, firetv_device):
        with patch("app.api.devices.firetv_client.ENABLED", True), \
             patch("app.api.devices.firetv_client.send_key", new=AsyncMock(return_value=True)) as mock_key:
            resp = client.post(f"/devices/{firetv_device.id}/key", data={"action": "play_pause"})
        assert resp.status_code == 200
        mock_key.assert_awaited_once_with("play_pause")

    def test_offline_device_returns_card_without_raising(self, client, firetv_device):
        with patch("app.api.devices.firetv_client.ENABLED", True), \
             patch("app.api.devices.firetv_client.send_key", new=AsyncMock(return_value=False)):
            resp = client.post(f"/devices/{firetv_device.id}/key", data={"action": "volume_up"})
        assert resp.status_code == 200

    def test_non_firetv_device_returns_404(self, client, z2m_plug):
        resp = client.post(f"/devices/{z2m_plug.id}/key", data={"action": "home"})
        assert resp.status_code == 404

    def test_unknown_device_returns_404(self, client):
        resp = client.post("/devices/9999/key", data={"action": "home"})
        assert resp.status_code == 404


class TestSchedule:
    def _mock_apply(self):
        return patch("app.api.devices.apply_schedule", new=MagicMock())

    def test_off_time_only(self, client, z2m_plug, session):
        with self._mock_apply():
            resp = client.post(f"/devices/{z2m_plug.id}/schedule", data={"off_time": "23:00"})
        assert resp.status_code == 200
        sched = session.exec(select(Schedule).where(Schedule.device_id == z2m_plug.id)).first()
        assert sched is not None
        assert sched.off_time == "23:00"
        assert sched.on_time == ""

    def test_on_time_only(self, client, z2m_plug, session):
        with self._mock_apply():
            resp = client.post(f"/devices/{z2m_plug.id}/schedule", data={"on_time": "07:30"})
        assert resp.status_code == 200
        sched = session.exec(select(Schedule).where(Schedule.device_id == z2m_plug.id)).first()
        assert sched is not None
        assert sched.on_time == "07:30"
        assert sched.off_time == ""

    def test_both_times(self, client, z2m_plug, session):
        with self._mock_apply():
            resp = client.post(f"/devices/{z2m_plug.id}/schedule", data={"on_time": "08:00", "off_time": "22:00"})
        assert resp.status_code == 200
        sched = session.exec(select(Schedule).where(Schedule.device_id == z2m_plug.id)).first()
        assert sched.on_time == "08:00"
        assert sched.off_time == "22:00"

    def test_neither_time_returns_error(self, client, z2m_plug, session):
        with self._mock_apply():
            resp = client.post(f"/devices/{z2m_plug.id}/schedule", data={})
        assert resp.status_code == 200
        assert "Set at least one time" in resp.text
        assert session.exec(select(Schedule).where(Schedule.device_id == z2m_plug.id)).first() is None


class TestDeviceManagement:
    def test_delete_device(self, client, tuya_bulb, session):
        resp = client.post(f"/devices/{tuya_bulb.id}/delete")
        assert resp.status_code == 200
        assert session.exec(select(Device)).first() is None

    def test_delete_unknown_device(self, client):
        resp = client.post("/devices/9999/delete")
        assert resp.status_code == 404
