"""SQLModel engine setup for `state.db`.

Single-file SQLite datastore for mutable runtime state (the tracked-project
registry and per-project snooze/archive/last-seen bookkeeping) -- see
docs/architecture.md §3 and adhd_dash.models. Config tuning lives in
config.yaml instead; this module never reads or writes it.
"""

from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

# SQLModel.metadata only knows about table classes that have actually been
# imported somewhere in the process -- without this import, a caller that
# never separately imports adhd_dash.models (e.g. main.py's lifespan) would
# have init_db() silently create zero tables.
from adhd_dash import models as _models  # noqa: F401

DEFAULT_DB_PATH = "state.db"


def create_db_engine(path: str | Path = DEFAULT_DB_PATH) -> Engine:
    """Build a SQLModel/SQLAlchemy engine bound to a single SQLite file.

    `check_same_thread=False` matches SQLModel's documented FastAPI pattern:
    the same engine is shared across request-handling threads/tasks, with
    per-request `Session`s providing isolation.

    `timeout` (the Python `sqlite3` driver's busy-timeout, in seconds --
    adhd-dash-v28) makes a writer that finds the DB locked (e.g. a scheduled
    `poll()` pass and a manual `POST /api/v1/refresh` writing at the same
    moment) wait and retry instead of immediately raising
    `sqlite3.OperationalError: database is locked`. 5s is generous enough to
    ride out a same-machine poll/refresh overlap without a caller noticing a
    delay, while still failing fast (rather than hanging) for this
    single-operator home-lab tool if something is genuinely stuck.
    """
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )


def init_db(engine: Engine) -> None:
    """Create all tables declared via SQLModel metadata (idempotent)."""
    SQLModel.metadata.create_all(engine)


def get_session(engine: Engine) -> Generator[Session, None, None]:
    """FastAPI-dependency-style session generator bound to `engine`."""
    with Session(engine) as session:
        yield session


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """FastAPI dependency: yield a `Session` bound to the app's DB engine.

    `get_session` above takes an explicit `engine` argument, so it can't be
    used directly as a `Depends(...)` callable (FastAPI has no engine to
    inject). This wraps it, pulling the engine off `request.app.state.db_engine`
    (set by `main.py`'s `lifespan`) and delegating to `get_session` for the
    actual session lifecycle -- routes should `Depends(get_db_session)`, and
    tests should override this dependency via `app.dependency_overrides`
    rather than relying on `lifespan` (which touches the real `state.db`).
    """
    yield from get_session(request.app.state.db_engine)
