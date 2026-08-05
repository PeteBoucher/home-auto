"""Tests for SQLite connection configuration."""
import sqlite3

from app.db import _configure_sqlite


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
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        finally:
            conn.close()
