"""Tests for the IKEA DIRIGERA hub client + air quality sensor poller."""
import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.devices import dirigera
from app.devices.models import ClimateDailySummary, ClimateSample, Device, DeviceType, Integration
from app.services import automation_engine, dirigera_poller


def _hub_record(**overrides) -> dict:
    """Shape of a real /v1/devices environmentSensor entry (trimmed)."""
    attrs = {
        "customName": "ALPSTUGA air quality moni",
        "model": "ALPSTUGA air quality monitor",
        "currentTemperature": 20.530000686645508,
        "currentRH": 68,
        "currentCO2": 575,
        "currentPM25": 3,
        "windowOpen": False,
    }
    attrs.update(overrides.pop("attributes", {}))
    record = {
        "id": "<device_id>_1",
        "deviceType": "environmentSensor",
        "isReachable": True,
        "lastSeen": "2026-01-01T10:00:00.000Z",
        "attributes": attrs,
        "room": {"name": "Living room"},
    }
    record.update(overrides)
    return record


@pytest.fixture(autouse=True)
def _clear_last_sampled():
    dirigera_poller._last_sampled.clear()
    yield
    dirigera_poller._last_sampled.clear()


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("DIRIGERA_HOST", "192.168.x.x")
    monkeypatch.setenv("DIRIGERA_TOKEN", "<your_token>")


def _poll(engine, records):
    sensors = [dirigera.parse_environment_sensor(r) for r in records]
    with patch("app.services.dirigera_poller.engine", engine), \
         patch("app.services.dirigera_poller.dirigera_client.get_environment_sensors",
               new=AsyncMock(return_value=sensors)), \
         patch("app.services.dirigera_poller.check_state_triggers", new=AsyncMock()) as triggers:
        asyncio.run(dirigera_poller.poll_dirigera_devices())
    return triggers


class TestParse:
    def test_flattens_and_rounds(self):
        parsed = dirigera.parse_environment_sensor(_hub_record())
        assert parsed["hub_id"] == "<device_id>_1"
        assert parsed["name"] == "ALPSTUGA air quality moni"
        assert parsed["room"] == "Living room"
        assert parsed["online"] is True
        assert parsed["sensor_temperature"] == 20.5
        assert parsed["humidity"] == 68.0
        assert parsed["co2"] == 575
        assert parsed["pm25"] == 3
        assert parsed["window_open"] is False

    def test_missing_readings_are_none(self):
        rec = _hub_record()
        for k in ("currentTemperature", "currentRH", "currentCO2", "currentPM25"):
            del rec["attributes"][k]
        parsed = dirigera.parse_environment_sensor(rec)
        assert parsed["sensor_temperature"] is None
        assert parsed["co2"] is None


class TestPoller:
    def test_registers_new_sensor_as_climate_sensor(self, engine, session):
        triggers = _poll(engine, [_hub_record()])

        device = session.exec(select(Device)).one()
        assert device.type == DeviceType.sensor
        assert device.integration == Integration.dirigera
        assert device.room == "Living room"
        assert device.online is True
        assert (device.sensor_temperature, device.humidity, device.co2, device.pm25) == (20.5, 68.0, 575, 3)

        samples = session.exec(select(ClimateSample)).all()
        assert len(samples) == 1
        assert (samples[0].temperature, samples[0].co2, samples[0].pm25) == (20.5, 575, 3)

        triggers.assert_awaited_once()
        device_id, state = triggers.await_args.args
        assert device_id == device.id
        assert state["co2"] == 575 and state["window_open"] is False and state["online"] is True

    def test_does_not_overwrite_dashboard_rename(self, engine, session):
        _poll(engine, [_hub_record()])
        device = session.exec(select(Device)).one()
        device.name, device.room = "Air monitor", "Lounge"
        session.add(device)
        session.commit()

        _poll(engine, [_hub_record(attributes={"currentCO2": 900})])

        session.refresh(device)
        assert (device.name, device.room, device.co2) == ("Air monitor", "Lounge", 900)
        assert len(session.exec(select(Device)).all()) == 1

    def test_samples_only_when_hub_reading_is_fresh(self, engine, session):
        _poll(engine, [_hub_record()])
        _poll(engine, [_hub_record()])  # same lastSeen — hub hasn't refreshed
        assert len(session.exec(select(ClimateSample)).all()) == 1

        _poll(engine, [_hub_record(lastSeen="2026-01-01T10:05:00.000Z")])
        assert len(session.exec(select(ClimateSample)).all()) == 2

    def test_unreachable_marks_offline_and_keeps_last_readings(self, engine, session):
        _poll(engine, [_hub_record()])
        _poll(engine, [_hub_record(isReachable=False, lastSeen="2026-01-01T11:00:00.000Z",
                                   attributes={"currentCO2": None})])

        device = session.exec(select(Device)).one()
        assert device.online is False
        assert device.co2 == 575
        assert len(session.exec(select(ClimateSample)).all()) == 1

    def test_registered_while_unreachable_still_shows_last_known_readings(self, engine, session):
        triggers = _poll(engine, [_hub_record(isReachable=False)])

        device = session.exec(select(Device)).one()
        assert device.online is False
        assert (device.sensor_temperature, device.co2, device.pm25) == (20.5, 575, 3)
        assert session.exec(select(ClimateSample)).all() == []  # stale, not a new reading
        assert triggers.await_args.args[1]["online"] is False

    def test_not_configured_is_a_noop(self, engine, session, monkeypatch):
        monkeypatch.delenv("DIRIGERA_TOKEN")
        with patch("app.services.dirigera_poller.dirigera_client.get_environment_sensors",
                   new=AsyncMock()) as fetch:
            asyncio.run(dirigera_poller.poll_dirigera_devices())
        fetch.assert_not_awaited()

    def test_hub_error_is_swallowed(self, engine, session):
        with patch("app.services.dirigera_poller.engine", engine), \
             patch("app.services.dirigera_poller.dirigera_client.get_environment_sensors",
                   new=AsyncMock(side_effect=OSError("unreachable"))):
            asyncio.run(dirigera_poller.poll_dirigera_devices())
        assert session.exec(select(Device)).all() == []


class TestTriggers:
    def test_window_open_is_boolean_field(self):
        assert automation_engine._eval_condition("window_open", "eq", "true", {"window_open": True}) is True
        assert automation_engine._eval_condition("window_open", "eq", "true", {"window_open": False}) is False

    def test_co2_numeric_threshold(self):
        assert automation_engine._eval_condition("co2", "gt", "1000", {"co2": 1250}) is True
        assert automation_engine._eval_condition("co2", "gt", "1000", {"co2": 600}) is False


@pytest.fixture(name="aq_sensor")
def aq_sensor_fixture(session):
    device = Device(
        name="Air monitor", room="Living room", device_id="<device_id>_1",
        type=DeviceType.sensor, integration=Integration.dirigera, online=True,
        sensor_temperature=20.5, humidity=68, co2=1350, pm25=4, window_open=True,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


class TestUi:
    def test_card_shows_air_quality(self, client, aq_sensor):
        html = client.get("/").text
        assert "1350" in html and "CO₂ ppm" in html
        assert "PM2.5 µg/m³" in html
        assert "Window open" in html
        assert "text-red-500" in html  # 1350 ppm is in the ventilate band

    def test_plain_sensor_card_has_no_air_quality_row(self, client, z2m_sensor):
        assert "CO₂ ppm" not in client.get("/").text

    def test_chart_data_includes_co2_and_pm25(self, client, session, aq_sensor):
        session.add(ClimateSample(device_id=aq_sensor.id, temperature=20.5, humidity=68, co2=700, pm25=5,
                                  timestamp=datetime.utcnow() - timedelta(minutes=5)))
        session.commit()
        data = client.get(f"/devices/{aq_sensor.id}/climate-chart/data").json()
        assert data["co2"] == [700]
        assert data["pm25"] == [5]

    def test_chart_page_has_air_quality_panels(self, client, aq_sensor):
        html = client.get(f"/devices/{aq_sensor.id}/climate-chart").text
        assert 'id="co2-chart"' in html
        assert 'id="co2-daily-chart"' in html and 'id="pm25-daily-chart"' in html

    def test_plain_sensor_chart_page_has_no_air_quality_panels(self, client, z2m_sensor):
        assert "co2-daily-chart" not in client.get(f"/devices/{z2m_sensor.id}/climate-chart").text

    def test_daily_endpoint_includes_co2_and_pm25(self, client, session, aq_sensor):
        day = (date.today() - timedelta(days=10)).isoformat()
        session.add(ClimateDailySummary(
            device_id=aq_sensor.id, date=day, temp_sum=41.0, temp_count=2,
            co2_sum=1300.0, co2_count=2, co2_min=500.0, co2_max=800.0,
            pm25_sum=9.0, pm25_count=2, pm25_min=3.0, pm25_max=6.0,
        ))
        session.commit()
        data = client.get(f"/devices/{aq_sensor.id}/climate-chart/daily").json()
        assert data["co2_mean"] == [650]
        assert (data["co2_min"], data["co2_max"]) == ([500.0], [800.0])
        assert data["pm25_mean"] == [4.5]

    def test_daily_endpoint_plain_sensor_has_null_air_quality(self, client, session, z2m_sensor):
        session.add(ClimateDailySummary(device_id=z2m_sensor.id, date=(date.today() - timedelta(days=10)).isoformat(),
                                        temp_sum=20.0, temp_count=1))
        session.commit()
        data = client.get(f"/devices/{z2m_sensor.id}/climate-chart/daily").json()
        assert data["co2_mean"] == [None] and data["pm25_mean"] == [None]

    def test_included_in_climate_widget_room_line(self, client, session, aq_sensor):
        session.add(ClimateSample(device_id=aq_sensor.id, temperature=20.5, humidity=68,
                                  timestamp=datetime.utcnow() - timedelta(minutes=5)))
        session.commit()
        data = client.get("/climate/data").json()
        assert data["Living room"]["temperature"] == [20.5]
