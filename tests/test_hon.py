"""Tests for the hOn A/C state parsing and command logic."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.devices import hon


class _Param:
    """Stand-in for pyhOn's typed command parameter objects."""
    def __init__(self, value):
        self.value = value


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


@pytest.fixture(autouse=True)
def _reset_hon():
    yield
    hon._hon = None


def _set_appliances(*appliances):
    fake_hon = MagicMock()
    fake_hon.appliances = list(appliances)
    hon._hon = fake_hon


_FULL_PARAMS = {
    "onOffStatus": _Param(1),
    "tempSel": _Param(26.0),
    "machMode": _Param(1),
    "windSpeed": _Param(5),
    "energySavingStatus": _Param(0),
    "muteStatus": _Param(1),
    "windDirectionVertical": _Param(5),
    "tempIndoor": _Param(27.0),
    "tempOutdoor": _Param(38.0),
    "totalElectricityUsed": _Param(12.5),
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
            "ac_energy": 12.5,
        }

    def test_unknown_appliance_returns_offline_fallback(self):
        _set_appliances()
        state = asyncio.run(hon.get_state("missing"))
        assert state["online"] is False

    def test_missing_temp_readings_are_none(self):
        params = dict(_FULL_PARAMS)
        del params["tempIndoor"]
        del params["tempOutdoor"]
        del params["totalElectricityUsed"]
        _set_appliances(_FakeAppliance("ac-1", params))
        state = asyncio.run(hon.get_state("ac-1"))
        assert state["indoor_temp"] is None
        assert state["outdoor_temp"] is None
        assert state["ac_energy"] is None


class TestSendCommand:
    def test_sets_eco_quiet_and_louvre(self):
        cmd = _FakeCommand({
            "onOffStatus": _Param(1),
            "tempSel": _Param(26),
            "machMode": _Param(1),
            "windSpeed": _Param(5),
            "energySavingStatus": _Param(0),
            "muteStatus": _Param(0),
            "windDirectionVertical": _Param(5),
        })
        appliance = _FakeAppliance("ac-1", _FULL_PARAMS, commands={"startProgram": cmd})
        _set_appliances(appliance)

        asyncio.run(hon.send_command("ac-1", {"eco": True, "quiet": False, "louvre_position": 8}))

        assert cmd.parameters["energySavingStatus"].value == 1
        assert cmd.parameters["muteStatus"].value == 0
        assert cmd.parameters["windDirectionVertical"].value == 8
        cmd.send.assert_awaited_once()

    def test_unknown_appliance_is_a_noop(self):
        _set_appliances()
        asyncio.run(hon.send_command("missing", {"eco": True}))  # should not raise
