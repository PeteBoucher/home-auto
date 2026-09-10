from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device
from app.devices import mqtt as mqtt_client


async def _publish_external_reading(target: Device, temperature: float | None, humidity: float | None) -> None:
    payload: dict = {}
    if temperature is not None:
        payload["external_temperature"] = temperature
    if humidity is not None:
        payload["external_humidity"] = humidity
    if payload:
        await mqtt_client.publish(f"{mqtt_client.PREFIX}/{target.device_id}/set", payload)


async def sync_display_targets(source_device_id: int) -> None:
    """Whenever a sensor's own reading updates, push it to any other sensor
    configured to show this one in its secondary on-screen field (Z2M's
    external_temperature/external_humidity datapoints, on models that support
    them — e.g. the SNZB-02DR2, which shows it as a smaller "EXT1" readout
    alongside, not replacing, its own reading)."""
    with Session(engine) as session:
        source = session.get(Device, source_device_id)
        if not source:
            return
        targets = list(session.exec(select(Device).where(Device.display_source_id == source_device_id)).all())
    for target in targets:
        await _publish_external_reading(target, source.sensor_temperature, source.humidity)


async def set_display_source(session: Session, target: Device, source_id: int | None) -> None:
    """Link/unlink `target`'s secondary on-screen field to `source_id`. Setting
    a source enables the field (temperature_sensor_select=external) and pushes
    the source's current reading; clearing it disables the field again
    (temperature_sensor_select=internal). The device's own reading stays
    primary either way."""
    target.display_source_id = source_id
    session.add(target)
    session.commit()
    if source_id is None:
        await mqtt_client.publish(f"{mqtt_client.PREFIX}/{target.device_id}/set", {"temperature_sensor_select": "internal"})
        return
    await mqtt_client.publish(f"{mqtt_client.PREFIX}/{target.device_id}/set", {"temperature_sensor_select": "external"})
    source = session.get(Device, source_id)
    if source:
        await _publish_external_reading(target, source.sensor_temperature, source.humidity)
