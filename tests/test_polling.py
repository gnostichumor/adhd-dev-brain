import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import time_machine
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from adhd_dash.config import (
    Config,
    GithubConfig,
    HostConfig,
    LoggingConfig,
    PollingConfig,
    StalenessConfig,
)
from adhd_dash.db import create_db_engine, init_db
from adhd_dash.main import build_scheduler
from adhd_dash.models import TrackedProject
from adhd_dash.polling import poll
from adhd_dash.projects import get_or_create_project as real_get_or_create_project


def _make_config(roots: list[str], interval_minutes: int = 60) -> Config:
    return Config(
        staleness=StalenessConfig(default_threshold_days=14),
        polling=PollingConfig(interval_minutes=interval_minutes),
        hosts=[
            HostConfig(
                name="local",
                ssh_host="",
                ssh_user="",
                ssh_key_path="",
                roots=roots,
            )
        ],
        github=GithubConfig(check_ttl_minutes=60, token=""),
        logging=LoggingConfig(level="INFO"),
    )


def test_poll_discovers_and_creates_projects(tmp_path: Path) -> None:
    beads_project = tmp_path / "beads-project"
    (beads_project / ".beads").mkdir(parents=True)
    git_project = tmp_path / "git-project"
    (git_project / ".git").mkdir(parents=True)

    config = _make_config([str(tmp_path)])
    engine = create_db_engine(tmp_path / "state.db")
    init_db(engine)

    poll(config, engine)

    with Session(engine) as session:
        rows = session.exec(select(TrackedProject).where(TrackedProject.host == "local")).all()

    assert len(rows) == 2
    paths = {row.path for row in rows}
    assert paths == {str(beads_project), str(git_project)}
    for row in rows:
        assert row.host == "local"
        assert row.last_seen_at is not None


def test_poll_is_idempotent_and_refreshes_last_seen_at(tmp_path: Path) -> None:
    project_dir = tmp_path / "my-project"
    (project_dir / ".beads").mkdir(parents=True)

    config = _make_config([str(tmp_path)])
    engine = create_db_engine(tmp_path / "state.db")
    init_db(engine)

    with time_machine.travel("2026-01-01T00:00:00+00:00", tick=False):
        poll(config, engine)
        with Session(engine) as session:
            first_row = session.exec(
                select(TrackedProject).where(TrackedProject.path == str(project_dir))
            ).one()
            first_seen_at = first_row.last_seen_at
            first_id = first_row.id

    with time_machine.travel("2026-01-01T01:00:00+00:00", tick=False):
        poll(config, engine)
        with Session(engine) as session:
            rows = session.exec(
                select(TrackedProject).where(TrackedProject.path == str(project_dir))
            ).all()

    assert len(rows) == 1
    assert rows[0].id == first_id
    assert first_seen_at is not None
    assert rows[0].last_seen_at is not None
    assert rows[0].last_seen_at > first_seen_at


def test_poll_reconfirms_existing_manually_added_project(tmp_path: Path) -> None:
    """Pre-insert the row the way `POST /api/v1/projects` actually would --
    with the *resolved* path (`Path.resolve()`), not the raw directory
    string -- since that's the real writer poll must reconcile with. `poll`
    must resolve `discover_projects`'s (unresolved) `ref.path` the same way
    before its get-or-create lookup, or this would produce a duplicate row
    instead of reconfirming the existing one."""
    project_dir = tmp_path / "existing-project"
    (project_dir / ".git").mkdir(parents=True)
    resolved_path = str(project_dir.resolve())

    config = _make_config([str(tmp_path)])
    engine = create_db_engine(tmp_path / "state.db")
    init_db(engine)

    with Session(engine) as session:
        preexisting = TrackedProject(host="local", path=resolved_path, last_seen_at=None)
        session.add(preexisting)
        session.commit()
        session.refresh(preexisting)
        preexisting_id = preexisting.id

    poll(config, engine)

    with Session(engine) as session:
        rows = session.exec(
            select(TrackedProject).where(TrackedProject.path == resolved_path)
        ).all()

    assert len(rows) == 1
    assert rows[0].id == preexisting_id
    assert rows[0].last_seen_at is not None


def test_poll_reconciles_with_resolved_path_when_root_is_a_symlink(tmp_path: Path) -> None:
    """`discover_projects` builds child paths by joining onto whatever root
    it was given (see discovery.py's `_walk`) -- it does not resolve
    symlinks. If a configured `HostConfig` root is itself a symlink (e.g. a
    `~/projects` symlink, or macOS's `/tmp` -> `/private/tmp`), the raw
    `ref.path` poll sees (`<symlink-root>/existing-project`) differs, as a
    string, from the resolved path `POST /api/v1/projects` would have
    stored for the same real directory. `poll` must resolve before its
    get-or-create lookup so the two agree -- otherwise this scenario
    produces a duplicate row instead of reconfirming the existing one."""
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    project_dir = real_root / "existing-project"
    (project_dir / ".git").mkdir(parents=True)
    resolved_path = str(project_dir.resolve())

    symlink_root = tmp_path / "symlink-root"
    symlink_root.symlink_to(real_root)

    config = _make_config([str(symlink_root)])
    engine = create_db_engine(tmp_path / "state.db")
    init_db(engine)

    with Session(engine) as session:
        preexisting = TrackedProject(host="local", path=resolved_path, last_seen_at=None)
        session.add(preexisting)
        session.commit()
        session.refresh(preexisting)
        preexisting_id = preexisting.id

    poll(config, engine)

    with Session(engine) as session:
        rows = session.exec(select(TrackedProject).where(TrackedProject.host == "local")).all()

    assert len(rows) == 1
    assert rows[0].id == preexisting_id
    assert rows[0].last_seen_at is not None


def test_poll_commits_progress_incrementally_not_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`poll()` must commit per-project (adhd-dash-v28), not once for the
    whole multi-root pass -- otherwise a mid-pass failure on one project
    rolls back the whole shared transaction, discarding `last_seen_at`
    stamps already set for earlier, unrelated projects in the same pass.

    Two roots (list order is deterministic, unlike filesystem-walk order
    within a single root) each hold one project. `get_or_create_project` is
    monkeypatched to call through unchanged for project A but raise for
    project B, simulating a mid-pass failure. `get_or_create_project`
    itself already commits when it *creates* a row, so project A's row
    exists under both the old and new code -- the fix only changes whether
    project A's *`last_seen_at` stamp* (set by `poll()` itself, after
    `get_or_create_project` returns) survives project B's later failure:
    under the old single-commit-at-the-end code, that stamp is still
    pending when the exception unwinds out of the `with Session(...)`
    block, so it gets rolled back; under the fix, it was already committed
    before project B was even attempted.
    """
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    root_a.mkdir()
    root_b.mkdir()
    project_a = root_a / "project-a"
    project_b = root_b / "project-b"
    (project_a / ".beads").mkdir(parents=True)
    (project_b / ".beads").mkdir(parents=True)
    resolved_a = str(project_a.resolve())
    resolved_b = str(project_b.resolve())

    config = _make_config([str(root_a), str(root_b)])
    engine = create_db_engine(tmp_path / "state.db")
    init_db(engine)

    def fake_get_or_create_project(
        session: Session, host: str, path: str
    ) -> tuple[TrackedProject, bool]:
        if path == resolved_b:
            raise RuntimeError("simulated mid-pass failure for project B")
        return real_get_or_create_project(session, host, path)

    monkeypatch.setattr("adhd_dash.polling.get_or_create_project", fake_get_or_create_project)

    with pytest.raises(RuntimeError, match="simulated mid-pass failure"):
        poll(config, engine)

    with Session(engine) as session:
        row_a = session.exec(
            select(TrackedProject).where(TrackedProject.path == resolved_a)
        ).one_or_none()
        row_b = session.exec(
            select(TrackedProject).where(TrackedProject.path == resolved_b)
        ).one_or_none()

    assert row_a is not None
    assert row_a.last_seen_at is not None
    assert row_b is None


def test_build_scheduler_registers_poll_job_with_configured_interval(tmp_path: Path) -> None:
    config = _make_config(roots=[], interval_minutes=15)
    engine = create_db_engine(tmp_path / "state.db")

    scheduler = build_scheduler(config, engine)

    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "poll"
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval == timedelta(minutes=15)


def test_build_scheduler_registers_poll_job_with_max_instances_one(tmp_path: Path) -> None:
    """`max_instances=1` (adhd-dash-v28) prevents two SCHEDULED poll passes
    from ever overlapping each other if one pass runs longer than the
    configured interval. It does not, by itself, protect against a
    scheduled poll overlapping `POST /api/v1/refresh`'s direct `poll()`
    call -- see that route's docstring for why that race is instead
    accepted and bounded rather than prevented."""
    config = _make_config(roots=[], interval_minutes=15)
    engine = create_db_engine(tmp_path / "state.db")

    scheduler = build_scheduler(config, engine)

    job = scheduler.get_jobs()[0]
    assert job.max_instances == 1


async def test_scheduled_poll_job_failure_is_logged_by_apscheduler_default_handling(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adhd-dash-s85: a scheduled poll job's failure is NOT silently
    swallowed by APScheduler's default handling -- its executor already logs
    any job exception at ERROR level. Locks that behavior against the
    actual registered "poll" job, running for real via `scheduler.start()`
    (rather than trusting it as an unverified assumption), so no extra
    error-handling hook is needed here.
    """
    config = _make_config(roots=[], interval_minutes=15)
    engine = create_db_engine(tmp_path / "state.db")

    def failing_poll(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated busy-timeout exceeded")

    monkeypatch.setattr("adhd_dash.main.poll", failing_poll)
    scheduler = build_scheduler(config, engine)
    scheduler.modify_job("poll", next_run_time=datetime.now(UTC))

    with caplog.at_level("ERROR"):
        scheduler.start()
        for _ in range(100):
            if any("raised an exception" in record.message for record in caplog.records):
                break
            await asyncio.sleep(0.05)
        scheduler.shutdown(wait=False)

    assert any(
        record.levelname == "ERROR" and "raised an exception" in record.message
        for record in caplog.records
    )
