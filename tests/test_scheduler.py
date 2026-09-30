"""Tests for the APScheduler instance's job defaults."""
from app.services.scheduler import scheduler


class TestMisfireGraceTime:
    """A once-a-day job (refresh_sun_jobs, the history rollup/prune) that misses
    its exact tick under APScheduler's default 1s grace time is silently
    skipped rather than run late — losing that whole day's sun automations
    with no error. A generous grace window means a late tick still runs."""

    def test_default_misfire_grace_time_is_generous(self):
        assert scheduler._job_defaults["misfire_grace_time"] == 3600
