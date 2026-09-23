"""Tests for services/history_rollup.py — the nightly job that rolls raw
ClimateSample/AcSample rows older than the 7-day raw-retention window into
daily mean/min/max summaries and deletes the raw rows."""
from datetime import datetime, timedelta

from sqlmodel import select

from app.devices.models import (
    AcDailySummary, AcSample, ClimateDailySummary, ClimateSample, Device, DeviceType, Integration,
)
from app.services.history_rollup import rollup_ac_samples, rollup_climate_samples


class TestRollupClimateSamples:
    def test_old_samples_rolled_up_and_deleted(self, session, z2m_sensor):
        now = datetime.utcnow()
        old_day = now - timedelta(days=10)
        session.add(ClimateSample(device_id=z2m_sensor.id, temperature=20.0, humidity=50.0, timestamp=old_day))
        session.add(ClimateSample(device_id=z2m_sensor.id, temperature=24.0, humidity=54.0, timestamp=old_day + timedelta(hours=1)))
        session.commit()

        rollup_climate_samples(session, now=now)

        assert session.exec(select(ClimateSample)).all() == []
        summary = session.exec(select(ClimateDailySummary).where(ClimateDailySummary.device_id == z2m_sensor.id)).one()
        assert summary.date == old_day.date().isoformat()
        assert summary.temp_count == 2
        assert summary.temp_sum == 44.0
        assert summary.temp_min == 20.0
        assert summary.temp_max == 24.0
        assert summary.humidity_count == 2
        assert summary.humidity_min == 50.0
        assert summary.humidity_max == 54.0

    def test_recent_samples_within_retention_are_untouched(self, session, z2m_sensor):
        now = datetime.utcnow()
        session.add(ClimateSample(device_id=z2m_sensor.id, temperature=21.0, timestamp=now - timedelta(hours=1)))
        session.commit()

        rollup_climate_samples(session, now=now)

        assert len(session.exec(select(ClimateSample)).all()) == 1
        assert session.exec(select(ClimateDailySummary)).all() == []

    def test_no_op_when_nothing_to_roll_up(self, session):
        # Must not raise or create a spurious summary row for no samples.
        rollup_climate_samples(session, now=datetime.utcnow())
        assert session.exec(select(ClimateDailySummary)).all() == []

    def test_null_metric_values_are_skipped_not_averaged_in(self, session, z2m_sensor):
        now = datetime.utcnow()
        old_day = now - timedelta(days=10)
        # A sensor that only reports temperature this reading (humidity None)
        # shouldn't drag the humidity mean toward zero.
        session.add(ClimateSample(device_id=z2m_sensor.id, temperature=20.0, humidity=None, timestamp=old_day))
        session.commit()

        rollup_climate_samples(session, now=now)

        summary = session.exec(select(ClimateDailySummary)).one()
        assert summary.temp_count == 1
        assert summary.humidity_count == 0
        assert summary.humidity_min is None

    def test_rerun_across_two_partial_runs_merges_instead_of_overwriting(self, session, z2m_sensor):
        """A day's raw rows can end up split across two rollup runs if the
        job's schedule shifts mid-day (e.g. a service restart) — the second
        run must add to the first's aggregate, not replace it."""
        now = datetime.utcnow()
        old_day = now - timedelta(days=10)

        session.add(ClimateSample(device_id=z2m_sensor.id, temperature=10.0, timestamp=old_day))
        session.commit()
        rollup_climate_samples(session, now=now)  # first partial run

        session.add(ClimateSample(device_id=z2m_sensor.id, temperature=30.0, timestamp=old_day + timedelta(hours=2)))
        session.commit()
        rollup_climate_samples(session, now=now)  # second partial run, same day

        summary = session.exec(select(ClimateDailySummary)).one()
        assert summary.temp_count == 2
        assert summary.temp_sum == 40.0
        assert summary.temp_min == 10.0
        assert summary.temp_max == 30.0

    def test_samples_for_different_devices_kept_separate(self, session, z2m_sensor):
        now = datetime.utcnow()
        old_day = now - timedelta(days=10)
        other = Device(
            name="Other Sensor", device_id="other-sensor",
            type=DeviceType.sensor, integration=Integration.zigbee2mqtt,
        )
        session.add(other)
        session.commit()
        session.refresh(other)

        session.add(ClimateSample(device_id=z2m_sensor.id, temperature=20.0, timestamp=old_day))
        session.add(ClimateSample(device_id=other.id, temperature=30.0, timestamp=old_day))
        session.commit()

        rollup_climate_samples(session, now=now)

        summaries = {s.device_id: s for s in session.exec(select(ClimateDailySummary)).all()}
        assert summaries[z2m_sensor.id].temp_sum == 20.0
        assert summaries[other.id].temp_sum == 30.0


class TestRollupAcSamples:
    def test_old_samples_rolled_up_and_deleted(self, session):
        ac = Device(name="Living Room A/C", device_id="ac-1", type=DeviceType.ac, integration=Integration.hon)
        session.add(ac)
        session.commit()
        session.refresh(ac)

        now = datetime.utcnow()
        old_day = now - timedelta(days=10)
        session.add(AcSample(device_id=ac.id, indoor_temp=22.0, outdoor_temp=30.0, timestamp=old_day))
        session.add(AcSample(device_id=ac.id, indoor_temp=24.0, outdoor_temp=32.0, timestamp=old_day + timedelta(hours=1)))
        session.commit()

        rollup_ac_samples(session, now=now)

        assert session.exec(select(AcSample)).all() == []
        summary = session.exec(select(AcDailySummary).where(AcDailySummary.device_id == ac.id)).one()
        assert summary.indoor_count == 2
        assert summary.indoor_sum == 46.0
        assert summary.indoor_min == 22.0
        assert summary.indoor_max == 24.0
        assert summary.outdoor_count == 2
        assert summary.outdoor_min == 30.0
        assert summary.outdoor_max == 32.0
