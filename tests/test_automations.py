"""Tests for weather service and rain automation."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.weather import get_sun_times, is_raining
import app.services.automations as auto_module
import app.services.automation_engine as auto_engine
from app.devices.models import Automation, Device, DeviceType, Integration, TriggerType


class TestGetSunTimes:
    def test_returns_parsed_open_meteo_times(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"daily": {
            "sunrise": ["2026-07-27T07:12"],
            "sunset": ["2026-07-27T21:03"],
        }}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("app.services.weather.httpx.AsyncClient", return_value=mock_client):
            sunrise, sunset = asyncio.run(get_sun_times(36.44, -5.27))
        assert sunrise == datetime(2026, 7, 27, 7, 12)
        assert sunset == datetime(2026, 7, 27, 21, 3)

    def test_falls_back_to_offline_calculation_when_unreachable(self):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=OSError("Temporary failure in name resolution"))

        with patch("app.services.weather.httpx.AsyncClient", return_value=mock_client):
            sunrise, sunset = asyncio.run(get_sun_times(36.44, -5.27))
        # No network involved — just sanity-check it's a real (naive) sunrise/sunset pair
        assert sunrise.tzinfo is None
        assert sunset.tzinfo is None
        assert sunrise < sunset

    def test_offline_fallback_used_on_http_error_status(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("app.services.weather.httpx.AsyncClient", return_value=mock_client), \
             patch("app.services.weather._offline_sun_times", return_value=(datetime(2026, 7, 27, 7, 0), datetime(2026, 7, 27, 21, 0))) as mock_offline:
            result = asyncio.run(get_sun_times(36.44, -5.27))
        mock_offline.assert_called_once_with(36.44, -5.27)
        assert result == (datetime(2026, 7, 27, 7, 0), datetime(2026, 7, 27, 21, 0))


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


class TestCheckStateTriggersDuringRedAlert:
    @pytest.fixture
    def trigger_device(self, session):
        d = Device(
            name="Living Room Socket",
            device_id="living_room_socket",
            type=DeviceType.plug,
            integration=Integration.zigbee2mqtt,
            online=True,
            state=False,
        )
        session.add(d)
        session.commit()
        session.refresh(d)
        return d

    @pytest.fixture
    def action_device(self, session):
        d = Device(
            name="Dining Room Uplighter",
            device_id="dining_room_uplighter",
            type=DeviceType.bulb,
            integration=Integration.zigbee2mqtt,
            online=True,
            state=False,
        )
        session.add(d)
        session.commit()
        session.refresh(d)
        return d

    @pytest.fixture
    def sync_automation(self, session, trigger_device, action_device):
        a = Automation(
            name="Sync on",
            enabled=True,
            trigger_type=TriggerType.device_state,
            trigger_device_id=trigger_device.id,
            trigger_field="state",
            trigger_operator="eq",
            trigger_value="true",
            action_device_id=action_device.id,
            action_type="set_state_on",
        )
        session.add(a)
        session.commit()
        session.refresh(a)
        return a

    def test_fires_normally_when_alert_inactive(self, engine, trigger_device, sync_automation):
        auto_engine._last_eval.clear()
        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.red_alert.is_active", return_value=False),
            patch("app.services.automation_engine.mqtt_client.publish", new=AsyncMock()) as mock_pub,
        ):
            asyncio.run(auto_engine.check_state_triggers(trigger_device.id, {"state": True}))
        mock_pub.assert_awaited_once()

    def test_suppressed_while_alert_active(self, engine, trigger_device, sync_automation):
        auto_engine._last_eval.clear()
        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.red_alert.is_active", return_value=True),
            patch("app.services.automation_engine.mqtt_client.publish", new=AsyncMock()) as mock_pub,
        ):
            asyncio.run(auto_engine.check_state_triggers(trigger_device.id, {"state": True}))
        mock_pub.assert_not_awaited()


class TestWithinTrigger:
    def test_eval_condition_true_when_within_tolerance(self):
        state = {"outdoor_temp": 24.0, "temperature": 22}
        assert auto_engine._eval_condition("outdoor_temp", "within", "2", state, "temperature") is True

    def test_eval_condition_false_when_outside_tolerance(self):
        state = {"outdoor_temp": 30.0, "temperature": 22}
        assert auto_engine._eval_condition("outdoor_temp", "within", "2", state, "temperature") is False

    def test_eval_condition_true_at_exact_boundary(self):
        state = {"outdoor_temp": 24.0, "temperature": 22}
        assert auto_engine._eval_condition("outdoor_temp", "within", "2.0", state, "temperature") is True

    def test_eval_condition_false_without_compare_field(self):
        state = {"outdoor_temp": 23.0, "temperature": 22}
        assert auto_engine._eval_condition("outdoor_temp", "within", "2", state, None) is False

    def test_eval_condition_false_when_compare_field_missing_from_state(self):
        state = {"outdoor_temp": 23.0}
        assert auto_engine._eval_condition("outdoor_temp", "within", "2", state, "temperature") is False

    def test_fire_sends_command_to_hon_device(self):
        from app.devices.models import DeviceType

        auto = Automation(
            id=1, name="Cool enough, shut off", enabled=True,
            trigger_type=TriggerType.device_state, action_device_id=1, action_type="set_state_off",
        )
        device = Device(
            id=1, name="Living Room A/C", device_id="ac-unit-1",
            type=DeviceType.ac, integration=Integration.hon,
        )
        with (
            patch("app.services.automation_engine.Session") as mock_session_cls,
            patch("app.services.automation_engine.hon_client.send_command", new=AsyncMock()) as mock_send,
        ):
            mock_session_cls.return_value.__enter__.return_value.get.return_value = device
            asyncio.run(auto_engine._fire(auto))
        mock_send.assert_awaited_once_with("ac-unit-1", {"state": False})

    def test_end_to_end_fires_once_on_rising_edge(self, engine, session):
        from app.devices.models import DeviceType

        auto_engine._last_eval.clear()
        ac = Device(
            name="Living Room A/C", device_id="ac-unit-1",
            type=DeviceType.ac, integration=Integration.hon, online=True, state=True, temperature=22,
        )
        session.add(ac)
        session.commit()
        session.refresh(ac)
        rule = Automation(
            name="Cool enough, shut off", enabled=True,
            trigger_type=TriggerType.device_state, trigger_device_id=ac.id,
            trigger_field="outdoor_temp", trigger_operator="within", trigger_value="2",
            trigger_compare_field="temperature",
            action_device_id=ac.id, action_type="set_state_off",
        )
        session.add(rule)
        session.commit()

        far_state = {"outdoor_temp": 30, "temperature": 22, "state": True}
        near_state = {"outdoor_temp": 23, "temperature": 22, "state": True}

        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.red_alert.is_active", return_value=False),
            patch("app.services.automation_engine.hon_client.send_command", new=AsyncMock()) as mock_send,
        ):
            asyncio.run(auto_engine.check_state_triggers(ac.id, far_state))
            mock_send.assert_not_awaited()

            asyncio.run(auto_engine.check_state_triggers(ac.id, near_state))
            mock_send.assert_awaited_once_with("ac-unit-1", {"state": False})

            # still within tolerance on the next poll — edge-triggered, must not refire
            asyncio.run(auto_engine.check_state_triggers(ac.id, near_state))
            mock_send.assert_awaited_once()


class TestTimeWindow:
    def test_no_window_always_true(self):
        assert auto_engine._within_time_window(None, None) is True

    def test_only_start_set_ignored(self):
        assert auto_engine._within_time_window("18:00", None) is True

    def test_within_same_day_window(self):
        now = datetime(2026, 8, 13, 19, 0)
        assert auto_engine._within_time_window("18:00", "23:00", now) is True

    def test_outside_same_day_window(self):
        now = datetime(2026, 8, 13, 8, 0)
        assert auto_engine._within_time_window("18:00", "23:00", now) is False

    def test_at_window_start_boundary_is_inside(self):
        now = datetime(2026, 8, 13, 18, 0)
        assert auto_engine._within_time_window("18:00", "23:00", now) is True

    def test_at_window_end_boundary_is_outside(self):
        now = datetime(2026, 8, 13, 23, 0)
        assert auto_engine._within_time_window("18:00", "23:00", now) is False

    def test_overnight_window_late_night_is_inside(self):
        now = datetime(2026, 8, 13, 23, 30)
        assert auto_engine._within_time_window("22:00", "06:00", now) is True

    def test_overnight_window_early_morning_is_inside(self):
        now = datetime(2026, 8, 13, 5, 0)
        assert auto_engine._within_time_window("22:00", "06:00", now) is True

    def test_overnight_window_midday_is_outside(self):
        now = datetime(2026, 8, 13, 12, 0)
        assert auto_engine._within_time_window("22:00", "06:00", now) is False

    def test_end_to_end_window_blocks_outside_and_allows_inside(self, engine, session):
        from app.devices.models import DeviceType

        auto_engine._last_eval.clear()
        ac = Device(
            name="Living Room A/C", device_id="ac-unit-1",
            type=DeviceType.ac, integration=Integration.hon, online=True, state=True, temperature=22,
        )
        session.add(ac)
        session.commit()
        session.refresh(ac)
        rule = Automation(
            name="Cool enough in the evening, shut off", enabled=True,
            trigger_type=TriggerType.device_state, trigger_device_id=ac.id,
            trigger_field="outdoor_temp", trigger_operator="within", trigger_value="2",
            trigger_compare_field="temperature",
            trigger_window_start="18:00", trigger_window_end="23:00",
            action_device_id=ac.id, action_type="set_state_off",
        )
        session.add(rule)
        session.commit()

        near_state = {"outdoor_temp": 23, "temperature": 22, "state": True}
        morning = datetime(2026, 8, 13, 8, 0)
        evening = datetime(2026, 8, 13, 19, 0)

        with (
            patch("app.services.automation_engine.engine", engine),
            patch("app.services.automation_engine.red_alert.is_active", return_value=False),
            patch("app.services.automation_engine.hon_client.send_command", new=AsyncMock()) as mock_send,
        ):
            with patch("app.services.automation_engine.datetime") as mock_dt:
                mock_dt.now.return_value = morning
                asyncio.run(auto_engine.check_state_triggers(ac.id, near_state))
            mock_send.assert_not_awaited()

            with patch("app.services.automation_engine.datetime") as mock_dt:
                mock_dt.now.return_value = evening
                asyncio.run(auto_engine.check_state_triggers(ac.id, near_state))
            mock_send.assert_awaited_once_with("ac-unit-1", {"state": False})


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

    def test_reschedules_for_tomorrow_when_time_already_passed_today(self, engine, session, bulb):
        # refresh_sun_jobs runs once/day at whatever time the service last
        # restarted, not a fixed pre-dawn time — so "today's" event having
        # already passed is the normal case, not a reason to drop the job.
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
        job = scheduler.get_job(f"auto_sun_{auto.id}")
        assert job is not None
        assert job.trigger.run_date.replace(tzinfo=None) == past_sunset + timedelta(days=1)

    def test_remove_automation_clears_sun_job(self, engine, session, bulb):
        from app.services.scheduler import scheduler
        auto = self._make_auto(session, bulb)
        scheduler.add_job(
            auto_engine._fire_by_id, "date", run_date=datetime.now() + timedelta(hours=1),
            id=f"auto_sun_{auto.id}", args=[auto.id],
        )
        auto_engine.remove_automation(auto.id)
        assert scheduler.get_job(f"auto_sun_{auto.id}") is None
