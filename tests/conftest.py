from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.devices.models import Automation, Device, DeviceType, Integration, Schedule  # noqa: F401 — all models imported so SQLModel.metadata.create_all creates their tables


@pytest.fixture(name="engine")
def engine_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture(name="tuya_bulb")
def tuya_bulb_fixture(session):
    device = Device(
        name="Test Bulb",
        device_id="dev_bulb_001",
        local_key="secretkey",
        ip_address="192.168.x.x",
        type=DeviceType.bulb,
        integration=Integration.tuya,
        protocol_version=3.5,
        online=True,
        state=True,
        brightness=50,
        color_temp=50,
        color_mode="white",
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="z2m_plug")
def z2m_plug_fixture(session):
    device = Device(
        name="Living Room Socket",
        device_id="living_room_socket",
        type=DeviceType.plug,
        integration=Integration.zigbee2mqtt,
        online=True,
        state=False,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="z2m_sensor")
def z2m_sensor_fixture(session):
    device = Device(
        name="Bedroom Sensor",
        device_id="bedroom_sensor",
        type=DeviceType.sensor,
        integration=Integration.zigbee2mqtt,
        online=True,
        sensor_temperature=21.5,
        humidity=55.2,
        battery=86,
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="firetv_device")
def firetv_device_fixture(session):
    device = Device(
        name="Fire TV",
        device_id="192.168.x.x",
        type=DeviceType.tv,
        integration=Integration.firetv,
        online=True,
        media_state="paused",
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


@pytest.fixture(name="client")
def client_fixture(engine):
    from app.db import get_session
    from app.main import app

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    with (
        patch("app.main.init_db"),
        patch("app.main.init_schedules"),
        patch("app.main.load_time_automations"),
        patch("app.devices.hon.start", new=AsyncMock()),
        patch("app.devices.hon.stop", new=AsyncMock()),
        patch("app.devices.mqtt.run", new=AsyncMock()),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.clear()
