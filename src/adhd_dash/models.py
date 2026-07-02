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

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TrackedProject(SQLModel, table=True):
    """A project tracked by the dashboard, identified by (host, path).

    NOTE: the `UniqueConstraint` below (adhd-dash-70d) is applied by
    `SQLModel.metadata.create_all()` (via `db.init_db`) only when the
    `trackedproject` table is created fresh -- it does NOT retroactively
    `ALTER TABLE` an already-existing SQLite file to add the constraint.
    There's no migration framework in this project yet, and no real
    `state.db` exists in production as of this writing, so this is fine --
    just don't assume this silently retrofits an existing on-disk table.
    """

    __table_args__ = (UniqueConstraint("host", "path"),)

    id: int | None = Field(default=None, primary_key=True)
    host: str
    path: str
    archived: bool = False
    archived_at: datetime | None = None
    snoozed_until: datetime | None = None
    # None means "never confirmed present by a poll pass" -- for a project
    # manually added via POST /api/v1/projects (PRD R3, adhd-dash-c6f.3),
    # that is expected and permanent, not "not yet polled": manual-add
    # exists precisely so a project outside every configured
    # `HostConfig.roots` can be tracked, and `poll()` (adhd_dash.polling)
    # only stamps rows it discovers by walking those configured roots, so
    # such a row is structurally unreachable by any future poll. This is
    # currently safe because nothing in this codebase reads `last_seen_at`
    # for any decision (see docs/architecture.md §6). Whichever consumer
    # eventually reads it first -- e.g. the staleness-detection epic
    # (adhd-dash-oui) -- must explicitly decide how to treat
    # "manually-added, never polled" rather than assuming None always means
    # stale or always means fine.
    last_seen_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
