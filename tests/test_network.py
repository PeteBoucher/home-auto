from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.network import _icon, zigbee_devices


# ---------------------------------------------------------------------------
# _icon() — pure function
# ---------------------------------------------------------------------------

class TestIcon:
    def test_gateway(self):
        assert _icon("anything", None, is_gateway=True, is_self=False) == "router-fill"

    def test_self(self):
        assert _icon("homeauto", None, is_gateway=False, is_self=True) == "cpu-fill"

    def test_ha_type_plug(self):
        assert _icon("myplug", "plug", is_gateway=False, is_self=False) == "plug-fill"

    def test_ha_type_bulb(self):
        assert _icon("mybulb", "bulb", is_gateway=False, is_self=False) == "lightbulb-fill"

    def test_ha_type_tv(self):
        assert _icon("firetv", "tv", is_gateway=False, is_self=False) == "tv-fill"

    def test_hostname_iphone(self):
        assert _icon("Pete-iPhone-11", None, is_gateway=False, is_self=False) == "phone-fill"

    def test_hostname_samsung(self):
        assert _icon("Samsung-Galaxy-S21", None, is_gateway=False, is_self=False) == "phone-fill"

    def test_hostname_pixel(self):
        assert _icon("pixel-7a", None, is_gateway=False, is_self=False) == "phone-fill"

    def test_hostname_oneplus(self):
        assert _icon("OnePlus-9", None, is_gateway=False, is_self=False) == "phone-fill"

    def test_hostname_ipad(self):
        assert _icon("iPad-3", None, is_gateway=False, is_self=False) == "tablet-fill"

    def test_hostname_macbook(self):
        assert _icon("MacBook-Pro-2", None, is_gateway=False, is_self=False) == "laptop-fill"

    def test_hostname_laptop(self):
        assert _icon("LAPTOP-ABC123", None, is_gateway=False, is_self=False) == "laptop-fill"

    def test_hostname_mac(self):
        assert _icon("Mac", None, is_gateway=False, is_self=False) == "display-fill"

    def test_hostname_windows_desktop(self):
        assert _icon("DESKTOP-XYZ123", None, is_gateway=False, is_self=False) == "display-fill"

    def test_hostname_firestick(self):
        assert _icon("firestick-abc123", None, is_gateway=False, is_self=False) == "tv-fill"

    def test_hostname_brother_printer(self):
        assert _icon("BRWDC567B3A9212", None, is_gateway=False, is_self=False) == "printer-fill"

    def test_hostname_extender(self):
        assert _icon("TL-WA860RE", None, is_gateway=False, is_self=False) == "router-fill"

    def test_hostname_unknown(self):
        assert _icon("unknown", None, is_gateway=False, is_self=False) == "pc-display"

    def test_gateway_takes_priority_over_ha_type(self):
        assert _icon("router", "plug", is_gateway=True, is_self=False) == "router-fill"

    def test_self_takes_priority_over_hostname(self):
        assert _icon("iPhone", None, is_gateway=False, is_self=True) == "cpu-fill"


# ---------------------------------------------------------------------------
# Routes — smoke tests with scan() mocked
# ---------------------------------------------------------------------------

_FAKE_DEVICES = [
    {"ip": "192.168.x.x", "mac": "aa:bb:cc:dd:ee:ff", "hostname": "router",
     "is_gateway": True, "is_self": False, "ha_name": None, "ha_type": None, "icon": "router-fill"},
    {"ip": "192.168.x.x", "mac": "11:22:33:44:55:66", "hostname": "homeauto",
     "is_gateway": False, "is_self": True, "ha_name": None, "ha_type": None, "icon": "cpu-fill"},
]

_FAKE_ZIGBEE = [
    {"name": "Living Room Lamp", "address": "0xaabbccddeeff0011", "type": "bulb",
     "online": True, "icon": "lightbulb-fill"},
]


class TestNetworkRoutes:
    def test_network_page_returns_200(self, client):
        with patch("app.api.network.scan", new=AsyncMock(return_value=_FAKE_DEVICES)), \
             patch("app.api.network.zigbee_devices", return_value=_FAKE_ZIGBEE):
            resp = client.get("/network")
        assert resp.status_code == 200
        assert "Network Map" in resp.text

    def test_network_page_shows_devices(self, client):
        with patch("app.api.network.scan", new=AsyncMock(return_value=_FAKE_DEVICES)), \
             patch("app.api.network.zigbee_devices", return_value=_FAKE_ZIGBEE):
            resp = client.get("/network")
        assert "homeauto" in resp.text
        assert "Living Room Lamp" in resp.text

    def test_network_page_shows_zigbee_section(self, client):
        with patch("app.api.network.scan", new=AsyncMock(return_value=_FAKE_DEVICES)), \
             patch("app.api.network.zigbee_devices", return_value=_FAKE_ZIGBEE):
            resp = client.get("/network")
        assert "Zigbee mesh" in resp.text

    def test_network_scan_partial_returns_200(self, client):
        with patch("app.api.network.scan", new=AsyncMock(return_value=_FAKE_DEVICES)), \
             patch("app.api.network.zigbee_devices", return_value=_FAKE_ZIGBEE):
            resp = client.get("/network/scan")
        assert resp.status_code == 200

    def test_network_page_empty_scan(self, client):
        with patch("app.api.network.scan", new=AsyncMock(return_value=[])), \
             patch("app.api.network.zigbee_devices", return_value=[]):
            resp = client.get("/network")
        assert resp.status_code == 200
        assert "No devices found" in resp.text
