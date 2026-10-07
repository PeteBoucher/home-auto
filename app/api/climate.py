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
    A/C's own indoor temperature, bucketed and averaged so multiple sensors
    sharing a room collapse into one line per room instead of overlapping raw
    noise. The A/C doesn't report humidity, so its series just carry nulls
    there. The A/C's outdoor reading is deliberately left out — it's rarely
    running now, so that line is mostly stale."""
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
    widget_cutoff_by_id = {d.id: d.climate_widget_cutoff for d in sensors if d.climate_widget_cutoff}
    widget_resume_by_id = {d.id: d.climate_widget_resume_at for d in sensors if d.climate_widget_resume_at}
    combine_outdoor_ids = {d.id for d in sensors if d.climate_combine_outdoor}
    # per-device, per-bucket temperature readings for sensors flagged climate_combine_outdoor — kept
    # separate from `buckets` (which is per-room, and per-device here since several such sensors can
    # share a room label) so each device's own reports can be tracked independently before combining.
    combine_device_points: dict[int, dict[datetime, list[float]]] = {}
    if room_by_id:
        samples = session.exec(
            select(ClimateSample).where(
                ClimateSample.device_id.in_(room_by_id.keys()),
                ClimateSample.timestamp >= cutoff,
            )
        ).all()
        for s in samples:
            # A repurposed sensor's readings from after the repurpose (e.g. an
            # outdoor sensor moved into the filament dryer box) shouldn't be
            # folded into its old room's line here — see Device.climate_widget_cutoff.
            # If it's since been moved back (climate_widget_resume_at set), only the
            # readings from the repurposed window itself stay excluded.
            device_cutoff = widget_cutoff_by_id.get(s.device_id)
            if device_cutoff and s.timestamp >= device_cutoff:
                resume_at = widget_resume_by_id.get(s.device_id)
                if resume_at is None or s.timestamp < resume_at:
                    continue
            room = room_by_id[s.device_id]
            _add(room, s.timestamp, "temperature", s.temperature)
            _add(room, s.timestamp, "humidity", s.humidity)
            if s.device_id in combine_outdoor_ids and s.temperature is not None:
                bucket = _bucket_start(s.timestamp, bucket_seconds, cutoff)
                combine_device_points.setdefault(s.device_id, {}).setdefault(bucket, []).append(s.temperature)

    # "Outdoor (combined)" — the lowest reading among the flagged sensors, since direct sun only ever
    # inflates a reading, never deflates one: whichever sensor isn't currently sun-struck is the
    # closer-to-true-ambient one. See Device.climate_combine_outdoor. Comparing bucket-for-bucket isn't
    # enough on its own — these are sleepy sensors on independent, un-synced report cycles, so most
    # buckets only ever have a fresh reading from one of them. Each device's last known reading is
    # carried forward (same held-value assumption the raw per-room lines report under) so every bucket
    # compares an actual pair of estimates rather than falling back to whichever sensor happened to
    # report in that narrow slice — which, without this, trails whichever one reports more often.
    if combine_device_points:
        device_bucket_avg = {
            dev_id: {b: sum(vals) / len(vals) for b, vals in dev_buckets.items()}
            for dev_id, dev_buckets in combine_device_points.items()
        }
        all_combine_buckets = sorted({b for dev_series in device_bucket_avg.values() for b in dev_series})
        last_known: dict[int, float] = {}
        for bucket in all_combine_buckets:
            for dev_id, dev_series in device_bucket_avg.items():
                if bucket in dev_series:
                    last_known[dev_id] = dev_series[bucket]
            buckets.setdefault("Outdoor (combined)", {}).setdefault(bucket, {})["temperature"] = [min(last_known.values())]

    ac_ids = [d.id for d in session.exec(select(Device).where(Device.type == DeviceType.ac)).all()]
    if ac_ids:
        ac_samples = session.exec(
            select(AcSample).where(AcSample.device_id.in_(ac_ids), AcSample.timestamp >= cutoff)
        ).all()
        for s in ac_samples:
            _add("AC Indoor", s.timestamp, "temperature", s.indoor_temp)

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
