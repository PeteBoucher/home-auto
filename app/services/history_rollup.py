"""Nightly rollup of raw climate/AC samples into daily mean/min/max summaries.

ClimateSample and AcSample are the source for every chart that reads them
(app/api/climate.py's dashboard widget, /devices/{id}/climate-chart,
/devices/{id}/ac-chart) — all capped at hours<=168, so nothing ever queries a
raw sample older than 7 days. This job rolls samples past that point into one
ClimateDailySummary/AcDailySummary row per device per day (kept indefinitely,
mirroring the EnergyDailySummary/PowerSample split) and deletes the raw rows,
so per-sensor history survives at day granularity without either sample table
growing forever.
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.devices.models import AcDailySummary, AcSample, ClimateDailySummary, ClimateSample

log = logging.getLogger(__name__)

RAW_RETENTION = timedelta(days=7)


def _merge_metric(summary, values: list[float], sum_attr: str, count_attr: str, min_attr: str, max_attr: str) -> None:
    """Add this batch's contribution to a summary row's running sum/count/min/max
    for one metric — additive, so it's safe to call more than once for the same
    (device, day) across separate rollup runs (see ClimateDailySummary docstring)."""
    if not values:
        return
    setattr(summary, sum_attr, getattr(summary, sum_attr) + sum(values))
    setattr(summary, count_attr, getattr(summary, count_attr) + len(values))
    prior_min, prior_max = getattr(summary, min_attr), getattr(summary, max_attr)
    setattr(summary, min_attr, min(values) if prior_min is None else min(prior_min, min(values)))
    setattr(summary, max_attr, max(values) if prior_max is None else max(prior_max, max(values)))


def _rollup(session: Session, sample_cls, summary_cls, metrics: list[tuple[str, str]], now: datetime) -> int:
    """metrics: [(sample_field, summary_prefix), ...]. Returns rows rolled up."""
    cutoff = now - RAW_RETENTION
    rows = session.exec(select(sample_cls).where(sample_cls.timestamp < cutoff)).all()
    if not rows:
        return 0
    by_day: dict[tuple[int, str], list] = defaultdict(list)
    for row in rows:
        by_day[(row.device_id, row.timestamp.date().isoformat())].append(row)
    for (device_id, day), day_rows in by_day.items():
        summary = session.exec(
            select(summary_cls).where(summary_cls.device_id == device_id, summary_cls.date == day)
        ).first() or summary_cls(device_id=device_id, date=day)
        for field, prefix in metrics:
            values = [v for r in day_rows if (v := getattr(r, field)) is not None]
            _merge_metric(summary, values, f"{prefix}_sum", f"{prefix}_count", f"{prefix}_min", f"{prefix}_max")
        session.add(summary)
    for row in rows:
        session.delete(row)
    session.commit()
    return len(rows)


def rollup_climate_samples(session: Session, now: datetime | None = None) -> None:
    n = _rollup(
        session, ClimateSample, ClimateDailySummary,
        [("temperature", "temp"), ("humidity", "humidity")],
        now or datetime.utcnow(),
    )
    if n:
        log.info("Rolled up %d climate samples into daily summaries", n)


def rollup_ac_samples(session: Session, now: datetime | None = None) -> None:
    n = _rollup(
        session, AcSample, AcDailySummary,
        [("indoor_temp", "indoor"), ("outdoor_temp", "outdoor")],
        now or datetime.utcnow(),
    )
    if n:
        log.info("Rolled up %d AC samples into daily summaries", n)
