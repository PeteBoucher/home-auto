from sqlmodel import Session, select

from app.db import engine
from app.devices.models import Device, DeviceType
from app.devices import mqtt as mqtt_client


async def _publish_external_reading(target: Device, temperature: float | None, humidity: float | None) -> None:
    payload: dict = {}
    if temperature is not None:
        payload["external_temperature"] = temperature
    if humidity is not None:
        payload["external_humidity"] = humidity
    if payload:
        await mqtt_client.publish(f"{mqtt_client.PREFIX}/{target.device_id}/set", payload)


def _lowest_outdoor_sensor(session: Session) -> Device | None:
    """The online climate_combine_outdoor sensor with the lowest current reading —
    the live counterpart of the climate widget's "Outdoor (combined)" line (direct
    sun only ever inflates a reading, so the lowest is the closest to ambient).
    Offline sensors are skipped so a dead one can't pin the display to its last,
    increasingly stale reading."""
    candidates = [
        d for d in session.exec(
            select(Device).where(Device.type == DeviceType.sensor, Device.climate_combine_outdoor == True)  # noqa: E712
        ).all()
        if d.online and d.sensor_temperature is not None
    ]
    return min(candidates, key=lambda d: d.sensor_temperature, default=None)


async def sync_display_targets(source_device_id: int) -> None:
    """Whenever a sensor's own reading updates, push it to any other sensor
    configured to show this one in its secondary on-screen field (Z2M's
    external_temperature/external_humidity datapoints, on models that support
    them — e.g. the SNZB-02DR2, which shows it as a smaller "EXT1" readout
    alongside, not replacing, its own reading). If the source is one of the
    combined-outdoor sensors, also re-evaluate which of them is lowest for any
    sensor showing "Outdoor (combined)"."""
    with Session(engine) as session:
        source = session.get(Device, source_device_id)
        if not source:
            return
        targets = list(session.exec(select(Device).where(Device.display_source_id == source_device_id)).all())
        combined_targets: list[Device] = []
        lowest = None
        if source.climate_combine_outdoor:
            combined_targets = list(session.exec(select(Device).where(Device.display_outdoor_combined == True)).all())  # noqa: E712
            lowest = _lowest_outdoor_sensor(session)
    for target in targets:
        await _publish_external_reading(target, source.sensor_temperature, source.humidity)
    if lowest:
        for target in combined_targets:
            await _publish_external_reading(target, lowest.sensor_temperature, lowest.humidity)


async def set_display_source(session: Session, target: Device, source_id: int | None, outdoor_combined: bool = False) -> None:
    """Link/unlink `target`'s secondary on-screen field to `source_id`, or to the
    lowest of the combined-outdoor sensors when `outdoor_combined` is set. Setting
    a source enables the field (temperature_sensor_select=external) and pushes
    the current reading; clearing both disables the field again
    (temperature_sensor_select=internal). The device's own reading stays
    primary either way."""
    target.display_source_id = None if outdoor_combined else source_id
    target.display_outdoor_combined = outdoor_combined
    session.add(target)
    session.commit()
    if source_id is None and not outdoor_combined:
        await mqtt_client.publish(f"{mqtt_client.PREFIX}/{target.device_id}/set", {"temperature_sensor_select": "internal"})
        return
    await mqtt_client.publish(f"{mqtt_client.PREFIX}/{target.device_id}/set", {"temperature_sensor_select": "external"})
    source = _lowest_outdoor_sensor(session) if outdoor_combined else session.get(Device, source_id)
    if source:
        await _publish_external_reading(target, source.sensor_temperature, source.humidity)
