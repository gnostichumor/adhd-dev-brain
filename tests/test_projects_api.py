import sqlite3
import subprocess
from collections.abc import AsyncGenerator, Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import time_machine
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
from adhd_dash.github_client import GithubClient
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


def _make_git_project_dir(
    tmp_path: Path, name: str = "git-project", remote_url: str | None = None
) -> Path:
    project = tmp_path / name
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    if remote_url is not None:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=project, check=True)
    return project


@pytest.fixture
async def app_state_for_project_listing(
    override_db_session: Engine,
) -> AsyncGenerator[Callable[[httpx.MockTransport], None], None]:
    """Set `app.state.config`/`app.state.db_engine` for `GET /api/v1/projects`,
    mirroring `app_state_for_refresh`. That route additionally reads
    `request.app.state.github_client` directly (again mirroring `main.py`'s
    `lifespan`, which plain `TestClient(app)` never runs), so this yields a
    setter each test calls with its own `httpx.MockTransport` to build and
    assign it -- letting each test control exactly what the mocked GitHub
    API returns without touching real network access.

    An async fixture (not a plain sync generator) specifically so teardown
    can `await` the constructed `GithubClient.aclose()` -- mirroring
    `main.py`'s own `lifespan` shutdown -- rather than leaking its
    underlying `httpx.AsyncClient` per test.
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

    def _set_github_transport(transport: httpx.MockTransport) -> None:
        app.state.github_client = GithubClient(
            token="",
            check_ttl_minutes=60,
            client=httpx.AsyncClient(transport=transport, base_url="https://api.github.com"),
        )

    yield _set_github_transport

    del app.state.config
    del app.state.db_engine
    if hasattr(app.state, "github_client"):
        await app.state.github_client.aclose()
        del app.state.github_client


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


def test_list_projects_no_beads_with_github_remote_evaluated_and_percent_complete_is_null(
    tmp_path: Path, app_state_for_project_listing: Callable[[httpx.MockTransport], None]
) -> None:
    """The named PRD-R18 acceptance scenario: a real git checkout with a
    real github.com `origin` remote, no `.beads/`, evaluates using GitHub
    commit activity alone -- `percent_complete` stays `None` (no Beads
    adapter is invoked in this issue) while `last_github_activity_at`/
    `is_stale` are genuinely populated."""
    project_dir = _make_git_project_dir(
        tmp_path, remote_url="https://github.com/octocat/hello-world.git"
    )
    add_response = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir)})
    assert add_response.status_code == 201

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"commit": {"committer": {"date": "2026-07-01T12:00:00Z"}}}]
        )

    app_state_for_project_listing(httpx.MockTransport(handler))

    with time_machine.travel(datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC), tick=False):
        response = client.get("/api/v1/projects")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    result = body[0]
    assert result["evaluation_status"] == "evaluated"
    assert result["percent_complete"] is None
    assert result["last_github_activity_at"] is not None
    assert result["is_stale"] is False


def test_list_projects_has_beads_returns_beads_not_supported(
    tmp_path: Path, app_state_for_project_listing: Callable[[httpx.MockTransport], None]
) -> None:
    project_dir = _make_project_dir(tmp_path)
    add_response = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir)})
    assert add_response.status_code == 201

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never hit the transport when .beads is present")

    app_state_for_project_listing(httpx.MockTransport(handler))

    response = client.get("/api/v1/projects")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    result = body[0]
    assert result["evaluation_status"] == "beads_not_supported"
    assert result["percent_complete"] is None
    assert result["is_stale"] is None


def test_list_projects_no_beads_no_github_remote_returns_cannot_evaluate(
    tmp_path: Path, app_state_for_project_listing: Callable[[httpx.MockTransport], None]
) -> None:
    project_dir = _make_git_project_dir(tmp_path)
    add_response = client.post("/api/v1/projects", json={"host": "local", "path": str(project_dir)})
    assert add_response.status_code == 201

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never hit the transport with no derivable owner/repo")

    app_state_for_project_listing(httpx.MockTransport(handler))

    response = client.get("/api/v1/projects")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    result = body[0]
    assert result["evaluation_status"] == "cannot_evaluate"
    assert result["is_stale"] is None


def test_list_projects_empty_when_no_tracked_projects(
    app_state_for_project_listing: Callable[[httpx.MockTransport], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never hit the transport with no tracked projects")

    app_state_for_project_listing(httpx.MockTransport(handler))

    response = client.get("/api/v1/projects")

    assert response.status_code == 200
    assert response.json() == []


def test_list_projects_isolates_one_projects_evaluation_error_from_the_rest(
    tmp_path: Path, app_state_for_project_listing: Callable[[httpx.MockTransport], None]
) -> None:
    """A malformed-but-200 GitHub API response for ONE project (e.g. a
    response missing the expected commit/date shape) must not 500 the
    whole listing -- it's isolated to that project's row as
    `evaluation_status="evaluation_error"`, while an unrelated project in
    the same request still evaluates normally."""
    broken_project_dir = _make_git_project_dir(
        tmp_path, name="broken-project", remote_url="https://github.com/octocat/hello-world.git"
    )
    add_broken = client.post(
        "/api/v1/projects", json={"host": "local", "path": str(broken_project_dir)}
    )
    assert add_broken.status_code == 201

    fine_project_dir = _make_project_dir(tmp_path, name="fine-project")
    add_fine = client.post(
        "/api/v1/projects", json={"host": "local", "path": str(fine_project_dir)}
    )
    assert add_fine.status_code == 201

    def handler(request: httpx.Request) -> httpx.Response:
        # A 200 with a body missing the expected "commit"/"date" shape --
        # github_client.py's own docstring calls this "a genuine bug
        # surface" it deliberately lets raise (here: KeyError).
        return httpx.Response(200, json=[{"unexpected": "shape"}])

    app_state_for_project_listing(httpx.MockTransport(handler))

    response = client.get("/api/v1/projects")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2

    by_path = {result["path"]: result for result in body}
    broken_result = by_path[str(broken_project_dir.resolve())]
    fine_result = by_path[str(fine_project_dir.resolve())]

    assert broken_result["evaluation_status"] == "evaluation_error"
    assert broken_result["is_stale"] is None
    assert fine_result["evaluation_status"] == "beads_not_supported"
