"""Unit tests for pure helper functions in tuya.py."""
import pytest

from unittest.mock import MagicMock, patch

from app.devices.models import Device, DeviceType, Integration
from app.devices.tuya import _command_to_dps, _dps_get, _hsv_hex_to_rgb_hex, _rgb_hex_to_hsv_hex, _send_command_sync


class TestDpsGet:
    def test_finds_string_key(self):
        assert _dps_get({"22": 500}, 22) == 500

    def test_finds_int_key(self):
        assert _dps_get({22: 500}, 22) == 500

    def test_returns_none_when_missing(self):
        assert _dps_get({"1": True}, 22) is None

    def test_returns_first_match(self):
        # key 20 exists; 22 does not — should return 20's value
        assert _dps_get({"20": True}, 20, 22) is True

    def test_skips_to_second_key(self):
        assert _dps_get({"22": 800}, 20, 22) == 800


class TestHsvHexToRgbHex:
    def test_red(self):
        # H=0 (red), S=1000, V=1000
        assert _hsv_hex_to_rgb_hex("000003e803e8") == "#ff0000"

    def test_green(self):
        # H=120, S=1000, V=1000
        assert _hsv_hex_to_rgb_hex("007803e803e8") == "#00ff00"

    def test_blue(self):
        # H=240, S=1000, V=1000
        assert _hsv_hex_to_rgb_hex("00f003e803e8") == "#0000ff"

    def test_white(self):
        # H=0, S=0, V=1000 → white
        assert _hsv_hex_to_rgb_hex("000000000000") is not None
        # any hue, S=0, V=1000 → grey/white family
        result = _hsv_hex_to_rgb_hex("000000003e8")  # malformed — skip; use valid
        # valid: H=0, S=0, V=1000 = 0x3e8
        assert _hsv_hex_to_rgb_hex("000000003e8".zfill(12)) == "#ffffff"

    def test_half_brightness_red(self):
        # H=0, S=1000, V=500 → dark red; round(0.5*255)=128=0x80
        result = _hsv_hex_to_rgb_hex("000003e801f4")
        assert result == "#800000"


class TestRgbHexToHsvHex:
    def test_red(self):
        assert _rgb_hex_to_hsv_hex("#ff0000") == "000003e803e8"

    def test_green(self):
        assert _rgb_hex_to_hsv_hex("#00ff00") == "007803e803e8"

    def test_blue(self):
        assert _rgb_hex_to_hsv_hex("#0000ff") == "00f003e803e8"


class TestRoundTrip:
    @pytest.mark.parametrize("rgb", ["#ff0000", "#00ff00", "#0000ff", "#ff8000", "#8000ff"])
    def test_rgb_round_trip(self, rgb):
        hsv = _rgb_hex_to_hsv_hex(rgb)
        assert len(hsv) == 12
        result = _hsv_hex_to_rgb_hex(hsv)
        assert result == rgb


def _dev(type_=DeviceType.bulb):
    return Device(name="Lamp", type=type_, integration=Integration.tuya, device_id="<device_id>",
                  ip_address="192.168.x.x", local_key="<local_key>", protocol_version="3.5")


class TestCommandToDps:
    def test_bulb_off_uses_dps_20(self):
        assert _command_to_dps(_dev(), {"state": False}) == {20: False}

    def test_plug_on_uses_dps_1(self):
        assert _command_to_dps(_dev(DeviceType.plug), {"state": True}) == {1: True}

    def test_plug_ignores_bulb_fields(self):
        assert _command_to_dps(_dev(DeviceType.plug), {"brightness": 50}) == {}

    def test_colour_temp_forces_white_mode(self):
        assert _command_to_dps(_dev(), {"color_temp": 30, "brightness": 50}) == {22: 500, 21: "white", 23: 300}

    def test_rgb_forces_colour_mode(self):
        assert _command_to_dps(_dev(), {"color_rgb": "#ff0000"}) == {21: "colour", 24: "000003e803e8"}

    def test_mode_only(self):
        assert _command_to_dps(_dev(), {"color_mode": "white"}) == {21: "white"}


class TestSendCommandSync:
    """The whole command goes out as one packet — each extra tinytuya call is
    a fresh connection + protocol-3.5 handshake (~1s on the Pi), and
    turn_on/turn_off add a hidden status() probe before sending."""

    def test_sends_single_packet_without_turn_off(self):
        fake = MagicMock()
        with patch("app.devices.tuya._make_device", return_value=fake):
            _send_command_sync(_dev(), {"state": False})
        fake.set_multiple_values.assert_called_once_with({20: False})
        fake.turn_off.assert_not_called()
        fake.set_value.assert_not_called()

    def test_colour_change_is_one_packet(self):
        fake = MagicMock()
        with patch("app.devices.tuya._make_device", return_value=fake):
            _send_command_sync(_dev(), {"state": True, "color_rgb": "#0000ff"})
        fake.set_multiple_values.assert_called_once_with({20: True, 21: "colour", 24: "00f003e803e8"})

    def test_empty_command_opens_no_connection(self):
        with patch("app.devices.tuya._make_device") as make:
            _send_command_sync(_dev(DeviceType.plug), {"brightness": 50})
        make.assert_not_called()
