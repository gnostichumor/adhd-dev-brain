"""Normalized Beads-ingestion result shared by `BdAdapter` and `BrAdapter`.

See docs/architecture.md §1: two Beads CLIs (`bd`, `br`) with different raw
JSON schemas both normalize to this single `ProjectStatus` shape, so the
staleness/percent-complete logic downstream never needs to know which CLI a
tracked project uses.

`last_github_activity_at` and `is_stale` (adhd-dash-oui.1/R9,
adhd-dash-oui.2/R17) are the staleness-evaluation layer described in
architecture.md §5 -- populated by whichever caller merges in GitHub
activity (a Beads adapter alone never sets `last_github_activity_at`; see
`BdAdapter.get_status`/`BrAdapter.get_status`), evaluated on demand rather
than stored, since it depends on the currently-configured threshold and the
current time, neither of which belongs on this otherwise-static status
snapshot.
"""

from datetime import datetime, timedelta

from pydantic import BaseModel


class CannotEvaluateStalenessError(ValueError):
    """Raised by `ProjectStatus.is_stale` when neither Beads nor GitHub
    activity is available. architecture.md §5 requires this be flagged
    explicitly rather than silently defaulted to fresh or stale -- a plain
    `bool | None` return invites exactly that mistake (`if is_stale(...):`
    silently treats `None` as falsy/"not stale"), so this case fails loudly
    instead, matching this codebase's established "fail loudly" convention
    (see `adhd_dash.config`'s module docstring).
    """


class ProjectStatus(BaseModel):
    """A tracked project's Beads- and GitHub-derived status, as of the last
    poll.

    `percent_complete` is `closed_issues / total_issues`, or `None` when
    `total_issues == 0` -- a project with zero Beads issues has no defined
    completion percentage (PRD R18), not a 0% one.

    `last_beads_activity_at` is `None` only when the project has zero
    issues; otherwise it is timezone-aware (UTC), for both adapters that
    populate this field. Both `bd`'s and `br`'s timestamp formats are
    confirmed against live installs: `bd` emits `Z`-suffixed ISO8601 (see
    `BdAdapter.get_status`); `br` (pinned to v0.2.15, re-verify on upgrade --
    same caveat `docs/architecture.md` §1a uses for `br`'s other confirmed
    fields) emits `Z`-suffixed ISO8601 with microsecond fractional seconds
    (e.g. `"2026-06-15T04:58:18.381241Z"`), which `datetime.fromisoformat`
    parses directly into a timezone-aware UTC `datetime` on Python 3.12 (see
    `BrAdapter.get_status`). No naive-datetime normalization step is needed
    for either CLI.

    `last_github_activity_at` is `None` when the project has no reachable
    GitHub remote configured (R18); otherwise timezone-aware (UTC), from
    `GithubClient.get_latest_commit_activity`.
    """

    percent_complete: float | None
    last_beads_activity_at: datetime | None
    last_github_activity_at: datetime | None
    total_issues: int
    closed_issues: int

    def is_stale(self, *, threshold_days: int, now: datetime) -> bool:
        """Per-signal staleness (PRD R9, R17, R18; architecture.md §5).

        Evaluates `last_beads_activity_at` and `last_github_activity_at`
        INDEPENDENTLY against `threshold_days`, with equal weight (R9): the
        project is stale as soon as *either available* signal is older than
        the threshold, even if the other is fresh -- deliberately more
        sensitive than `max(beads_age, github_age) > threshold`, which would
        hide exactly the drift case (e.g. active commits with an abandoned
        Beads tracker) this rule exists to catch (R17). A signal that isn't
        available (`None`) simply can't make the project stale on its own,
        which also gives R18's "no Beads init" case for free: staleness
        falls through to GitHub activity alone.

        Raises `CannotEvaluateStalenessError` when NEITHER signal is
        available: architecture.md §5 is explicit that such a project can't
        be evaluated for staleness at all. This fails loudly rather than
        returning `None`, so a caller can't accidentally write
        `if project.is_stale(...):` and have the un-evaluable case silently
        fall through as "not stale" (`None` is falsy) -- exactly the silent
        default §5 warns against.

        `threshold_days` and `now` (which must be timezone-aware, like
        `last_beads_activity_at`/`last_github_activity_at` above -- a naive
        `now` raises `TypeError` when compared against them) are explicit
        parameters rather than implicit config/`datetime.now()` reads so
        this stays a pure, deterministic function of its inputs -- callers
        own reading `config.yaml`'s `staleness.default_threshold_days` and
        the current time (see `time_machine`'s use elsewhere in this
        codebase's tests for why an implicit "now" is avoided).
        """
        if self.last_beads_activity_at is None and self.last_github_activity_at is None:
            raise CannotEvaluateStalenessError(
                "neither last_beads_activity_at nor last_github_activity_at is available"
            )

        threshold = timedelta(days=threshold_days)
        beads_stale = (
            self.last_beads_activity_at is not None
            and now - self.last_beads_activity_at > threshold
        )
        github_stale = (
            self.last_github_activity_at is not None
            and now - self.last_github_activity_at > threshold
        )
        return beads_stale or github_stale
