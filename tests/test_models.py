from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from adhd_dash.db import create_db_engine, init_db
from adhd_dash.models import TrackedProject


@pytest.fixture
def engine(tmp_path: Path) -> Generator[Engine, None, None]:
    """Temp-file SQLite engine, isolated from the real state.db file.

    A plain sqlite:///:memory: engine hands each new connection its own
    empty database, so data written in one Session wouldn't be visible in
    the next -- a temp file avoids that gotcha while still never touching
    the real state.db.
    """
    test_engine = create_db_engine(tmp_path / "test-state.db")
    init_db(test_engine)
    yield test_engine


def test_create_and_read_tracked_project(engine: Engine) -> None:
    with Session(engine) as session:
        project = TrackedProject(host="example-host", path="/srv/projects/foo")
        session.add(project)
        session.commit()
        session.refresh(project)

        assert project.id is not None
        assert project.archived is False
        assert project.archived_at is None
        assert project.snoozed_until is None
        assert isinstance(project.created_at, datetime)

    with Session(engine) as session:
        fetched = session.exec(
            select(TrackedProject).where(TrackedProject.path == "/srv/projects/foo")
        ).one()

        assert fetched.host == "example-host"
        assert fetched.path == "/srv/projects/foo"


def test_update_archives_tracked_project(engine: Engine) -> None:
    with Session(engine) as session:
        project = TrackedProject(host="example-host", path="/srv/projects/bar")
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    archived_at = datetime(2026, 7, 2, 12, 0, 0)
    with Session(engine) as session:
        project = session.get(TrackedProject, project_id)
        assert project is not None
        project.archived = True
        project.archived_at = archived_at
        session.add(project)
        session.commit()

    with Session(engine) as session:
        fetched = session.get(TrackedProject, project_id)
        assert fetched is not None
        assert fetched.archived is True
        assert fetched.archived_at == archived_at


def test_snooze_and_last_seen_persist(engine: Engine) -> None:
    snoozed_until = datetime(2026, 8, 1, 0, 0, 0)
    last_seen_at = datetime(2026, 7, 1, 9, 30, 0)

    with Session(engine) as session:
        project = TrackedProject(
            host="example-host",
            path="/srv/projects/baz",
            snoozed_until=snoozed_until,
            last_seen_at=last_seen_at,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    with Session(engine) as session:
        fetched = session.get(TrackedProject, project_id)
        assert fetched is not None
        assert fetched.snoozed_until == snoozed_until
        assert fetched.last_seen_at == last_seen_at


def test_duplicate_host_path_violates_unique_constraint(engine: Engine) -> None:
    """adhd-dash-70d: (host, path) must be unique at the DB level -- a
    second row with the same pair is rejected even when inserted directly
    via a Session, independent of any application-level get-or-create
    logic."""
    with Session(engine) as session:
        session.add(TrackedProject(host="example-host", path="/srv/projects/dup"))
        session.commit()

    with Session(engine) as session:
        session.add(TrackedProject(host="example-host", path="/srv/projects/dup"))
        with pytest.raises(IntegrityError):
            session.commit()
