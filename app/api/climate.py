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
    """Temperature series for every climate sensor's room, plus the A/C's own
    indoor/outdoor readings, bucketed and averaged so multiple sensors sharing
    a room collapse into one line per room instead of overlapping raw noise."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    bucket_seconds = max(60, hours * 3600 // 150)

    buckets: dict[str, dict[datetime, list[float]]] = {}

    def _add(label: str, ts: datetime, value: float | None) -> None:
        if value is None:
            return
        bucket = _bucket_start(ts, bucket_seconds, cutoff)
        buckets.setdefault(label, {}).setdefault(bucket, []).append(value)

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
            _add(room_by_id[s.device_id], s.timestamp, s.temperature)

    ac_ids = [d.id for d in session.exec(select(Device).where(Device.type == DeviceType.ac)).all()]
    if ac_ids:
        ac_samples = session.exec(
            select(AcSample).where(AcSample.device_id.in_(ac_ids), AcSample.timestamp >= cutoff)
        ).all()
        for s in ac_samples:
            _add("AC Indoor", s.timestamp, s.indoor_temp)
            _add("AC Outdoor", s.timestamp, s.outdoor_temp)

    return {
        label: {
            "timestamps": [b.isoformat() for b in sorted(points)],
            "temperature": [round(sum(points[b]) / len(points[b]), 2) for b in sorted(points)],
        }
        for label, points in buckets.items()
    }
