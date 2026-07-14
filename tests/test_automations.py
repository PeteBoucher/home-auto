"""Tests for weather service and rain automation."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.weather import is_raining
import app.services.automations as auto_module
import app.services.automation_engine as auto_engine
from app.devices.models import Automation, Device, DeviceType, Integration, TriggerType


class TestIsRaining:
    @pytest.mark.parametrize("code,expected", [
        (0,  False),   # clear sky
        (3,  False),   # overcast
        (51, True),    # light drizzle
        (61, True),    # slight rain
        (65, True),    # heavy rain
        (80, True),    # slight showers
        (95, True),    # thunderstorm
        (99, True),    # heavy thunderstorm with hail
    ])
    def test_weather_codes(self, code, expected):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"current": {"weather_code": code}}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("app.services.weather.httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(is_raining(36.44, -5.27))
        assert result == expected


class TestRainAutomation:
    def setup_method(self):
        auto_module._raining = False
        auto_module._saved.clear()

    @pytest.fixture
    def bulb(self, session):
        from app.devices.models import Device, DeviceType, Integration
        d = Device(
            name="Test Bulb",
            device_id="dev_bulb_001",
            local_key="key",
            ip_address="192.168.x.x",
            type=DeviceType.bulb,
            integration=Integration.tuya,
            protocol_version=3.5,
            online=True,
            state=False,
            brightness=80,
            color_temp=50,
            color_mode="white",
        )
        session.add(d)
        session.commit()
        session.refresh(d)
        return d

    def test_activates_on_rain(self, engine, bulb):
        with (
            patch("app.devices.tuya.engine", engine),
            patch("app.services.automations.is_raining", new=AsyncMock(return_value=True)),
            patch("app.services.automations.tuya_client.send_command", new=AsyncMock()) as mock_cmd,
            patch.dict("os.environ", {"LAT": "36.44", "LON": "-5.27"}),
        ):
            asyncio.run(auto_module.check_weather())

        assert auto_module._raining is True
        assert bulb.id in auto_module._saved
        calls = [str(c) for c in mock_cmd.call_args_list]
        assert any("True" in c for c in calls)
        assert any("#add8e6" in c for c in calls)

    def test_restores_when_cleared(self, engine, bulb):
        auto_module._raining = True
        auto_module._saved[bulb.id] = {
            "state": False,
            "color_mode": "white",
            "color_rgb": None,
            "brightness": 80,
            "color_temp": 50,
        }
        with (
            patch("app.devices.tuya.engine", engine),
            patch("app.services.automations.is_raining", new=AsyncMock(return_value=False)),
            patch("app.services.automations.tuya_client.send_command", new=AsyncMock()) as mock_cmd,
            patch.dict("os.environ", {"LAT": "36.44", "LON": "-5.27"}),
        ):
            asyncio.run(auto_module.check_weather())

        assert auto_module._raining is False
        assert bulb.id not in auto_module._saved
        calls = [str(c) for c in mock_cmd.call_args_list]
        assert any("False" in c for c in calls)

    def test_skips_without_location(self, engine):
        with (
            patch("app.services.automations.is_raining", new=AsyncMock()) as mock_rain,
            patch.dict("os.environ", {"LAT": "0", "LON": "0"}),
        ):
            asyncio.run(auto_module.check_weather())
        mock_rain.assert_not_awaited()

    def test_no_double_activation(self, engine, bulb):
        auto_module._raining = True
        with (
            patch("app.devices.tuya.engine", engine),
            patch("app.services.automations.is_raining", new=AsyncMock(return_value=True)),
            patch("app.services.automations.tuya_client.send_command", new=AsyncMock()) as mock_cmd,
            patch.dict("os.environ", {"LAT": "36.44", "LON": "-5.27"}),
        ):
            asyncio.run(auto_module.check_weather())
        mock_cmd.assert_not_awaited()


class TestSunTriggers:
    def setup_method(self):
        from app.services.scheduler import scheduler
        for job in scheduler.get_jobs():
            job.remove()

    teardown_method = setup_method

    @pytest.fixture
    def bulb(self, session):
        d = Device(
            name="Test Bulb", device_id="dev_bulb_sun", local_key="key", ip_address="192.168.x.x",
            type=DeviceType.bulb, integration=Integration.tuya, protocol_version=3.5,
        )
        session.add(d)
        session.commit()
        session.refresh(d)
        return d

    def _make_auto(self, session, bulb, **overrides):
        defaults = dict(
            name="Sun rule", enabled=True, trigger_type=TriggerType.sun,
            trigger_sun_event="sunset", trigger_sun_offset=0,
            action_device_id=bulb.id, action_type="set_state_on",
        )
        defaults.update(overrides)
        auto = Automation(**defaults)
        session.add(auto)
        session.commit()
        session.refresh(auto)
        return auto

    def test_schedules_job_at_sunset(self, engine, session, bulb):
        from app.services.scheduler import scheduler
        auto = self._make_auto(session, bulb)
        future_sunset = datetime.now() + timedelta(hours=2)
        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.get_sun_times",
                  new=AsyncMock(return_value=(datetime.now() - timedelta(hours=6), future_sunset))),
            patch.dict("os.environ", {"LAT": "36.44", "LON": "-5.27"}),
        ):
            asyncio.run(auto_engine.refresh_sun_jobs())
        job = scheduler.get_job(f"auto_sun_{auto.id}")
        assert job is not None
        assert job.trigger.run_date.replace(tzinfo=None) == future_sunset

    def test_applies_negative_offset_before_sunrise(self, engine, session, bulb):
        from app.services.scheduler import scheduler
        auto = self._make_auto(session, bulb, trigger_sun_event="sunrise", trigger_sun_offset=-15)
        sunrise = datetime.now() + timedelta(hours=2)
        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.get_sun_times",
                  new=AsyncMock(return_value=(sunrise, sunrise + timedelta(hours=12)))),
            patch.dict("os.environ", {"LAT": "36.44", "LON": "-5.27"}),
        ):
            asyncio.run(auto_engine.refresh_sun_jobs())
        job = scheduler.get_job(f"auto_sun_{auto.id}")
        assert job.trigger.run_date.replace(tzinfo=None) == sunrise - timedelta(minutes=15)

    def test_skips_and_clears_job_without_location(self, engine, session, bulb):
        from app.services.scheduler import scheduler
        auto = self._make_auto(session, bulb)
        scheduler.add_job(
            auto_engine._fire_by_id, "date", run_date=datetime.now() + timedelta(hours=1),
            id=f"auto_sun_{auto.id}", args=[auto.id],
        )
        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.get_sun_times", new=AsyncMock()) as mock_sun,
            patch.dict("os.environ", {"LAT": "0", "LON": "0"}),
        ):
            asyncio.run(auto_engine.refresh_sun_jobs())
        mock_sun.assert_not_awaited()
        assert scheduler.get_job(f"auto_sun_{auto.id}") is None

    def test_removes_job_for_disabled_automation(self, engine, session, bulb):
        from app.services.scheduler import scheduler
        auto = self._make_auto(session, bulb, enabled=False)
        scheduler.add_job(
            auto_engine._fire_by_id, "date", run_date=datetime.now() + timedelta(hours=1),
            id=f"auto_sun_{auto.id}", args=[auto.id],
        )
        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.get_sun_times", new=AsyncMock()) as mock_sun,
        ):
            asyncio.run(auto_engine.refresh_sun_jobs())
        mock_sun.assert_not_awaited()
        assert scheduler.get_job(f"auto_sun_{auto.id}") is None

    def test_removes_job_when_time_already_passed_today(self, engine, session, bulb):
        from app.services.scheduler import scheduler
        auto = self._make_auto(session, bulb)
        past_sunset = datetime.now() - timedelta(minutes=5)
        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.get_sun_times",
                  new=AsyncMock(return_value=(datetime.now() - timedelta(hours=6), past_sunset))),
            patch.dict("os.environ", {"LAT": "36.44", "LON": "-5.27"}),
        ):
            asyncio.run(auto_engine.refresh_sun_jobs())
        assert scheduler.get_job(f"auto_sun_{auto.id}") is None

    def test_remove_automation_clears_sun_job(self, engine, session, bulb):
        from app.services.scheduler import scheduler
        auto = self._make_auto(session, bulb)
        scheduler.add_job(
            auto_engine._fire_by_id, "date", run_date=datetime.now() + timedelta(hours=1),
            id=f"auto_sun_{auto.id}", args=[auto.id],
        )
        auto_engine.remove_automation(auto.id)
        assert scheduler.get_job(f"auto_sun_{auto.id}") is None
