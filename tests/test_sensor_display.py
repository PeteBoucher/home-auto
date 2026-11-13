"""Tests for mirroring one sensor's reading onto another's physical screen
(Z2M external_temperature/external_humidity, e.g. SNZB-02DR2)."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.devices.models import Device, DeviceType, Integration
from app.services import sensor_display


@pytest.fixture(name="outdoor_sensor")
def outdoor_sensor_fixture(session):
    device = Device(
        name="Temp/Humidity", room="Front yard", device_id="front_yard_sensor",
        type=DeviceType.sensor, integration=Integration.zigbee2mqtt,
        online=True, sensor_temperature=32.7, humidity=51.2,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="display_sensor")
def display_sensor_fixture(session):
    device = Device(
        name="Temp/Humidity", room="Living room", device_id="living_room_sensor",
        type=DeviceType.sensor, integration=Integration.zigbee2mqtt,
        online=True, sensor_temperature=26.5, humidity=69.1,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


class TestSetDisplaySource:
    def test_setting_a_source_switches_display_to_external_and_pushes_reading(self, session, outdoor_sensor, display_sensor):
        with patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.set_display_source(session, display_sensor, outdoor_sensor.id))

        session.refresh(display_sensor)
        assert display_sensor.display_source_id == outdoor_sensor.id
        calls = [c.args for c in mock_pub.await_args_list]
        assert ("zigbee2mqtt/living_room_sensor/set", {"temperature_sensor_select": "external"}) in calls
        assert ("zigbee2mqtt/living_room_sensor/set", {"external_temperature": 32.7, "external_humidity": 51.2}) in calls

    def test_clearing_source_reverts_display_to_internal(self, session, outdoor_sensor, display_sensor):
        display_sensor.display_source_id = outdoor_sensor.id
        session.add(display_sensor)
        session.commit()
        with patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.set_display_source(session, display_sensor, None))

        session.refresh(display_sensor)
        assert display_sensor.display_source_id is None
        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/living_room_sensor/set", {"temperature_sensor_select": "internal"}
        )


class TestSyncDisplayTargets:
    def test_pushes_fresh_reading_to_linked_target(self, engine, session, outdoor_sensor, display_sensor):
        display_sensor.display_source_id = outdoor_sensor.id
        session.add(display_sensor)
        session.commit()
        outdoor_sensor.sensor_temperature = 33.5
        outdoor_sensor.humidity = 48.0
        session.add(outdoor_sensor)
        session.commit()

        with patch("app.services.sensor_display.engine", engine), \
             patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.sync_display_targets(outdoor_sensor.id))

        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/living_room_sensor/set", {"external_temperature": 33.5, "external_humidity": 48.0}
        )

    def test_noop_when_no_one_is_linked_to_it(self, engine, session, outdoor_sensor):
        with patch("app.services.sensor_display.engine", engine), \
             patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.sync_display_targets(outdoor_sensor.id))
        mock_pub.assert_not_awaited()

    def test_unknown_source_is_a_noop(self, engine, session):
        with patch("app.services.sensor_display.engine", engine), \
             patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.sync_display_targets(99999))
        mock_pub.assert_not_awaited()


@pytest.fixture(name="balcony_sensor")
def balcony_sensor_fixture(session):
    device = Device(
        name="Temp/Humidity", room="Balcony", device_id="balcony_sensor",
        type=DeviceType.sensor, integration=Integration.zigbee2mqtt,
        online=True, sensor_temperature=24.1, humidity=60.0, climate_combine_outdoor=True,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="combined_setup")
def combined_setup_fixture(session, outdoor_sensor, balcony_sensor, display_sensor):
    outdoor_sensor.climate_combine_outdoor = True
    display_sensor.display_outdoor_combined = True
    session.add(outdoor_sensor)
    session.add(display_sensor)
    session.commit()
    return outdoor_sensor, balcony_sensor, display_sensor


class TestOutdoorCombinedDisplay:
    def test_setting_combined_pushes_lowest_outdoor_reading(self, session, outdoor_sensor, balcony_sensor, display_sensor):
        outdoor_sensor.climate_combine_outdoor = True
        session.add(outdoor_sensor)
        session.commit()
        with patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.set_display_source(session, display_sensor, None, outdoor_combined=True))

        session.refresh(display_sensor)
        assert display_sensor.display_outdoor_combined is True
        assert display_sensor.display_source_id is None
        calls = [c.args for c in mock_pub.await_args_list]
        assert ("zigbee2mqtt/living_room_sensor/set", {"temperature_sensor_select": "external"}) in calls
        assert ("zigbee2mqtt/living_room_sensor/set", {"external_temperature": 24.1, "external_humidity": 60.0}) in calls

    def test_picking_a_single_source_clears_combined(self, session, combined_setup):
        outdoor_sensor, _, display_sensor = combined_setup
        with patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()):
            asyncio.run(sensor_display.set_display_source(session, display_sensor, outdoor_sensor.id))
        session.refresh(display_sensor)
        assert display_sensor.display_outdoor_combined is False
        assert display_sensor.display_source_id == outdoor_sensor.id

    def test_switches_to_whichever_sensor_is_now_lowest(self, engine, session, combined_setup):
        outdoor_sensor, balcony_sensor, _ = combined_setup
        # Afternoon: the west-facing balcony catches the sun, front yard is now the cooler one.
        balcony_sensor.sensor_temperature = 35.0
        outdoor_sensor.sensor_temperature = 22.0
        outdoor_sensor.humidity = 55.0
        session.add(balcony_sensor)
        session.add(outdoor_sensor)
        session.commit()

        with patch("app.services.sensor_display.engine", engine), \
             patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.sync_display_targets(balcony_sensor.id))

        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/living_room_sensor/set", {"external_temperature": 22.0, "external_humidity": 55.0}
        )

    def test_offline_sensor_is_ignored(self, engine, session, combined_setup):
        outdoor_sensor, balcony_sensor, _ = combined_setup
        balcony_sensor.online = False  # last reading 24.1 is lower, but stale
        session.add(balcony_sensor)
        session.commit()

        with patch("app.services.sensor_display.engine", engine), \
             patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.sync_display_targets(outdoor_sensor.id))

        mock_pub.assert_awaited_once_with(
            "zigbee2mqtt/living_room_sensor/set", {"external_temperature": 32.7, "external_humidity": 51.2}
        )

    def test_non_outdoor_source_does_not_push_to_combined_targets(self, engine, session, combined_setup):
        other = Device(
            name="Temp/Humidity", room="Study", device_id="study_sensor",
            type=DeviceType.sensor, integration=Integration.zigbee2mqtt,
            online=True, sensor_temperature=21.0,
        )
        session.add(other)
        session.commit()
        with patch("app.services.sensor_display.engine", engine), \
             patch("app.services.sensor_display.mqtt_client.publish", new=AsyncMock()) as mock_pub:
            asyncio.run(sensor_display.sync_display_targets(other.id))
        mock_pub.assert_not_awaited()
