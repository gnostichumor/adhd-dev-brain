"""Scheduled discovery + last-seen refresh pass (PRD R4, adhd-dash-c6f.4).

This pass does exactly two things, on a cadence controlled by
`config.polling.interval_minutes` (see `adhd_dash.main.build_scheduler`):
re-run `discover_projects` (`adhd_dash.discovery`) across every configured
host's roots, get-or-create a `TrackedProject` row for each match found
(reusing `adhd_dash.projects.get_or_create_project`, the same idempotent
mechanism the manual `POST /api/v1/projects` route uses), and stamp
`last_seen_at` on every project it touches -- both newly-created rows and
already-tracked rows it reconfirms. No separate `last_polled_at` field was
added: `last_seen_at`'s existing intent ("last-seen bookkeeping", see
`adhd_dash.models`) already means exactly "discovery confirmed this project
is still present," which is what a poll pass produces.

Deliberately out of scope for this pass, and why:

- **Discovery here is local-filesystem-only.** `discover_projects` is
  documented as a local walker (`adhd_dash.discovery` module docstring,
  adhd-dash-c6f.2) -- it does not reach over SSH. For a `HostConfig` whose
  `roots` live on a genuinely remote Tailscale host, this pass currently
  walks the *dashboard process's own* local filesystem at that root path
  string, which only happens to be correct when the root is reachable
  locally (e.g. the dashboard is deployed on that same host, or the path is
  bind-mounted in). Real remote directory listing would need a
  filesystem-walking analog of the asyncssh-based runner
  `BdAdapter`/`BrAdapter` already use for remote status calls -- that
  mechanism does not exist yet, and building it is out of scope here.
- **No Beads or GitHub status ingestion happens here.** This pass only
  confirms a project's *presence*, not its *status* (percent-complete,
  activity timestamps). Wiring in `BdAdapter`/`BrAdapter`/`GithubClient` is
  blocked on two prerequisites this issue does not add: (1) a stored signal
  for which Beads CLI variant (`bd` vs `br`) a given `TrackedProject` uses,
  and (2) a stored GitHub remote/repo association per project. Neither
  exists in the data model yet -- adding either now would be scope creep for
  a polling-cadence issue. (Relatedly, `adhd-dash-v3d`, a GithubClient
  rate-limit-handling bug, stays open: its own fix-condition is "wired into
  a real poll loop," which still isn't true after this issue.)
"""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session

from adhd_dash.config import Config
from adhd_dash.discovery import discover_projects
from adhd_dash.projects import get_or_create_project


def _utcnow() -> datetime:
    return datetime.now(UTC)


def poll(config: Config, engine: Engine) -> None:
    """Run one discovery + last-seen refresh pass across all configured hosts.

    For every `HostConfig` in `config.hosts`, walks each of its `roots` via
    `discover_projects` (local-filesystem-only -- see module docstring),
    get-or-creates a `TrackedProject` row keyed on `(host.name, resolved
    ref.path)` -- resolved to match `POST /api/v1/projects`'s own
    canonicalization, so the two writers agree on one form for the same
    real directory -- and sets `last_seen_at` to now on every row touched,
    whether it was just created or already tracked. Does not perform
    Beads/GitHub status ingestion -- see module docstring for why.
    """
    with Session(engine) as session:
        for host in config.hosts:
            for root in host.roots:
                for ref in discover_projects(Path(root)):
                    # `discover_projects` yields `ref.path` unresolved
                    # (`str(directory)`, see discovery.py's `_walk`), but
                    # `POST /api/v1/projects` stores the *resolved* form
                    # (`str(directory.resolve())`, see api/v1.py's
                    # `add_project`) specifically so symlink/relative/
                    # trailing-slash aliases of the same real directory
                    # can't defeat the `(host, path)` idempotency this
                    # function shares with that route. Resolving here too
                    # keeps both writers agreeing on one canonical form --
                    # without it, a manually-added project reachable from a
                    # poll root via a non-canonical path (e.g. a symlinked
                    # root, or `/tmp` vs `/private/tmp` on macOS) would get
                    # a second, duplicate row instead of being reconfirmed.
                    resolved_path = str(Path(ref.path).resolve())
                    project, _created = get_or_create_project(session, host.name, resolved_path)
                    project.last_seen_at = _utcnow()
                    session.add(project)
        session.commit()
