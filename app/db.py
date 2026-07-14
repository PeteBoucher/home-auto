from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

engine = create_engine("sqlite:///home_auto.db")


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
            "ALTER TABLE device ADD COLUMN sensor_temperature REAL",
            "ALTER TABLE device ADD COLUMN humidity REAL",
            "ALTER TABLE device ADD COLUMN battery INTEGER",
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
