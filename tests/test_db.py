"""Tests for SQLite connection configuration."""
import sqlite3

from sqlalchemy import create_engine, text

from app.db import _allow_null_action_device_id, _configure_sqlite


class TestConfigureSqlite:
    def test_enables_wal_mode(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        try:
            _configure_sqlite(conn, None)
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            conn.close()

    def test_sets_busy_timeout(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        try:
            _configure_sqlite(conn, None)
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
        finally:
            conn.close()


class TestAllowNullActionDeviceId:
    """Automations gained the ability to target a DeviceGroup (action_group_id)
    instead of a single Device, leaving action_device_id null — but an
    existing install's `automation` table still has it NOT NULL from before
    that feature existed, and SQLite can't ALTER COLUMN that away, so
    _allow_null_action_device_id() rebuilds the table instead."""

    def _old_schema_engine_with_a_row(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE automation (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    trigger_type VARCHAR NOT NULL,
                    trigger_time VARCHAR,
                    trigger_device_id INTEGER,
                    trigger_field VARCHAR,
                    trigger_operator VARCHAR,
                    trigger_value VARCHAR,
                    trigger_compare_field VARCHAR,
                    trigger_sun_event VARCHAR,
                    trigger_sun_offset INTEGER,
                    trigger_window_start VARCHAR,
                    trigger_window_end VARCHAR,
                    action_device_id INTEGER NOT NULL,
                    action_type VARCHAR NOT NULL,
                    action_value VARCHAR
                )
            """))
            conn.execute(text(
                "INSERT INTO automation (id, name, enabled, trigger_type, action_device_id, action_type) "
                "VALUES (1, 'Existing rule', 1, 'time', 5, 'set_state_on')"
            ))
        return engine

    def test_rebuilds_table_preserving_data_and_allows_null(self):
        engine = self._old_schema_engine_with_a_row()
        # init_db() always adds action_group_id/action_snapshot_before via plain
        # ALTER TABLEs first — mirror that ordering rather than assuming this
        # function does it.
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE automation ADD COLUMN action_group_id INTEGER"))
            conn.execute(text("ALTER TABLE automation ADD COLUMN action_snapshot_before INTEGER NOT NULL DEFAULT 0"))
        with engine.begin() as conn:
            _allow_null_action_device_id(conn)

        with engine.begin() as conn:
            row = conn.execute(text("SELECT id, name, action_device_id FROM automation WHERE id = 1")).fetchone()
            assert row == (1, "Existing rule", 5)

            # a NULL action_device_id (a group-targeted rule) is now accepted
            conn.execute(text(
                "INSERT INTO automation (id, name, enabled, trigger_type, action_group_id, action_type) "
                "VALUES (2, 'Group rule', 1, 'sun', 9, 'set_color_temp')"
            ))
            row2 = conn.execute(text(
                "SELECT action_device_id, action_group_id FROM automation WHERE id = 2"
            )).fetchone()
            assert row2 == (None, 9)

    def test_noop_when_already_nullable(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE automation (
                    id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, enabled BOOLEAN NOT NULL,
                    trigger_type VARCHAR NOT NULL, action_device_id INTEGER, action_group_id INTEGER,
                    action_type VARCHAR NOT NULL
                )
            """))
        with engine.begin() as conn:
            _allow_null_action_device_id(conn)  # must not raise or touch the table
        with engine.begin() as conn:
            cols = {c[1] for c in conn.execute(text("PRAGMA table_info(automation)")).fetchall()}
        assert {"action_device_id", "action_group_id"} <= cols

    def test_noop_when_table_does_not_exist_yet(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            _allow_null_action_device_id(conn)  # fresh install — must not raise
