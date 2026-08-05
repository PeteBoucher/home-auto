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

    def test_climate_widget_hidden_without_climate_devices(self, client, tuya_bulb):
        resp = client.get("/")
        assert "climate-widget-chart" not in resp.text

    def test_climate_widget_shown_with_sensor(self, client, z2m_sensor):
        resp = client.get("/")
        assert "climate-widget-chart" in resp.text


class TestTuyaCommands:
    def test_toggle_on(self, client, tuya_bulb):
        with (
            patch("app.services.device_commands.tuya_client.send_command", new=AsyncMock()),
            patch("app.services.device_commands.tuya_client.get_state", new=AsyncMock(return_value=_TUYA_STATE_ON)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"state": "true"})
        assert resp.status_code == 200
        assert "On" in resp.text

    def test_toggle_off(self, client, tuya_bulb):
        with (
            patch("app.services.device_commands.tuya_client.send_command", new=AsyncMock()),
            patch("app.services.device_commands.tuya_client.get_state", new=AsyncMock(return_value=_TUYA_STATE_OFF)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"state": "false"})
        assert resp.status_code == 200
        assert "Off" in resp.text

    def test_brightness(self, client, tuya_bulb):
        state = {**_TUYA_STATE_ON, "brightness": 30}
        with (
            patch("app.services.device_commands.tuya_client.send_command", new=AsyncMock()),
            patch("app.services.device_commands.tuya_client.get_state", new=AsyncMock(return_value=state)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"brightness": "30"})
        assert resp.status_code == 200
        assert 'name="brightness"' in resp.text
        assert 'value="30"' in resp.text

    def test_color_temp(self, client, tuya_bulb):
        state = {**_TUYA_STATE_ON, "color_temp": 25}
        with (
            patch("app.services.device_commands.tuya_client.send_command", new=AsyncMock()),
            patch("app.services.device_commands.tuya_client.get_state", new=AsyncMock(return_value=state)),
        ):
            resp = client.post(f"/devices/{tuya_bulb.id}/command", data={"color_temp": "25"})
        assert resp.status_code == 200
        assert 'name="color_temp"' in resp.text

    def test_switch_to_colour_mode(self, client, tuya_bulb):
        state = {**_TUYA_STATE_ON, "color_mode": "colour", "color_rgb": "#ff0000"}
        with (
            patch("app.services.device_commands.tuya_client.send_command", new=AsyncMock()),
            patch("app.services.device_commands.tuya_client.get_state", new=AsyncMock(return_value=state)),
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
            patch("app.services.device_commands.tuya_client.send_command", new=AsyncMock()),
            patch("app.services.device_commands.tuya_client.get_state", new=AsyncMock(return_value=state)),
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


class TestClimateWidget:
    def test_empty_returns_empty_dict(self, client):
        resp = client.get("/climate/data")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_single_room_sensor(self, client, session):
        from datetime import datetime
        from app.devices.models import ClimateSample, Device, DeviceType, Integration
        device = Device(
            name="Temp/Humidity", room="Living room", device_id="s1",
            type=DeviceType.sensor, integration=Integration.zigbee2mqtt, online=True,
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        session.add(ClimateSample(device_id=device.id, temperature=21.0, timestamp=datetime.utcnow()))
        session.commit()

        resp = client.get("/climate/data")
        data = resp.json()
        assert list(data.keys()) == ["Living room"]
        assert data["Living room"]["temperature"] == [21.0]

    def test_multiple_sensors_same_room_averaged(self, client, session):
        from datetime import datetime
        from app.devices.models import ClimateSample, Device, DeviceType, Integration
        now = datetime.utcnow()
        d1 = Device(name="A", room="Living room", device_id="s1", type=DeviceType.sensor, integration=Integration.zigbee2mqtt)
        d2 = Device(name="B", room="Living room", device_id="s2", type=DeviceType.sensor, integration=Integration.zigbee2mqtt)
        session.add(d1)
        session.add(d2)
        session.commit()
        session.refresh(d1)
        session.refresh(d2)
        session.add(ClimateSample(device_id=d1.id, temperature=20.0, timestamp=now))
        session.add(ClimateSample(device_id=d2.id, temperature=24.0, timestamp=now))
        session.commit()

        resp = client.get("/climate/data")
        data = resp.json()
        assert list(data.keys()) == ["Living room"]
        assert data["Living room"]["temperature"] == [22.0]

    def test_uses_device_name_when_no_room(self, client, session):
        from datetime import datetime
        from app.devices.models import ClimateSample, Device, DeviceType, Integration
        device = Device(name="Unassigned Sensor", device_id="s1", type=DeviceType.sensor, integration=Integration.zigbee2mqtt)
        session.add(device)
        session.commit()
        session.refresh(device)
        session.add(ClimateSample(device_id=device.id, temperature=19.5, timestamp=datetime.utcnow()))
        session.commit()

        resp = client.get("/climate/data")
        assert list(resp.json().keys()) == ["Unassigned Sensor"]

    def test_ac_indoor_outdoor_included(self, client, session):
        from datetime import datetime
        from app.devices.models import AcSample, Device, DeviceType, Integration
        ac = Device(name="Living Room A/C", device_id="ac-1", type=DeviceType.ac, integration=Integration.hon)
        session.add(ac)
        session.commit()
        session.refresh(ac)
        session.add(AcSample(device_id=ac.id, indoor_temp=23.0, outdoor_temp=31.0, timestamp=datetime.utcnow()))
        session.commit()

        resp = client.get("/climate/data")
        data = resp.json()
        assert data["AC Indoor"]["temperature"] == [23.0]
        assert data["AC Outdoor"]["temperature"] == [31.0]

    def test_respects_hours_window(self, client, session):
        from datetime import datetime, timedelta
        from app.devices.models import ClimateSample, Device, DeviceType, Integration
        device = Device(name="Sensor", room="Attic", device_id="s1", type=DeviceType.sensor, integration=Integration.zigbee2mqtt)
        session.add(device)
        session.commit()
        session.refresh(device)
        session.add(ClimateSample(
            device_id=device.id, temperature=15.0,
            timestamp=datetime.utcnow() - timedelta(hours=10),
        ))
        session.commit()

        resp = client.get("/climate/data?hours=6")
        assert resp.json() == {}


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


class TestGroups:
    def test_groups_page_empty(self, client):
        resp = client.get("/groups")
        assert resp.status_code == 200
        assert "No groups yet" in resp.text

    def test_create_group(self, client, z2m_bulb, tuya_bulb, session):
        from app.devices.models import DeviceGroup
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            resp = client.post(
                "/groups",
                data={"name": "Lounge & Dining", "device_ids": [str(z2m_bulb.id), str(tuya_bulb.id)]},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        group = session.exec(select(DeviceGroup)).first()
        assert group.name == "Lounge & Dining"
        session.refresh(z2m_bulb)
        session.refresh(tuya_bulb)
        assert z2m_bulb.group_id == group.id
        assert tuya_bulb.group_id == group.id

    def test_groups_page_lists_members(self, client, z2m_bulb, tuya_bulb):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            client.post("/groups", data={"name": "Lounge & Dining", "device_ids": [str(z2m_bulb.id), str(tuya_bulb.id)]})
        resp = client.get("/groups")
        assert "Lounge &amp; Dining" in resp.text  # Jinja2 HTML-escapes "&"
        assert z2m_bulb.name in resp.text
        assert tuya_bulb.name in resp.text

    def test_group_command_toggles_on(self, client, z2m_bulb, session):
        from app.devices.models import DeviceGroup
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            client.post("/groups", data={"name": "Lights", "device_ids": [str(z2m_bulb.id)]})
        group = session.exec(select(DeviceGroup)).first()
        with patch("app.services.groups.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            resp = client.post(f"/groups/{group.id}/command", data={"state": "true"})
        assert resp.status_code == 200
        assert "On" in resp.text
        mock_pub.assert_awaited_once()

    def test_delete_group_removes_it(self, client, z2m_bulb, session):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            client.post("/groups", data={"name": "Lights", "device_ids": [str(z2m_bulb.id)]})
        from app.devices.models import DeviceGroup
        group = session.exec(select(DeviceGroup)).first()
        with patch("app.services.groups.mqtt_client.remove_zigbee_group", new=AsyncMock()):
            resp = client.post(f"/groups/{group.id}/delete")
        assert resp.status_code == 200
        assert resp.text == ""
        session.refresh(z2m_bulb)
        assert z2m_bulb.group_id is None

    def test_delete_unknown_group_404(self, client):
        resp = client.post("/groups/9999/delete")
        assert resp.status_code == 404

    def test_update_members(self, client, z2m_bulb, z2m_plug, session):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            client.post("/groups", data={"name": "Lights", "device_ids": [str(z2m_bulb.id)]})
        from app.devices.models import DeviceGroup
        group = session.exec(select(DeviceGroup)).first()
        with patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.remove_group_member", new=AsyncMock()):
            resp = client.post(
                f"/groups/{group.id}/members",
                data={"device_ids": [str(z2m_plug.id)]},
                follow_redirects=False,
            )
        assert resp.status_code == 303
        session.refresh(z2m_bulb)
        session.refresh(z2m_plug)
        assert z2m_bulb.group_id is None
        assert z2m_plug.group_id == group.id

    def test_individual_command_sets_override(self, client, z2m_bulb, session):
        with patch("app.services.groups.mqtt_client.create_zigbee_group", new=AsyncMock()), \
             patch("app.services.groups.mqtt_client.add_group_member", new=AsyncMock()):
            client.post("/groups", data={"name": "Lights", "device_ids": [str(z2m_bulb.id)]})

        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()):
            resp = client.post(f"/devices/{z2m_bulb.id}/command", data={"state": "true"})
        assert resp.status_code == 200
        assert "Independent" in resp.text
        session.refresh(z2m_bulb)
        assert z2m_bulb.group_override is True

    def test_command_on_ungrouped_device_does_not_set_override(self, client, z2m_bulb, session):
        with patch("app.api.devices.mqtt_client.publish", new=AsyncMock()):
            client.post(f"/devices/{z2m_bulb.id}/command", data={"state": "true"})
        session.refresh(z2m_bulb)
        assert z2m_bulb.group_override is False


class TestHonCommand:
    @pytest.fixture(name="hon_device")
    def hon_device_fixture(self, session):
        from app.devices.models import Device, DeviceType, Integration
        device = Device(
            name="Living Room A/C", device_id="ac-unit-1",
            type=DeviceType.ac, integration=Integration.hon,
            online=True, state=True, temperature=22, ac_mode="cool", fan_speed=2,
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        return device

    def test_eco_quiet_and_louvre_pass_through(self, client, hon_device, session):
        new_state = {
            "online": True, "state": True, "temperature": 22, "ac_mode": "cool", "fan_speed": 2,
            "eco": True, "quiet": True, "louvre_position": 8,
            "indoor_temp": 24.0, "outdoor_temp": 33.0,
        }
        with patch("app.api.devices.hon_client.send_command", new=AsyncMock()) as mock_send, \
             patch("app.api.devices.hon_client.get_state", new=AsyncMock(return_value=new_state)):
            resp = client.post(
                f"/devices/{hon_device.id}/command",
                data={"eco": "true", "quiet": "true", "louvre_position": "8"},
            )
        assert resp.status_code == 200
        mock_send.assert_awaited_once_with("ac-unit-1", {"eco": True, "quiet": True, "louvre_position": 8})
        session.refresh(hon_device)
        assert hon_device.eco is True
        assert hon_device.quiet is True
        assert hon_device.louvre_position == 8
        assert hon_device.indoor_temp == 24.0
        assert hon_device.outdoor_temp == 33.0


class TestHonImport:
    def test_import_seeds_initial_state(self, client, session):
        from app.devices.models import Device
        state = {
            "online": True, "state": True, "temperature": 21,
            "ac_mode": "cool", "fan_speed": 2,
            "eco": True, "quiet": False, "louvre_position": 5,
            "indoor_temp": 23.5, "outdoor_temp": 30.0,
        }
        with patch("app.api.devices.hon_client.get_state", new=AsyncMock(return_value=state)):
            resp = client.post("/devices/hon/ac-unit-1", data={"name": "Living Room A/C", "type": "ac"})
        assert resp.status_code == 200
        device = session.exec(select(Device).where(Device.device_id == "ac-unit-1")).first()
        assert device is not None
        assert device.online is True
        assert device.state is True
        assert device.temperature == 21
        assert device.ac_mode == "cool"
        assert device.fan_speed == 2
        assert device.eco is True
        assert device.quiet is False
        assert device.louvre_position == 5
        assert device.indoor_temp == 23.5
        assert device.outdoor_temp == 30.0

    def test_import_already_registered_409(self, client, session):
        from app.devices.models import Device, DeviceType, Integration
        session.add(Device(name="Existing", device_id="ac-unit-1", type=DeviceType.ac, integration=Integration.hon))
        session.commit()
        with patch("app.api.devices.hon_client.get_state", new=AsyncMock()) as mock_get:
            resp = client.post("/devices/hon/ac-unit-1", data={"name": "Dup", "type": "ac"})
        assert resp.status_code == 409
        mock_get.assert_not_awaited()


class TestAcChart:
    @pytest.fixture(name="hon_device")
    def hon_device_fixture(self, session):
        from app.devices.models import Device, DeviceType, Integration
        device = Device(
            name="Living Room A/C", device_id="ac-unit-1",
            type=DeviceType.ac, integration=Integration.hon,
            online=True, state=True, temperature=22, ac_mode="cool", fan_speed=2,
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        return device

    def test_chart_page(self, client, hon_device):
        resp = client.get(f"/devices/{hon_device.id}/ac-chart")
        assert resp.status_code == 200
        assert "Temperature History" in resp.text

    def test_chart_page_404_for_non_ac(self, client, z2m_plug):
        resp = client.get(f"/devices/{z2m_plug.id}/ac-chart")
        assert resp.status_code == 404

    def test_chart_link_shown_when_off_but_online(self, client, hon_device, session):
        hon_device.state = False
        session.add(hon_device)
        session.commit()
        resp = client.get("/")
        assert f"/devices/{hon_device.id}/ac-chart" in resp.text

    def test_chart_link_hidden_when_offline(self, client, hon_device, session):
        hon_device.online = False
        session.add(hon_device)
        session.commit()
        resp = client.get("/")
        assert f"/devices/{hon_device.id}/ac-chart" not in resp.text

    def test_data_empty(self, client, hon_device):
        resp = client.get(f"/devices/{hon_device.id}/ac-chart/data")
        assert resp.status_code == 200
        data = resp.json()
        assert data["timestamps"] == []
        assert data["temperature"] == []
        assert data["indoor_temp"] == []
        assert data["outdoor_temp"] == []
        assert data["ac_state"] == []

    def test_data_returns_samples(self, client, hon_device, session):
        from datetime import datetime
        from app.devices.models import AcSample
        session.add(AcSample(
            device_id=hon_device.id, temperature=22, indoor_temp=24.5, outdoor_temp=31.0,
            ac_state=False, timestamp=datetime.utcnow(),
        ))
        session.commit()
        resp = client.get(f"/devices/{hon_device.id}/ac-chart/data")
        data = resp.json()
        assert len(data["timestamps"]) == 1
        assert data["temperature"][0] == 22
        assert data["indoor_temp"][0] == 24.5
        assert data["outdoor_temp"][0] == 31.0
        assert data["ac_state"][0] is False

    def test_data_sun_events_empty_without_lat_lon(self, client, hon_device, monkeypatch):
        monkeypatch.delenv("LAT", raising=False)
        monkeypatch.delenv("LON", raising=False)
        resp = client.get(f"/devices/{hon_device.id}/ac-chart/data")
        assert resp.json()["sun_events"] == []

    def test_data_sun_events_present_with_lat_lon(self, client, hon_device, monkeypatch):
        from datetime import datetime
        monkeypatch.setenv("LAT", "36.44")
        monkeypatch.setenv("LON", "-5.27")
        resp = client.get(f"/devices/{hon_device.id}/ac-chart/data?hours=24")
        events = resp.json()["sun_events"]
        assert len(events) == (24 // 24 + 2) * 2
        assert {e["type"] for e in events} == {"sunrise", "sunset"}
        for e in events:
            datetime.fromisoformat(e["time"])  # parseable


class TestAutomationWithinTrigger:
    def test_create_captures_compare_field(self, client, z2m_bulb, session):
        from app.devices.models import Automation
        resp = client.post("/automations", data={
            "name": "Cool enough, shut off",
            "enabled": "1",
            "trigger_type": "device_state",
            "trigger_device_id": str(z2m_bulb.id),
            "trigger_field": "outdoor_temp",
            "trigger_operator": "within",
            "trigger_value": "2",
            "trigger_compare_field": "temperature",
            "action_device_id": str(z2m_bulb.id),
            "action_type": "set_state_off",
        })
        assert resp.status_code == 200
        auto = session.exec(select(Automation).where(Automation.name == "Cool enough, shut off")).first()
        assert auto is not None
        assert auto.trigger_operator == "within"
        assert auto.trigger_value == "2"
        assert auto.trigger_compare_field == "temperature"
