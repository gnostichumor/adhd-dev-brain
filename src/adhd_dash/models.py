"""SQLModel table models for `state.db`.

See docs/architecture.md §3: state.db holds mutable, user-generated runtime
state -- the tracked-project registry and per-project snooze/archive/
last-seen bookkeeping (PRD R3) -- as opposed to config.yaml's static tuning.

Staleness signal timestamps (`last_beads_activity_at`,
`last_github_activity_at`), percent-complete, and live_url deliberately do
NOT live here -- those belong to later epics (PRD R8, R17/R18) and adding
them now would be scope creep for the config/state foundations issue.
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TrackedProject(SQLModel, table=True):
    """A project tracked by the dashboard, identified by (host, path)."""

    id: int | None = Field(default=None, primary_key=True)
    host: str
    path: str
    archived: bool = False
    archived_at: datetime | None = None
    snoozed_until: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
