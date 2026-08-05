from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from sqlmodel import select

from app.db import SessionDep
from app.devices.models import AcSample, ClimateSample, Device, DeviceType

router = APIRouter(prefix="/climate", tags=["climate"])


def _bucket_start(ts: datetime, bucket_seconds: int, epoch: datetime) -> datetime:
    elapsed = (ts - epoch).total_seconds()
    return epoch + timedelta(seconds=int(elapsed // bucket_seconds) * bucket_seconds)


@router.get("/data")
async def climate_data(session: SessionDep, hours: int = Query(default=6, ge=1, le=168)):
    """Temperature + humidity series for every climate sensor's room, plus the
    A/C's own indoor/outdoor temperature, bucketed and averaged so multiple
    sensors sharing a room collapse into one line per room instead of
    overlapping raw noise. The A/C doesn't report humidity, so its series
    just carry nulls there."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    bucket_seconds = max(60, hours * 3600 // 150)

    # buckets[label][bucket_time][metric] -> list of readings to average
    buckets: dict[str, dict[datetime, dict[str, list[float]]]] = {}

    def _add(label: str, ts: datetime, metric: str, value: float | None) -> None:
        if value is None:
            return
        bucket = _bucket_start(ts, bucket_seconds, cutoff)
        buckets.setdefault(label, {}).setdefault(bucket, {}).setdefault(metric, []).append(value)

    sensors = session.exec(select(Device).where(Device.type == DeviceType.sensor)).all()
    room_by_id = {d.id: (d.room or d.name) for d in sensors}
    if room_by_id:
        samples = session.exec(
            select(ClimateSample).where(
                ClimateSample.device_id.in_(room_by_id.keys()),
                ClimateSample.timestamp >= cutoff,
            )
        ).all()
        for s in samples:
            room = room_by_id[s.device_id]
            _add(room, s.timestamp, "temperature", s.temperature)
            _add(room, s.timestamp, "humidity", s.humidity)

    ac_ids = [d.id for d in session.exec(select(Device).where(Device.type == DeviceType.ac)).all()]
    if ac_ids:
        ac_samples = session.exec(
            select(AcSample).where(AcSample.device_id.in_(ac_ids), AcSample.timestamp >= cutoff)
        ).all()
        for s in ac_samples:
            _add("AC Indoor", s.timestamp, "temperature", s.indoor_temp)
            _add("AC Outdoor", s.timestamp, "temperature", s.outdoor_temp)

    def _series(points: dict[datetime, dict[str, list[float]]], metric: str, order: list[datetime]) -> list[float | None]:
        return [
            round(sum(points[b][metric]) / len(points[b][metric]), 2) if metric in points[b] else None
            for b in order
        ]

    result = {}
    for label, points in buckets.items():
        order = sorted(points)
        result[label] = {
            "timestamps": [b.isoformat() for b in order],
            "temperature": _series(points, "temperature", order),
            "humidity": _series(points, "humidity", order),
        }
    return result
