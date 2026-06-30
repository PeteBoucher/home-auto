"""Tests for MQTT state application logic."""
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from app.devices.models import Device, DeviceType, Integration
from app.devices.mqtt import _apply_state


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
