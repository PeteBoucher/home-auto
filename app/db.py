from typing import Annotated

from fastapi import Depends
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine


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
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
