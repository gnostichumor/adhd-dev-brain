"""v1 API routes.

Only a health check, the manual add-project route, and a manual refresh
trigger (adhd-dash-c6f.4) exist here by design -- Beads adapters, the GitHub
client, and staleness evaluation each own their own routes and land as those
subsystems are implemented, not bundled in here ahead of time.
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlmodel import Session

from adhd_dash.db import get_db_session
from adhd_dash.discovery import detect_project
from adhd_dash.models import TrackedProject
from adhd_dash.polling import poll
from adhd_dash.projects import get_or_create_project

router = APIRouter(prefix="/api/v1")


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


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh(request: Request) -> dict[str, str]:
    """Manually trigger a poll pass out-of-band (PRD R4).

    Runs the same discovery + last-seen refresh pass as the scheduled job
    (`adhd_dash.polling.poll`), synchronously, so a caller can force an
    immediate refresh instead of waiting for the next scheduled interval.
    See `poll`'s docstring for what this pass does and does not do (in
    particular: no Beads/GitHub status ingestion yet).
    """
    poll(request.app.state.config, request.app.state.db_engine)
    return {"status": "polled"}
