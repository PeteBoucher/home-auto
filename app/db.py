import logging
from typing import Annotated

from fastapi import Depends
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine

log = logging.getLogger(__name__)


def _configure_sqlite(dbapi_connection, connection_record) -> None:
    """WAL lets readers and a writer work concurrently instead of SQLite's
    default hard single-writer lock, and busy_timeout makes a write that does
    collide retry internally for a few seconds instead of raising
    'database is locked' immediately — both needed since background tasks
    (MQTT listener, pollers) and HTTP request handlers write to the same
    file concurrently."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


engine = create_engine("sqlite:///home_auto.db")
event.listen(engine, "connect", _configure_sqlite)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE device ADD COLUMN media_state TEXT",
            "ALTER TABLE device ADD COLUMN current_app TEXT",
            "ALTER TABLE device ADD COLUMN dimmable INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE device ADD COLUMN power_on_behavior TEXT",
            "ALTER TABLE device ADD COLUMN overload_protection TEXT",
            "ALTER TABLE device ADD COLUMN power REAL",
            "ALTER TABLE device ADD COLUMN current REAL",
            "ALTER TABLE device ADD COLUMN voltage REAL",
            "ALTER TABLE device ADD COLUMN energy REAL",
            "ALTER TABLE automation ADD COLUMN trigger_sun_event TEXT",
            "ALTER TABLE automation ADD COLUMN trigger_sun_offset INTEGER",
            "ALTER TABLE automation ADD COLUMN trigger_compare_field TEXT",
            "ALTER TABLE device ADD COLUMN sensor_temperature REAL",
            "ALTER TABLE device ADD COLUMN humidity REAL",
            "ALTER TABLE device ADD COLUMN battery INTEGER",
            "ALTER TABLE device ADD COLUMN room TEXT",
            "ALTER TABLE device ADD COLUMN energy_today REAL",
            "ALTER TABLE device ADD COLUMN energy_month REAL",
            "ALTER TABLE powersample ADD COLUMN energy_today REAL",
            "ALTER TABLE powersample ADD COLUMN energy_month REAL",
            "ALTER TABLE device ADD COLUMN group_id INTEGER REFERENCES devicegroup(id)",
            "ALTER TABLE device ADD COLUMN group_override INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE device ADD COLUMN eco INTEGER",
            "ALTER TABLE device ADD COLUMN quiet INTEGER",
            "ALTER TABLE device ADD COLUMN louvre_position INTEGER",
            "ALTER TABLE device ADD COLUMN indoor_temp REAL",
            "ALTER TABLE device ADD COLUMN outdoor_temp REAL",
            "ALTER TABLE acsample ADD COLUMN ac_state INTEGER",
            "ALTER TABLE automation ADD COLUMN trigger_window_start TEXT",
            "ALTER TABLE automation ADD COLUMN trigger_window_end TEXT",
            "ALTER TABLE device ADD COLUMN display_source_id INTEGER REFERENCES device(id)",
            "ALTER TABLE device ADD COLUMN has_external_display INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE device ADD COLUMN temp_range_low REAL",
            "ALTER TABLE device ADD COLUMN temp_range_high REAL",
            "ALTER TABLE device ADD COLUMN time_in_range_seconds INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE device ADD COLUMN time_in_range_updated_at TEXT",
            "ALTER TABLE device ADD COLUMN climate_widget_cutoff TEXT",
            "ALTER TABLE automation ADD COLUMN action_group_id INTEGER REFERENCES devicegroup(id)",
            "ALTER TABLE automation ADD COLUMN action_snapshot_before INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists

    with engine.begin() as conn:
        _allow_null_action_device_id(conn)


# Full current column set of `automation`, in the order the table is rebuilt
# with below — kept in sync with the Automation model by hand, same as the
# ALTER TABLE list above.
_AUTOMATION_COLUMNS = (
    "id", "name", "enabled", "trigger_type", "trigger_time", "trigger_device_id",
    "trigger_field", "trigger_operator", "trigger_value", "trigger_compare_field",
    "trigger_sun_event", "trigger_sun_offset", "trigger_window_start", "trigger_window_end",
    "action_device_id", "action_group_id", "action_type", "action_value", "action_snapshot_before",
)


def _allow_null_action_device_id(conn) -> None:
    """Automations could originally only target a single Device
    (action_device_id NOT NULL); they can now target a DeviceGroup instead
    (action_group_id), leaving action_device_id null. SQLite has no ALTER
    COLUMN to drop a NOT NULL constraint, so an existing table created under
    the old schema needs a full rebuild — a fresh install never hits this,
    since create_all() already makes the column nullable from the model.
    """
    cols = conn.execute(text("PRAGMA table_info(automation)")).fetchall()
    action_device_col = next((c for c in cols if c[1] == "action_device_id"), None)
    if not action_device_col or action_device_col[3] == 0:
        return  # no table yet, or already nullable
    log.warning("Rebuilding 'automation' table to allow group-targeted rules (action_device_id -> nullable)")
    conn.execute(text("""
        CREATE TABLE automation_new (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            enabled BOOLEAN NOT NULL,
            trigger_type VARCHAR NOT NULL,
            trigger_time VARCHAR,
            trigger_device_id INTEGER REFERENCES device(id),
            trigger_field VARCHAR,
            trigger_operator VARCHAR,
            trigger_value VARCHAR,
            trigger_compare_field VARCHAR,
            trigger_sun_event VARCHAR,
            trigger_sun_offset INTEGER,
            trigger_window_start VARCHAR,
            trigger_window_end VARCHAR,
            action_device_id INTEGER REFERENCES device(id),
            action_group_id INTEGER REFERENCES devicegroup(id),
            action_type VARCHAR NOT NULL,
            action_value VARCHAR,
            action_snapshot_before BOOLEAN NOT NULL DEFAULT 0
        )
    """))
    columns = ", ".join(_AUTOMATION_COLUMNS)
    conn.execute(text(f"INSERT INTO automation_new ({columns}) SELECT {columns} FROM automation"))
    conn.execute(text("DROP TABLE automation"))
    conn.execute(text("ALTER TABLE automation_new RENAME TO automation"))


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
