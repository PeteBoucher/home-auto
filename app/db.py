from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

engine = create_engine("sqlite:///home_auto.db")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE device ADD COLUMN media_state TEXT",
            "ALTER TABLE device ADD COLUMN current_app TEXT",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists


def get_session():
    with Session(engine) as session:
        yield session
