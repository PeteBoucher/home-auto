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

    def test_color_temp_sent_as_mireds(self, client, z2m_bulb, session):
        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            resp = client.post(f"/devices/{z2m_bulb.id}/command", data={"color_temp": "0"})
        assert resp.status_code == 200
        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/dining_room_uplighter/set", {"color_temp": 556}
        )
        session.refresh(z2m_bulb)
        assert z2m_bulb.color_temp == 0
        assert z2m_bulb.color_mode == "white"

    def test_color_rgb_sent_as_hue_saturation(self, client, z2m_bulb, session):
        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            resp = client.post(f"/devices/{z2m_bulb.id}/command", data={"color_rgb": "#ff0000"})
        assert resp.status_code == 200
        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/dining_room_uplighter/set",
            {"color": {"hue": 0, "saturation": 100}, "brightness": 254},
        )
        session.refresh(z2m_bulb)
        assert z2m_bulb.color_rgb == "#ff0000"
        assert z2m_bulb.color_mode == "colour"

    def test_color_mode_toggle_alone_does_not_publish(self, client, z2m_bulb, session):
        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            resp = client.post(f"/devices/{z2m_bulb.id}/command", data={"color_mode": "colour"})
        assert resp.status_code == 200
        mock_pub.assert_not_awaited()
        session.refresh(z2m_bulb)
        assert z2m_bulb.color_mode == "colour"


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


class TestClimateChart:
    def test_chart_page(self, client, z2m_sensor):
        resp = client.get(f"/devices/{z2m_sensor.id}/climate-chart")
        assert resp.status_code == 200
        assert "Climate History" in resp.text

    def test_chart_page_404_for_non_sensor(self, client, z2m_plug):
        resp = client.get(f"/devices/{z2m_plug.id}/climate-chart")
        assert resp.status_code == 404

    def test_data_empty(self, client, z2m_sensor):
        resp = client.get(f"/devices/{z2m_sensor.id}/climate-chart/data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["timestamps"] == []
        assert data["temperature"] == []
        assert data["humidity"] == []

    def test_data_returns_samples(self, client, z2m_sensor, session):
        from datetime import datetime
        from app.devices.models import ClimateSample
        session.add(ClimateSample(
            device_id=z2m_sensor.id, temperature=21.5, humidity=55.2,
            timestamp=datetime.utcnow(),
        ))
        session.commit()
        resp = client.get(f"/devices/{z2m_sensor.id}/climate-chart/data")
        data = resp.json()
        assert len(data["timestamps"]) == 1
        assert data["temperature"][0] == 21.5
        assert data["humidity"][0] == 55.2


class TestPowerChart:
    def test_chart_page(self, client, z2m_plug):
        resp = client.get(f"/devices/{z2m_plug.id}/power-chart")
        assert resp.status_code == 200
        assert "Power History" in resp.text

    def test_data_empty(self, client, z2m_plug):
        resp = client.get(f"/devices/{z2m_plug.id}/power-chart/data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["timestamps"] == []
        assert data["energy_today"] == []
        assert data["energy_month"] == []

    def test_data_returns_samples(self, client, z2m_plug, session):
        from datetime import datetime
        from app.devices.models import PowerSample
        session.add(PowerSample(
            device_id=z2m_plug.id, voltage=230.1, power=7.5, current=0.03,
            energy_today=0.42, energy_month=12.7,
            timestamp=datetime.utcnow(),
        ))
        session.commit()
        resp = client.get(f"/devices/{z2m_plug.id}/power-chart/data")
        data = resp.json()
        assert len(data["timestamps"]) == 1
        assert data["energy_today"][0] == 0.42
        assert data["energy_month"][0] == 12.7

    def test_energy_daily_empty(self, client, z2m_plug):
        resp = client.get(f"/devices/{z2m_plug.id}/power-chart/energy-daily")
        assert resp.status_code == 200
        assert resp.json() == {"dates": [], "energy_today": []}

    def test_energy_daily_returns_rows_within_window(self, client, z2m_plug, session):
        from app.devices.models import EnergyDailySummary
        session.add(EnergyDailySummary(device_id=z2m_plug.id, date="2026-06-01", energy_today=0.9, energy_month=20.0))
        session.add(EnergyDailySummary(device_id=z2m_plug.id, date="2026-07-19", energy_today=0.42, energy_month=12.7))
        session.commit()
        resp = client.get(f"/devices/{z2m_plug.id}/power-chart/energy-daily?days=30")
        data = resp.json()
        assert data["dates"] == ["2026-07-19"]
        assert data["energy_today"] == [0.42]

    def test_energy_monthly_empty(self, client, z2m_plug):
        resp = client.get(f"/devices/{z2m_plug.id}/power-chart/energy-monthly")
        assert resp.status_code == 200
        assert resp.json() == {"months": [], "energy_month": []}

    def test_energy_monthly_takes_max_per_month(self, client, z2m_plug, session):
        from app.devices.models import EnergyDailySummary
        session.add(EnergyDailySummary(device_id=z2m_plug.id, date="2026-06-15", energy_today=0.3, energy_month=8.0))
        session.add(EnergyDailySummary(device_id=z2m_plug.id, date="2026-06-30", energy_today=0.5, energy_month=15.4))
        session.add(EnergyDailySummary(device_id=z2m_plug.id, date="2026-07-05", energy_today=0.4, energy_month=2.1))
        session.commit()
        resp = client.get(f"/devices/{z2m_plug.id}/power-chart/energy-monthly")
        data = resp.json()
        assert data["months"] == ["2026-06", "2026-07"]
        assert data["energy_month"] == [15.4, 2.1]


class TestRename:
    def test_sets_name_and_room(self, client, tuya_bulb, session):
        resp = client.post(f"/devices/{tuya_bulb.id}/rename", data={"name": "Bedside Lamp", "room": "Bedroom"})
        assert resp.status_code == 200
        session.refresh(tuya_bulb)
        assert tuya_bulb.name == "Bedside Lamp"
        assert tuya_bulb.room == "Bedroom"
        assert "Bedroom" in resp.text

    def test_room_is_optional(self, client, tuya_bulb, session):
        resp = client.post(f"/devices/{tuya_bulb.id}/rename", data={"name": "Bedside Lamp"})
        assert resp.status_code == 200
        session.refresh(tuya_bulb)
        assert tuya_bulb.room is None

    def test_blank_room_clears_existing_value(self, client, tuya_bulb, session):
        tuya_bulb.room = "Bedroom"
        session.add(tuya_bulb)
        session.commit()
        resp = client.post(f"/devices/{tuya_bulb.id}/rename", data={"name": "Bedside Lamp", "room": "  "})
        assert resp.status_code == 200
        session.refresh(tuya_bulb)
        assert tuya_bulb.room is None

    def test_blank_name_is_a_noop(self, client, tuya_bulb, session):
        original_name = tuya_bulb.name
        resp = client.post(f"/devices/{tuya_bulb.id}/rename", data={"name": "  ", "room": "Bedroom"})
        assert resp.status_code == 200
        session.refresh(tuya_bulb)
        assert tuya_bulb.name == original_name
        assert tuya_bulb.room is None

    def test_unknown_device_returns_404(self, client):
        resp = client.post("/devices/9999/rename", data={"name": "X"})
        assert resp.status_code == 404

    def test_grid_wraps_name_area_in_preserve_boundary(self, client, tuya_bulb):
        # The dashboard's 30s poll re-renders the whole grid via an innerHTML
        # swap on an ancestor; wrapping just the name/room area at the card
        # level lets hx-preserve keep an in-progress edit (open rename form)
        # intact across that background refresh.
        resp = client.get("/devices/grid")
        assert f'id="device-{tuya_bulb.id}-name-preserve" hx-preserve="true"' in resp.text

    def test_name_fragment_itself_is_not_marked_preserve(self, client, tuya_bulb):
        # Regression guard: hx-preserve on the element that the edit/save/cancel
        # buttons themselves outerHTML-swap breaks htmx's swap entirely (the
        # element vanishes instead of being replaced) — it must only be on the
        # stable wrapper one level up, never on this fragment's own root.
        resp = client.get(f"/devices/{tuya_bulb.id}/name")
        assert "hx-preserve" not in resp.text

    def test_rename_form_fragment_itself_is_not_marked_preserve(self, client, tuya_bulb):
        resp = client.get(f"/devices/{tuya_bulb.id}/rename-form")
        assert "hx-preserve" not in resp.text


class TestDeviceManagement:
    def test_delete_device(self, client, tuya_bulb, session):
        resp = client.post(f"/devices/{tuya_bulb.id}/delete")
        assert resp.status_code == 200
        assert session.exec(select(Device)).first() is None

    def test_delete_unknown_device(self, client):
        resp = client.post("/devices/9999/delete")
        assert resp.status_code == 404
