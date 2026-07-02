from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from adhd_dash.db import create_db_engine, get_db_session, init_db
from adhd_dash.main import app
from adhd_dash.models import TrackedProject

client = TestClient(app)


@pytest.fixture(autouse=True)
def override_db_session(tmp_path: Path) -> Generator[None, None, None]:
    """Replace get_db_session with one backed by an isolated tmp_path engine.

    `app` is a module-level singleton shared across the whole test suite
    (see tests/test_health.py), so the override must be cleared after each
    test -- otherwise it would leak into other test files that import the
    same `app`. Lifespan is never triggered here (plain TestClient(app), no
    `with`), so this is the only way app.state.db_engine would ever be set
    for these tests -- and we bypass it entirely via dependency_overrides.
    """
    engine = create_db_engine(tmp_path / "test-state.db")
    init_db(engine)

    def _get_test_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = _get_test_session
    yield
    app.dependency_overrides.clear()


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
