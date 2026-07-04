"""v1 API routes.

A health check, the manual add-project route, a manual refresh trigger
(adhd-dash-c6f.4), and a per-project staleness listing (adhd-dash-oui.3)
exist here. Beads-status ingestion for a project WITH `.beads/` present
does not land here yet -- see `list_projects`'s docstring.
"""

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from sqlmodel import Session, select

from adhd_dash.adapters.models import CannotEvaluateStalenessError
from adhd_dash.db import get_db_session
from adhd_dash.discovery import detect_project
from adhd_dash.github_client import GithubClient
from adhd_dash.models import TrackedProject
from adhd_dash.polling import poll
from adhd_dash.projects import get_or_create_project
from adhd_dash.staleness import BeadsNotSupportedError, evaluate_project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _is_sqlite_busy_error(exc: sqlite3.OperationalError | SQLAlchemyOperationalError) -> bool:
    """True only for the SQLite busy-timeout condition (`SQLITE_BUSY` --
    "database is locked"), not other `OperationalError`s (adhd-dash-0yo).

    Checked via `sqlite_errorcode` (populated by the `sqlite3` driver on
    every error it raises, Python 3.11+) rather than matching text in the
    exception's rendered message: the message can embed unrelated bound
    parameter values (SQLAlchemy doesn't `hide_parameters` here) that could
    coincidentally contain matching text, and a differently-worded lock
    message wouldn't match a literal string check at all.
    """
    orig = exc.orig if isinstance(exc, SQLAlchemyOperationalError) else exc
    return getattr(orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness check for the service."""
    return {"status": "ok"}


class AddProjectRequest(BaseModel):
    """Request body for `POST /api/v1/projects` (PRD R3: manual add)."""

    host: str
    path: str


class TrackedProjectResponse(BaseModel):
    """Minimal response shape for a `TrackedProject` row."""

    id: int
    host: str
    path: str
    created_at: datetime


@router.post("/projects", response_model=TrackedProjectResponse)
def add_project(
    body: AddProjectRequest,
    response: Response,
    session: Session = Depends(get_db_session),
) -> TrackedProject:
    """Manually register a project for tracking (PRD R3).

    Get-or-create / idempotent: adding the same `(host, path)` twice returns
    the existing row (HTTP 200) rather than erroring or creating a
    duplicate; a brand-new `(host, path)` is inserted and returned (HTTP
    201). The DB-level `UniqueConstraint("host", "path")` on `TrackedProject`
    (adhd-dash-70d) is what actually guarantees no duplicate row ever
    exists -- the `IntegrityError` handling below is a defensive
    race-condition safety net for two near-simultaneous POSTs, not the
    primary mechanism.

    Path validation is deliberately local-filesystem-only and scoped to
    `path` alone: `host` is stored as an opaque string, is NOT checked
    against `config.yaml`'s configured hosts, and is NOT used to attempt any
    remote/SSH-based validation of `path` on a non-local host. This mirrors
    `discover_projects`'s own local-only scope (`adhd_dash.discovery`,
    adhd-dash-c6f.2), which explicitly deferred remote scanning as a
    separate mechanism-of-execution concern that isn't built yet. Follow-up:
    once remote scanning/execution exists, this route should likely grow a
    remote-validation path for non-local hosts.
    """
    directory = Path(body.path)
    try:
        is_dir = directory.is_dir()
    except OSError:
        # Path.is_dir() raises PermissionError (not just False) when a
        # parent directory isn't traversable -- a real scenario for an
        # endpoint accepting arbitrary user-supplied paths (e.g. another
        # user's restricted home directory). Same class of bug fixed in
        # discovery.py's _walk/detect_project (adhd-dash-c6f.2) -- treat it
        # as "can't use this path" (400), not an unhandled 500.
        is_dir = False
    if not is_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path does not exist or is not a directory",
        )

    if detect_project(directory) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path is not a Beads project or git repository",
        )

    # Resolve once, after confirming the directory exists, and use this one
    # canonical form for the SELECT and the stored row -- otherwise two
    # requests for the same real directory expressed differently (trailing
    # slash, relative path, a symlink) would each pass validation but miss
    # each other on lookup, defeating "add-duplicate is idempotent" (the
    # UniqueConstraint wouldn't catch it either, since the raw strings
    # differ).
    resolved_path = str(directory.resolve())

    project, created = get_or_create_project(session, body.host, resolved_path)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return project


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED, response_model=None)
def refresh(request: Request) -> dict[str, str] | JSONResponse:
    """Manually trigger a poll pass out-of-band (PRD R4).

    Runs the same discovery + last-seen refresh pass as the scheduled job
    (`adhd_dash.polling.poll`), synchronously, so a caller can force an
    immediate refresh instead of waiting for the next scheduled interval.
    See `poll`'s docstring for what this pass does and does not do (in
    particular: no Beads/GitHub status ingestion yet).

    This call can race a concurrently-running SCHEDULED poll (adhd-dash-v28):
    `build_scheduler`'s `max_instances=1` only prevents two scheduled polls
    from overlapping each other, not a scheduled poll overlapping this
    route's direct call to `poll()`. That race is accepted, not actively
    prevented -- over-engineering a cross-process lock for a
    single-operator home-lab tool isn't worth it when the damage is already
    bounded: `create_db_engine`'s SQLite busy-timeout lets a concurrent
    writer wait briefly rather than fail immediately, and `poll()` now
    commits per-project (not once for the whole pass), so a genuine
    conflict can only cost the one project involved, not the whole pass. If
    the busy-timeout is exceeded anyway, SQLite raises `SQLITE_BUSY`
    ("database is locked") -- surfaced here as `sqlite3.OperationalError`
    directly, or as SQLAlchemy's wrapped `OperationalError` (whose `.orig`
    is the same underlying `sqlite3.OperationalError`) when it happens
    inside a `Session` commit -- and turned into a 503 below instead of an
    unhandled 500. Only that specific `SQLITE_BUSY` condition is treated as
    the bounded/accepted race (adhd-dash-0yo, see `_is_sqlite_busy_error`);
    any other `OperationalError` (e.g. "no such table", a corrupt/malformed
    database) is not transient and propagates as a normal unhandled 500
    instead of being mislabeled "busy, try again".
    """
    try:
        poll(request.app.state.config, request.app.state.db_engine)
    except (sqlite3.OperationalError, SQLAlchemyOperationalError) as exc:
        if not _is_sqlite_busy_error(exc):
            raise
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "busy",
                "detail": "database is busy (likely a concurrent poll) -- try again shortly",
            },
        )
    return {"status": "polled"}


class ProjectStalenessResponse(BaseModel):
    """Per-project staleness view for `GET /api/v1/projects` (PRD R18,
    adhd-dash-oui.3).

    `evaluation_status` distinguishes four outcomes explicitly so a
    consumer never has to guess at what a bare `None` means -- **branch on
    `evaluation_status`, never on `is_stale`'s truthiness**: `is_stale` is
    `None` whenever `evaluation_status != "evaluated"`, and a project that
    couldn't be evaluated must not be silently read as "not stale" (`None`
    is falsy) -- exactly the failure mode `CannotEvaluateStalenessError`
    exists to prevent internally; this field is what prevents it here too,
    since a JSON response can't raise.

    - `"evaluated"`: a real evaluation ran. In this issue, `percent_complete`
      and `last_beads_activity_at` are always `None` here -- only
      `last_github_activity_at`/`is_stale` are meaningfully populated until
      Beads adapter selection (adhd-dash-fqd) is built.
    - `"beads_not_supported"`: `project.path` has a `.beads/` directory but
      no adapter is wired to a CLI-variant selection yet (adhd-dash-fqd).
    - `"cannot_evaluate"`: no derivable GitHub owner/repo is available for
      this project (no git remote, or not a GitHub remote), and it has no
      `.beads/` either -- neither signal exists.
    - `"evaluation_error"`: an unexpected error occurred evaluating this one
      project (e.g. a malformed GitHub API response) -- logged server-side
      for an operator to investigate; isolated to this project so it
      doesn't take down the rest of the listing.
    """

    id: int
    host: str
    path: str
    evaluation_status: Literal[
        "evaluated", "beads_not_supported", "cannot_evaluate", "evaluation_error"
    ]
    percent_complete: float | None
    last_beads_activity_at: datetime | None
    last_github_activity_at: datetime | None
    is_stale: bool | None


def _unevaluated_response(
    project: TrackedProject,
    evaluation_status: Literal["beads_not_supported", "cannot_evaluate", "evaluation_error"],
) -> ProjectStalenessResponse:
    assert project.id is not None, "a persisted TrackedProject row always has an id"
    return ProjectStalenessResponse(
        id=project.id,
        host=project.host,
        path=project.path,
        evaluation_status=evaluation_status,
        percent_complete=None,
        last_beads_activity_at=None,
        last_github_activity_at=None,
        is_stale=None,
    )


@router.get("/projects", response_model=list[ProjectStalenessResponse])
async def list_projects(
    request: Request,
    session: Session = Depends(get_db_session),
) -> list[ProjectStalenessResponse]:
    """List every tracked project with its current staleness evaluation
    (PRD R18, adhd-dash-oui.3).

    Always returns HTTP 200 with one entry per `TrackedProject` row --
    never a per-project 4xx/5xx, never a silently-dropped row. A project
    that can't be fully evaluated (has `.beads/` but no adapter wired yet;
    has neither signal available; or hit an unexpected error) is still
    returned, with `evaluation_status` explicitly flagging why -- raising
    would take the whole listing down for one problem project; omitting it
    would silently hide exactly the information staleness-tracking exists
    to surface. See `ProjectStalenessResponse`'s docstring for the four
    possible values.

    The final `except Exception` is deliberate, not a blanket catch-all
    layered on carelessly: `evaluate_project`'s callees (in particular
    `GithubClient.get_latest_commit_activity`, whose own docstring calls a
    malformed-but-200 API response "a genuine bug surface" it deliberately
    lets raise) can fail in ways `BeadsNotSupportedError`/
    `CannotEvaluateStalenessError` don't cover. This route's own contract
    (every project gets a row, one bad project never 500s the whole
    listing) requires catching those too -- logged loudly server-side via
    `logger.exception`, not silently swallowed, so an operator still sees it.

    Archived/snoozed filtering is explicitly out of scope for this route in
    this issue -- it returns every `TrackedProject` row unconditionally.

    Uses `request.app.state.github_client` (built once in `main.py`'s
    `lifespan`) rather than constructing a `GithubClient` per request -- a
    fresh client per call would discard its internal per-`(owner, repo)`
    TTL cache on every request, defeating `check_ttl_minutes`.
    """
    projects = session.exec(select(TrackedProject)).all()
    github_client: GithubClient = request.app.state.github_client
    threshold_days: int = request.app.state.config.staleness.default_threshold_days
    now = datetime.now(UTC)

    results: list[ProjectStalenessResponse] = []
    for project in projects:
        try:
            evaluation = await evaluate_project(
                project, github_client, threshold_days=threshold_days, now=now
            )
        except BeadsNotSupportedError:
            results.append(_unevaluated_response(project, "beads_not_supported"))
            continue
        except CannotEvaluateStalenessError:
            results.append(_unevaluated_response(project, "cannot_evaluate"))
            continue
        except Exception:
            logger.exception(
                "Unexpected error evaluating staleness for project %s (%s)",
                project.id,
                project.path,
            )
            results.append(_unevaluated_response(project, "evaluation_error"))
            continue

        assert project.id is not None, "a persisted TrackedProject row always has an id"
        results.append(
            ProjectStalenessResponse(
                id=project.id,
                host=project.host,
                path=project.path,
                evaluation_status="evaluated",
                percent_complete=evaluation.percent_complete,
                last_beads_activity_at=evaluation.last_beads_activity_at,
                last_github_activity_at=evaluation.last_github_activity_at,
                is_stale=evaluation.is_stale,
            )
        )
    return results
