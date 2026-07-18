"""Unit tests for pure helper functions in zigbee_color.py."""
import pytest

from app.devices.zigbee_color import (
    hs_to_rgb_hex,
    mireds_to_pct,
    pct_to_mireds,
    rgb_hex_to_hs_brightness,
)


class TestPctToMireds:
    def test_warmest(self):
        assert pct_to_mireds(0) == 556

    def test_coolest(self):
        assert pct_to_mireds(100) == 153

    def test_midpoint(self):
        assert pct_to_mireds(50) == round(556 - 0.5 * (556 - 153))

    def test_clamps_below_range(self):
        assert pct_to_mireds(-10) == pct_to_mireds(0)

    def test_clamps_above_range(self):
        assert pct_to_mireds(150) == pct_to_mireds(100)


class TestMiredsToPct:
    def test_warmest(self):
        assert mireds_to_pct(556) == 0

    def test_coolest(self):
        assert mireds_to_pct(153) == 100

    def test_clamps_below_range(self):
        assert mireds_to_pct(100) == mireds_to_pct(153)

    def test_clamps_above_range(self):
        assert mireds_to_pct(600) == mireds_to_pct(556)


class TestRgbHexToHsBrightness:
    def test_red(self):
        hue, saturation, brightness = rgb_hex_to_hs_brightness("#ff0000")
        assert hue == 0
        assert saturation == 100
        assert brightness == 254

    def test_green(self):
        hue, saturation, brightness = rgb_hex_to_hs_brightness("#00ff00")
        assert hue == 120
        assert saturation == 100
        assert brightness == 254

    def test_half_brightness_red(self):
        hue, saturation, brightness = rgb_hex_to_hs_brightness("#800000")
        assert hue == 0
        assert saturation == 100
        assert brightness == round(128 / 255 * 254)


class TestHsToRgbHex:
    def test_red(self):
        assert hs_to_rgb_hex(0, 100, 254) == "#ff0000"

    def test_green(self):
        assert hs_to_rgb_hex(120, 100, 254) == "#00ff00"

    def test_blue(self):
        assert hs_to_rgb_hex(240, 100, 254) == "#0000ff"


class TestRoundTrip:
    @pytest.mark.parametrize("rgb", ["#ff0000", "#00ff00", "#0000ff", "#ff8000"])
    def test_rgb_round_trip(self, rgb):
        hue, saturation, brightness = rgb_hex_to_hs_brightness(rgb)
        assert hs_to_rgb_hex(hue, saturation, brightness) == rgb
