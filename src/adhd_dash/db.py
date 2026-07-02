"""SQLModel engine setup for `state.db`.

Single-file SQLite datastore for mutable runtime state (the tracked-project
registry and per-project snooze/archive/last-seen bookkeeping) -- see
docs/architecture.md §3 and adhd_dash.models. Config tuning lives in
config.yaml instead; this module never reads or writes it.
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

DEFAULT_DB_PATH = "state.db"


def create_db_engine(path: str | Path = DEFAULT_DB_PATH) -> Engine:
    """Build a SQLModel/SQLAlchemy engine bound to a single SQLite file.

    `check_same_thread=False` matches SQLModel's documented FastAPI pattern:
    the same engine is shared across request-handling threads/tasks, with
    per-request `Session`s providing isolation.
    """
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


def init_db(engine: Engine) -> None:
    """Create all tables declared via SQLModel metadata (idempotent)."""
    SQLModel.metadata.create_all(engine)


def get_session(engine: Engine) -> Generator[Session, None, None]:
    """FastAPI-dependency-style session generator bound to `engine`."""
    with Session(engine) as session:
        yield session
