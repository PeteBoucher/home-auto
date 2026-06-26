from sqlmodel import Session, SQLModel, create_engine

engine = create_engine("sqlite:///home_auto.db")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
