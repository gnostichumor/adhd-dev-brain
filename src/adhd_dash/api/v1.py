"""v1 API routes.

Only a health check and the manual add-project route exist here by design --
config/state, Beads adapters, the GitHub client, and staleness evaluation
each own their own routes and land as those subsystems are implemented, not
bundled in here ahead of time.
"""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from adhd_dash.db import get_db_session
from adhd_dash.discovery import detect_project
from adhd_dash.models import TrackedProject

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


def _select_project(session: Session, host: str, path: str) -> TrackedProject | None:
    return session.exec(
        select(TrackedProject).where(TrackedProject.host == host, TrackedProject.path == path)
    ).first()


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
    if not directory.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path does not exist or is not a directory",
        )

    if detect_project(directory) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path is not a Beads project or git repository",
        )

    existing = _select_project(session, body.host, body.path)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    project = TrackedProject(host=body.host, path=body.path)
    session.add(project)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _select_project(session, body.host, body.path)
        if existing is None:
            # The insert failed with a uniqueness conflict, but the row it
            # conflicted with isn't found on re-query -- something other
            # than the (host, path) race we're guarding against. Surface it
            # rather than silently pretending success.
            raise
        response.status_code = status.HTTP_200_OK
        return existing

    session.refresh(project)
    response.status_code = status.HTTP_201_CREATED
    return project
