"""Tests for MQTT state application logic."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from app.devices.models import Device, DeviceType, Integration
from app.devices import mqtt as mqtt_module
from app.devices.mqtt import _apply_state, build_set_payload, get_zigbee_bulbs


@pytest.fixture(name="z2m_sensor")
def z2m_sensor_fixture(session, engine):
    device = Device(
        name="Bedroom Sensor",
        device_id="bedroom_sensor",
        type=DeviceType.sensor,
        integration=Integration.zigbee2mqtt,
        online=True,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="z2m_device")
def z2m_device_fixture(session, engine):
    device = Device(
        name="Test Socket",
        device_id="test_socket",
        type=DeviceType.plug,
        integration=Integration.zigbee2mqtt,
        online=True,
        state=False,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="z2m_bulb")
def z2m_bulb_fixture(session, engine):
    device = Device(
        name="Test Bulb",
        device_id="test_bulb",
        type=DeviceType.bulb,
        integration=Integration.zigbee2mqtt,
        online=True,
        state=True,
        dimmable=True,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def _refresh(session, device):
    session.expire(device)
    return session.get(Device, device.id)


class TestApplyState:
    def test_turns_on(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"state": "ON"})
        d = _refresh(session, z2m_device)
        assert d.state is True
        assert d.online is True

    def test_turns_off(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"state": "OFF"})
        d = _refresh(session, z2m_device)
        assert d.state is False

    def test_case_insensitive_state(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"state": "on"})
        d = _refresh(session, z2m_device)
        assert d.state is True

    def test_brightness_scaling(self, engine, session, z2m_device):
        # Z2M sends 0-254; app stores as 0-100 percentage
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"brightness": 254})
        d = _refresh(session, z2m_device)
        assert d.brightness == 100

    def test_brightness_midpoint(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"brightness": 127})
        d = _refresh(session, z2m_device)
        assert d.brightness == 50

    def test_energy_today_and_month(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"power": 7.5, "energy_today": 0.4234, "energy_month": 12.789})
        d = _refresh(session, z2m_device)
        assert d.energy_today == 0.423
        assert d.energy_month == 12.789

    def test_energy_today_and_month_logged_to_power_sample(self, engine, session, z2m_device):
        from app.devices.models import PowerSample
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"power": 7.5, "energy_today": 0.42, "energy_month": 12.7})
        samples = session.exec(select(PowerSample).where(PowerSample.device_id == z2m_device.id)).all()
        assert len(samples) == 1
        assert samples[0].energy_today == 0.42
        assert samples[0].energy_month == 12.7

    def test_energy_today_upserts_daily_summary(self, engine, session, z2m_device):
        from datetime import date
        from app.devices.models import EnergyDailySummary
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"power": 7.5, "energy_today": 0.42, "energy_month": 12.7})
        rows = session.exec(select(EnergyDailySummary).where(EnergyDailySummary.device_id == z2m_device.id)).all()
        assert len(rows) == 1
        assert rows[0].date == date.today().isoformat()
        assert rows[0].energy_today == 0.42
        assert rows[0].energy_month == 12.7

    def test_energy_today_summary_updates_same_day_row(self, engine, session, z2m_device):
        from app.devices.models import EnergyDailySummary
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"energy_today": 0.42, "energy_month": 12.7})
            _apply_state("test_socket", {"energy_today": 0.55, "energy_month": 12.8})
        rows = session.exec(select(EnergyDailySummary).where(EnergyDailySummary.device_id == z2m_device.id)).all()
        assert len(rows) == 1
        assert rows[0].energy_today == 0.55
        assert rows[0].energy_month == 12.8

    def test_energy_summary_not_touched_without_energy_fields(self, engine, session, z2m_device):
        from app.devices.models import EnergyDailySummary
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"power": 7.5})
        rows = session.exec(select(EnergyDailySummary).where(EnergyDailySummary.device_id == z2m_device.id)).all()
        assert rows == []

    def test_marks_offline(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {}, online=False)
        d = _refresh(session, z2m_device)
        assert d.online is False

    def test_marks_online(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {}, online=False)
            _apply_state("test_socket", {}, online=True)
        d = _refresh(session, z2m_device)
        assert d.online is True

    def test_unknown_device_is_noop(self, engine, session):
        # should not raise
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("no_such_device", {"state": "ON"})

    def test_sensor_temperature(self, engine, session, z2m_sensor):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("bedroom_sensor", {"temperature": 21.5, "humidity": 55.2, "battery": 86})
        d = _refresh(session, z2m_sensor)
        assert d.sensor_temperature == 21.5
        assert d.humidity == 55.2
        assert d.battery == 86

    def test_sensor_not_marked_offline_by_availability(self, engine, session, z2m_sensor):
        # Availability heartbeat must not flip a sensor offline between readings
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("bedroom_sensor", {"temperature": 21.5, "humidity": 55.2})
            _apply_state("bedroom_sensor", {}, online=False)
        d = _refresh(session, z2m_sensor)
        assert d.online is True

    def test_sensor_temperature_rounded(self, engine, session, z2m_sensor):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("bedroom_sensor", {"temperature": 21.567, "humidity": 55.234})
        d = _refresh(session, z2m_sensor)
        assert d.sensor_temperature == 21.6
        assert d.humidity == 55.2

    def test_color_temp_reported_as_pct(self, engine, session, z2m_bulb):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_bulb", {"color_temp": 153, "color_mode": "color_temp"})
        d = _refresh(session, z2m_bulb)
        assert d.color_temp == 100
        assert d.color_mode == "white"

    def test_color_hs_reported_as_rgb_hex(self, engine, session, z2m_bulb):
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_bulb", {
                "brightness": 254,
                "color": {"hue": 0, "saturation": 100, "x": 0.7, "y": 0.3},
                "color_mode": "hs",
            })
        d = _refresh(session, z2m_bulb)
        assert d.color_rgb == "#ff0000"
        assert d.color_mode == "colour"

    def test_ignores_non_z2m_device(self, engine, session):
        # A Tuya device with the same device_id should not be updated
        from app.devices.models import DeviceType
        tuya = Device(
            name="Tuya Thing",
            device_id="test_socket",
            type=DeviceType.plug,
            integration=Integration.tuya,
            online=True,
            state=False,
        )
        session.add(tuya)
        session.commit()
        with patch("app.devices.mqtt.engine", engine):
            _apply_state("test_socket", {"state": "ON"})
        # _apply_state filters by integration == zigbee2mqtt, so tuya device unchanged
        session.expire(tuya)
        assert session.get(Device, tuya.id).state is False


class TestGetZigbeeBulbs:
    def test_returns_dimmable_zigbee_bulb(self, engine, session, z2m_bulb):
        with patch("app.devices.mqtt.engine", engine):
            bulbs = get_zigbee_bulbs()
        assert [b.id for b in bulbs] == [z2m_bulb.id]

    def test_excludes_non_dimmable_bulb(self, engine, session):
        device = Device(
            name="Plain Bulb",
            device_id="plain_bulb",
            type=DeviceType.bulb,
            integration=Integration.zigbee2mqtt,
            online=True,
            dimmable=False,
        )
        session.add(device)
        session.commit()
        with patch("app.devices.mqtt.engine", engine):
            bulbs = get_zigbee_bulbs()
        assert bulbs == []

    def test_excludes_non_bulb_device(self, engine, session, z2m_device):
        with patch("app.devices.mqtt.engine", engine):
            bulbs = get_zigbee_bulbs()
        assert bulbs == []

    def test_excludes_tuya_bulb(self, engine, session):
        device = Device(
            name="Tuya Bulb",
            device_id="tuya_bulb_001",
            local_key="key",
            ip_address="192.168.x.x",
            type=DeviceType.bulb,
            integration=Integration.tuya,
            dimmable=True,
        )
        session.add(device)
        session.commit()
        with patch("app.devices.mqtt.engine", engine):
            bulbs = get_zigbee_bulbs()
        assert bulbs == []


class TestBuildSetPayload:
    def test_state_on(self):
        assert build_set_payload({"state": True}) == {"state": "ON"}

    def test_state_off(self):
        assert build_set_payload({"state": False}) == {"state": "OFF"}

    def test_brightness_scaled_to_254(self):
        assert build_set_payload({"brightness": 100})["brightness"] == 254

    def test_color_temp_converted_to_mireds(self):
        assert build_set_payload({"color_temp": 0})["color_temp"] == 556

    def test_color_rgb_converted_to_hue_saturation(self):
        payload = build_set_payload({"color_rgb": "#ff0000"})
        assert payload["color"] == {"hue": 0, "saturation": 100}
        assert payload["brightness"] == 254

    def test_empty_command_yields_empty_payload(self):
        assert build_set_payload({}) == {}


class TestZigbeeGroupManagement:
    def test_create_zigbee_group(self):
        with patch("app.devices.mqtt.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(mqtt_module.create_zigbee_group("group_1"))
        mock_pub.assert_awaited_once_with("zigbee2mqtt/bridge/request/group/add", {"friendly_name": "group_1"})

    def test_remove_zigbee_group(self):
        with patch("app.devices.mqtt.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(mqtt_module.remove_zigbee_group("group_1"))
        mock_pub.assert_awaited_once_with("zigbee2mqtt/bridge/request/group/remove", {"id": "group_1"})

    def test_add_group_member(self):
        with patch("app.devices.mqtt.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(mqtt_module.add_group_member("group_1", "dining_room_uplighter"))
        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/bridge/request/group/members/add",
            {"group": "group_1", "device": "dining_room_uplighter"},
        )

    def test_remove_group_member(self):
        with patch("app.devices.mqtt.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(mqtt_module.remove_group_member("group_1", "dining_room_uplighter"))
        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/bridge/request/group/members/remove",
            {"group": "group_1", "device": "dining_room_uplighter"},
        )
