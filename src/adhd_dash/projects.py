"""Shared get-or-create logic for `TrackedProject` rows.

Used by both the manual `POST /api/v1/projects` route (adhd_dash.api.v1)
and the polling loop (adhd_dash.polling, adhd-dash-c6f.4) so the two paths
share one row-creation/idempotency mechanism instead of duplicating the
DB-level UniqueConstraint(host, path) race-condition handling.
"""

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from adhd_dash.models import TrackedProject


def select_project(session: Session, host: str, path: str) -> TrackedProject | None:
    return session.exec(
        select(TrackedProject).where(TrackedProject.host == host, TrackedProject.path == path)
    ).first()


def get_or_create_project(session: Session, host: str, path: str) -> tuple[TrackedProject, bool]:
    """Idempotently get-or-create a `TrackedProject` row.

    Returns `(project, created)`. Does NOT touch `last_seen_at` -- callers
    that want "confirmed present" bookkeeping (the polling loop) set it
    themselves after this returns; the manual-add API route intentionally
    leaves it untouched, preserving its existing, already-reviewed behavior
    (adhd-dash-c6f.3 / adhd-dash-70d).

    The DB-level `UniqueConstraint("host", "path")` on `TrackedProject`
    (adhd-dash-70d) is what actually guarantees no duplicate row ever
    exists -- the `IntegrityError` handling below is a defensive
    race-condition safety net for two near-simultaneous callers, not the
    primary mechanism.
    """
    existing = select_project(session, host, path)
    if existing is not None:
        return existing, False

    project = TrackedProject(host=host, path=path)
    session.add(project)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = select_project(session, host, path)
        if existing is None:
            raise
        return existing, False

    session.refresh(project)
    return project, True
