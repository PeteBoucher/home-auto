"""API endpoint tests using an in-memory SQLite database."""
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.devices.models import Device


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
        with patch("app.main.tuya_client.get_state", new=AsyncMock(return_value=_TUYA_STATE_ON)):
            resp = client.get("/")
        assert resp.status_code == 200
        assert "Test Bulb" in resp.text
        assert "Living Room Socket" in resp.text


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


class TestDeviceManagement:
    def test_delete_device(self, client, tuya_bulb, session):
        resp = client.post(f"/devices/{tuya_bulb.id}/delete")
        assert resp.status_code == 200
        assert session.exec(select(Device)).first() is None

    def test_delete_unknown_device(self, client):
        resp = client.post("/devices/9999/delete")
        assert resp.status_code == 404
