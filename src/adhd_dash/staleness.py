"""Per-project staleness evaluation for `GET /api/v1/projects` (PRD R18,
adhd-dash-oui.3).

Bridges a `TrackedProject` DB row to `ProjectStatus.is_stale` (adapters/
models.py, adhd-dash-oui.1/oui.2) by deriving the GitHub half of the signal
on the fly (`adhd_dash.git_remote`) and explicitly refusing to guess at a
Beads status for a project that has `.beads/` -- see `BeadsNotSupportedError`
below.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from adhd_dash.adapters.models import ProjectStatus
from adhd_dash.git_remote import get_github_owner_repo
from adhd_dash.github_client import GithubClient
from adhd_dash.models import TrackedProject


class BeadsNotSupportedError(Exception):
    """Raised when `project.path` has a `.beads/` directory.

    This issue (adhd-dash-oui.3 / R18) only implements the no-Beads,
    GitHub-only path. A project WITH `.beads/` has a real Beads signal
    available in principle, but evaluating it correctly requires knowing
    which CLI variant (`bd` vs `br`) manages it and, for a remote host,
    SSH-reachability -- a separate, harder gap tracked as adhd-dash-fqd.
    Raising a dedicated exception here keeps this distinction explicit and
    fails loudly per this codebase's convention, rather than silently
    misreporting the Beads signal as unavailable.
    """


@dataclass(frozen=True)
class ProjectEvaluation:
    """Result of successfully evaluating one `TrackedProject`.

    `percent_complete` and `last_beads_activity_at` are always `None` here
    -- a project with `.beads/` present always raises
    `BeadsNotSupportedError` before a `ProjectEvaluation` is ever built.
    These fields are forward-looking for when adhd-dash-fqd (Beads adapter
    selection) lands, not dead code.
    """

    percent_complete: float | None
    last_beads_activity_at: datetime | None
    last_github_activity_at: datetime | None
    is_stale: bool


def _has_beads(path: str) -> bool:
    try:
        return (Path(path) / ".beads").is_dir()
    except OSError:
        # An unreadable path can't be positively confirmed as beads-having;
        # treat as "no beads" -- matches discovery.py's OSError tolerance.
        return False


async def evaluate_project(
    project: TrackedProject,
    github_client: GithubClient,
    *,
    threshold_days: int,
    now: datetime,
) -> ProjectEvaluation:
    """Evaluate one `TrackedProject`'s staleness using GitHub activity alone
    (R18: no Beads adapter is invoked here at all).

    Raises:
        BeadsNotSupportedError: `project.path` has a `.beads/` directory.
        CannotEvaluateStalenessError: no derivable GitHub owner/repo is
            available for this project (propagated from
            `ProjectStatus.is_stale` -- since this function never populates
            a Beads signal, that's the only signal that can be present).

    `threshold_days`/`now` are explicit parameters for the same reason
    `ProjectStatus.is_stale` takes them explicitly: a pure, deterministic
    function of its inputs.
    """
    if _has_beads(project.path):
        raise BeadsNotSupportedError(project.path)

    last_github_activity_at: datetime | None = None
    owner_repo = await get_github_owner_repo(project.path)
    if owner_repo is not None:
        owner, repo = owner_repo
        last_github_activity_at = await github_client.get_latest_commit_activity(owner, repo)

    status = ProjectStatus(
        percent_complete=None,
        last_beads_activity_at=None,
        last_github_activity_at=last_github_activity_at,
        total_issues=0,
        closed_issues=0,
    )
    # Raises CannotEvaluateStalenessError when last_github_activity_at is
    # also None -- propagated as-is; the caller decides how to surface it.
    is_stale = status.is_stale(threshold_days=threshold_days, now=now)

    return ProjectEvaluation(
        percent_complete=status.percent_complete,
        last_beads_activity_at=status.last_beads_activity_at,
        last_github_activity_at=status.last_github_activity_at,
        is_stale=is_stale,
    )
