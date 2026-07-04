import sqlite3
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from sqlmodel import Session, select

from adhd_dash.config import (
    Config,
    GithubConfig,
    LoggingConfig,
    PollingConfig,
    StalenessConfig,
)
from adhd_dash.db import create_db_engine, get_db_session, init_db
from adhd_dash.main import app
from adhd_dash.models import TrackedProject

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_db_session(tmp_path: Path) -> Generator[Engine, None, None]:
    """Replace get_db_session with one backed by an isolated tmp_path engine.

    `app` is a module-level singleton shared across the whole test suite
    (see tests/test_health.py), so the override must be cleared after each
    test -- otherwise it would leak into other test files that import the
    same `app`. Lifespan is never triggered here (plain TestClient(app), no
    `with`), so this is the only way app.state.db_engine would ever be set
    for these tests -- and we bypass it entirely via dependency_overrides.

    Yields the engine itself (not just None) so other fixtures/tests that
    need `POST /api/v1/refresh` to see the same rows (that route reads
    `request.app.state.db_engine` directly, not the dependency-injected
    session) can reuse it instead of standing up a second, disconnected one.
    """
    engine = create_db_engine(tmp_path / "test-state.db")
    init_db(engine)

    def _get_test_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _get_test_session
    yield engine
    app.dependency_overrides.clear()


@pytest.fixture
def app_state_for_refresh(override_db_session: Engine) -> Generator[None, None, None]:
    """Set `app.state.config`/`app.state.db_engine` for `POST /api/v1/refresh`.

    That route reads config/engine directly off `app.state` (mirroring how
    `main.py`'s `lifespan` sets them), which plain `TestClient(app)` never
    populates since lifespan doesn't run. Bound to the same engine
    `override_db_session` already set up, and cleaned up afterward so it
    doesn't leak into other tests sharing the same `app` singleton.
    """
    config = Config(
        staleness=StalenessConfig(default_threshold_days=14),
        polling=PollingConfig(interval_minutes=60),
        hosts=[],
        github=GithubConfig(check_ttl_minutes=60, token=""),
        logging=LoggingConfig(level="INFO"),
    )
    app.state.config = config
    app.state.db_engine = override_db_session
    yield
    del app.state.config
    del app.state.db_engine


def _make_project_dir(tmp_path: Path, name: str = "my-project", marker: str = ".beads") -> Path:
    project = tmp_path / name
    (project / marker).mkdir(parents=True)
    return project


def test_add_valid_project_returns_201_and_persists(tmp_path: Path) -> None:
    project_dir = _make_project_dir(tmp_path)

    response = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir)})

    assert response.status_code == 201
    body = response.json()
    assert body["host"] == "local"
    assert body["path"] == str(project_dir)
    assert body["id"] is not None


def test_add_project_with_git_dir_also_valid(tmp_path: Path) -> None:
    project_dir = _make_project_dir(tmp_path, name="git-project", marker=".git")

    response = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir)})

    assert response.status_code == 201


def test_add_duplicate_project_is_idempotent(tmp_path: Path) -> None:
    project_dir = _make_project_dir(tmp_path)
    payload = {"host": "local", "path": str(project_dir)}

    first = client.post("/api/v1/projects", json=payload)
    second = client.post("/api/v1/projects", json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_add_duplicate_via_trailing_slash_is_still_idempotent(tmp_path: Path) -> None:
    """The same real directory expressed differently (trailing slash) must
    still be recognized as a duplicate -- validation resolves the path
    before it's used for lookup/storage, so string-level aliases of the same
    directory don't defeat idempotency."""
    project_dir = _make_project_dir(tmp_path)

    first = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir)})
    second = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir) + "/"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    engine = create_db_engine(tmp_path / "test-state.db")
    with Session(engine) as session:
        rows = session.exec(select(TrackedProject).where(TrackedProject.host == "local")).all()
    assert len(rows) == 1


def test_add_duplicate_project_leaves_exactly_one_row(tmp_path: Path) -> None:
    project_dir = _make_project_dir(tmp_path)
    payload = {"host": "local", "path": str(project_dir)}

    client.post("/api/v1/projects", json=payload)
    client.post("/api/v1/projects", json=payload)

    engine = create_db_engine(tmp_path / "test-state.db")
    with Session(engine) as session:
        rows = session.exec(
            select(TrackedProject).where(
                TrackedProject.host == "local", TrackedProject.path == str(project_dir)
            )
        ).all()
    assert len(rows) == 1


def test_add_project_nonexistent_path_returns_400(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    response = client.post("/api/v1/projects", json={"host": "local", "path": str(missing)})

    assert response.status_code == 400
    assert "detail" in response.json()


def test_add_project_path_without_beads_or_git_returns_400(tmp_path: Path) -> None:
    plain_dir = tmp_path / "just-a-folder"
    plain_dir.mkdir()

    response = client.post("/api/v1/projects", json={"host": "local", "path": str(plain_dir)})

    assert response.status_code == 400
    assert "detail" in response.json()


def test_refresh_triggers_poll_using_shared_db_engine(
    tmp_path: Path, app_state_for_refresh: None
) -> None:
    """`POST /api/v1/refresh` runs a poll pass against the same DB engine
    the rest of the test suite uses (via `override_db_session`), not a
    disconnected one -- and with an empty `hosts` config (the simplest valid
    case), the poll pass finds nothing to do, so a pre-existing row is left
    exactly as-is: not duplicated, not removed."""
    project_dir = _make_project_dir(tmp_path)
    add_response = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir)})
    assert add_response.status_code == 201

    refresh_response = client.post("/api/v1/refresh")

    assert refresh_response.status_code == 202
    assert refresh_response.json() == {"status": "polled"}

    engine = app.state.db_engine
    with Session(engine) as session:
        rows = session.exec(select(TrackedProject).where(TrackedProject.host == "local")).all()
    assert len(rows) == 1


def _sqlite_busy_error(message: str = "database is locked") -> sqlite3.OperationalError:
    """A raw `sqlite3.OperationalError` with `sqlite_errorcode` populated the
    way the real `sqlite3` driver populates it on an actual lock (verified
    against a live lock-contention repro) -- constructing the exception
    directly (as these tests must, to simulate the error without a real
    concurrent writer) does not set that attribute on its own."""
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return exc


def test_refresh_returns_503_when_poll_raises_wrapped_lock_error(
    app_state_for_refresh: None,
) -> None:
    """A concurrent scheduled poll can leave SQLite locked long enough that
    the busy-timeout (`adhd_dash.db.create_db_engine`) is exceeded anyway --
    in production this fires inside a `Session.commit()`, surfacing as
    SQLAlchemy's *wrapped* `OperationalError` (adhd-dash-v28, adhd-dash-0yo).
    `POST /api/v1/refresh` must turn that into a clean 503, not an
    unhandled 500."""
    with patch(
        "adhd_dash.api.v1.poll",
        side_effect=SQLAlchemyOperationalError(
            "UPDATE tracked_project ...", {}, _sqlite_busy_error()
        ),
    ):
        response = client.post("/api/v1/refresh")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "busy"
    assert "detail" in body


def test_refresh_returns_503_when_poll_raises_raw_sqlite_lock_error(
    app_state_for_refresh: None,
) -> None:
    """The lock can also surface as a raw `sqlite3.OperationalError`
    directly (not wrapped by SQLAlchemy), per this route's own docstring --
    `POST /api/v1/refresh` must handle that path too, not just the
    SQLAlchemy-wrapped one (adhd-dash-0yo)."""
    with patch("adhd_dash.api.v1.poll", side_effect=_sqlite_busy_error()):
        response = client.post("/api/v1/refresh")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "busy"
    assert "detail" in body


def test_refresh_propagates_non_lock_operational_error(
    app_state_for_refresh: None,
) -> None:
    """Only the SQLite busy-timeout condition (`SQLITE_BUSY`) is the
    bounded/accepted race (adhd-dash-0yo) -- a different `OperationalError`
    (e.g. a missing table) is not transient and must not be mislabeled
    "busy, try again"; it should propagate as a genuine, unhandled 500
    instead."""
    with patch(
        "adhd_dash.api.v1.poll",
        side_effect=sqlite3.OperationalError("no such table: tracked_project"),
    ):
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            client.post("/api/v1/refresh")


def test_add_project_unreadable_path_returns_400_not_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path.is_dir() raises PermissionError (not just False) when a parent
    directory isn't traversable -- a real scenario for an endpoint accepting
    arbitrary user-supplied paths. Must be a clean 400, not an unhandled 500
    -- the same PermissionError class fixed in discovery.py (adhd-dash-c6f.2)
    reintroduced at this new call site."""
    restricted = tmp_path / "restricted"

    original_is_dir = Path.is_dir

    def fake_is_dir(self: Path) -> bool:
        if self == restricted:
            raise PermissionError(f"Permission denied: {self}")
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    response = client.post("/api/v1/projects", json={"host": "local", "path": str(restricted)})

    assert response.status_code == 400
    assert "detail" in response.json()
