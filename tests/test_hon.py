"""Tests for the hOn A/C state parsing and command logic."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.devices import hon


class _Param:
    """Stand-in for pyhOn's HonParameterRange/Fixed — accepts any value, like the real thing."""
    def __init__(self, value):
        self.value = value


class _EnumParam:
    """Stand-in for pyhOn's HonParameterEnum — validates against a list of *strings*
    and raises on anything else, exactly like the real pyhOn parameter does. Setting
    an int here (even one that's numerically an allowed option) must raise."""
    def __init__(self, value: str, allowed: list[str]):
        self._value = value
        self._allowed = allowed

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        if not isinstance(v, str) or v not in self._allowed:
            raise ValueError(f"Allowed values: {self._allowed} But was: {v!r}")
        self._value = v


class _FakeCommand:
    def __init__(self, parameters):
        self.parameters = parameters
        self.send = AsyncMock()


class _FakeAppliance:
    def __init__(self, unique_id, parameters, commands=None):
        self.unique_id = unique_id
        self.mac_address = unique_id
        self.attributes = {"parameters": parameters}
        self.commands = commands or {}
        self.update = AsyncMock()


@pytest.fixture(autouse=True)
def _reset_hon():
    yield
    hon._hon = None


def _set_appliances(*appliances):
    fake_hon = MagicMock()
    fake_hon.appliances = list(appliances)
    hon._hon = fake_hon


_FULL_PARAMS = {
    "onOffStatus": _Param("1"),
    "tempSel": _Param(26.0),
    "machMode": _Param(1),
    "windSpeed": _Param(5),
    "energySavingStatus": _Param(0),
    "muteStatus": _Param(1),
    "windDirectionVertical": _Param(5),
    "tempIndoor": _Param(27.0),
    "tempOutdoor": _Param(38.0),
}


class TestGetState:
    def test_parses_all_fields(self):
        _set_appliances(_FakeAppliance("ac-1", _FULL_PARAMS))
        state = asyncio.run(hon.get_state("ac-1"))
        assert state == {
            "online": True,
            "state": True,
            "temperature": 26,
            "ac_mode": "cool",
            "fan_speed": 5,
            "eco": False,
            "quiet": True,
            "louvre_position": 5,
            "indoor_temp": 27.0,
            "outdoor_temp": 38.0,
        }

    def test_forces_a_real_refresh_not_the_optimistic_cache(self):
        # sync_command_to_params overwrites the local attribute cache with
        # whatever a command *intended* to send, before the network call even
        # happens — get_state must force a real re-fetch, not trust that cache.
        appliance = _FakeAppliance("ac-1", _FULL_PARAMS)
        _set_appliances(appliance)
        asyncio.run(hon.get_state("ac-1"))
        appliance.update.assert_awaited_once_with(force=True)

    def test_unknown_appliance_returns_offline_fallback(self):
        _set_appliances()
        state = asyncio.run(hon.get_state("missing"))
        assert state["online"] is False

    def test_missing_temp_readings_are_none(self):
        params = dict(_FULL_PARAMS)
        del params["tempIndoor"]
        del params["tempOutdoor"]
        _set_appliances(_FakeAppliance("ac-1", params))
        state = asyncio.run(hon.get_state("ac-1"))
        assert state["indoor_temp"] is None
        assert state["outdoor_temp"] is None


def _make_start_stop_appliance():
    """An appliance whose startProgram/stopProgram commands use realistic enum
    validation for machMode/windSpeed/windDirectionVertical, matching the live
    unit's actual reported allowed values."""
    def build_cmd():
        return _FakeCommand({
            "tempSel": _Param(26),
            "machMode": _EnumParam("1", ["0", "1", "2", "4", "6"]),
            "windSpeed": _EnumParam("5", ["1", "2", "3", "5"]),
            "energySavingStatus": _Param("1"),
            "muteStatus": _Param(0),
            "windDirectionVertical": _EnumParam("5", ["2", "4", "5", "6", "8"]),
        })
    start_cmd = build_cmd()
    stop_cmd = build_cmd()
    appliance = _FakeAppliance("ac-1", _FULL_PARAMS, commands={
        "startProgram": start_cmd, "stopProgram": stop_cmd,
    })
    return appliance, start_cmd, stop_cmd


class TestSendCommand:
    def test_ac_mode_is_sent_as_string(self):
        appliance, start_cmd, _ = _make_start_stop_appliance()
        _set_appliances(appliance)
        asyncio.run(hon.send_command("ac-1", {"ac_mode": "cool"}))
        assert start_cmd.parameters["machMode"].value == "1"
        start_cmd.send.assert_awaited_once()

    def test_fan_speed_is_sent_as_string(self):
        appliance, start_cmd, _ = _make_start_stop_appliance()
        _set_appliances(appliance)
        asyncio.run(hon.send_command("ac-1", {"fan_speed": 3}))
        assert start_cmd.parameters["windSpeed"].value == "3"
        start_cmd.send.assert_awaited_once()

    def test_louvre_position_is_sent_as_string(self):
        appliance, start_cmd, _ = _make_start_stop_appliance()
        _set_appliances(appliance)
        asyncio.run(hon.send_command("ac-1", {"louvre_position": 8}))
        assert start_cmd.parameters["windDirectionVertical"].value == "8"
        start_cmd.send.assert_awaited_once()

    def test_sets_eco_and_quiet(self):
        appliance, start_cmd, _ = _make_start_stop_appliance()
        _set_appliances(appliance)
        asyncio.run(hon.send_command("ac-1", {"eco": True, "quiet": False}))
        assert start_cmd.parameters["energySavingStatus"].value == 1
        assert start_cmd.parameters["muteStatus"].value == 0

    def test_turning_off_uses_stop_program(self):
        appliance, start_cmd, stop_cmd = _make_start_stop_appliance()
        _set_appliances(appliance)
        asyncio.run(hon.send_command("ac-1", {"state": False}))
        stop_cmd.send.assert_awaited_once()
        start_cmd.send.assert_not_awaited()

    def test_turning_on_uses_start_program(self):
        appliance, start_cmd, stop_cmd = _make_start_stop_appliance()
        _set_appliances(appliance)
        asyncio.run(hon.send_command("ac-1", {"state": True}))
        start_cmd.send.assert_awaited_once()
        stop_cmd.send.assert_not_awaited()

    def test_plain_adjustment_without_state_uses_start_program(self):
        appliance, start_cmd, stop_cmd = _make_start_stop_appliance()
        _set_appliances(appliance)
        asyncio.run(hon.send_command("ac-1", {"temperature": 20}))
        start_cmd.send.assert_awaited_once()
        stop_cmd.send.assert_not_awaited()

    def test_unknown_appliance_is_a_noop(self):
        _set_appliances()
        asyncio.run(hon.send_command("missing", {"eco": True}))  # should not raise
