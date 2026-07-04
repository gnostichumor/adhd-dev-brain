from datetime import UTC, datetime, timedelta

import pytest

from adhd_dash.adapters.models import CannotEvaluateStalenessError, ProjectStatus

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
THRESHOLD_DAYS = 14
FRESH = NOW - timedelta(days=1)
STALE = NOW - timedelta(days=15)


def _status(
    beads: datetime | None, github: datetime | None, total_issues: int = 5, closed_issues: int = 2
) -> ProjectStatus:
    return ProjectStatus(
        percent_complete=closed_issues / total_issues if total_issues else None,
        last_beads_activity_at=beads,
        last_github_activity_at=github,
        total_issues=total_issues,
        closed_issues=closed_issues,
    )


@pytest.mark.parametrize(
    ("beads", "github", "expected"),
    [
        pytest.param(FRESH, FRESH, False, id="both_fresh"),
        pytest.param(STALE, STALE, True, id="both_stale"),
        # The two cases a max()-based implementation would get wrong (R17):
        # the freshest signal must NOT mask staleness on the other side.
        # Together they also prove R9's no-ordering-bias claim -- swapping
        # which signal is stale doesn't change the outcome.
        pytest.param(STALE, FRESH, True, id="beads_stale_github_fresh"),
        pytest.param(FRESH, STALE, True, id="beads_fresh_github_stale"),
        pytest.param(FRESH, None, False, id="beads_only_fresh"),
        pytest.param(STALE, None, True, id="beads_only_stale"),
        pytest.param(None, FRESH, False, id="github_only_fresh"),
        pytest.param(None, STALE, True, id="github_only_stale"),
    ],
)
def test_is_stale_combinations(
    beads: datetime | None, github: datetime | None, expected: bool
) -> None:
    """PRD R9 (equal-weight), R17 (either-signal, not max()), R18 (GitHub
    alone when no Beads init) -- the full combination table named in
    adhd-dash-oui's own epic acceptance criteria, architecture.md §5."""
    status = _status(beads, github)

    assert status.is_stale(threshold_days=THRESHOLD_DAYS, now=NOW) is expected


def test_is_stale_raises_when_neither_signal_available() -> None:
    """architecture.md §5: a project with neither signal available can't be
    evaluated for staleness at all -- this must fail loudly (not return
    `None`, which a careless `if is_stale(...):` would silently treat as
    "not stale")."""
    status = _status(None, None)

    with pytest.raises(CannotEvaluateStalenessError):
        status.is_stale(threshold_days=THRESHOLD_DAYS, now=NOW)


def test_is_stale_boundary_exactly_at_threshold_is_not_stale() -> None:
    """ "Older than the threshold" (architecture.md §5) is a strict
    inequality -- an age exactly equal to the threshold is not yet stale."""
    exactly_at_threshold = NOW - timedelta(days=THRESHOLD_DAYS)
    status = _status(exactly_at_threshold, None)

    assert status.is_stale(threshold_days=THRESHOLD_DAYS, now=NOW) is False


def test_is_stale_boundary_just_past_threshold_is_stale() -> None:
    just_past_threshold = NOW - timedelta(days=THRESHOLD_DAYS, seconds=1)
    status = _status(just_past_threshold, None)

    assert status.is_stale(threshold_days=THRESHOLD_DAYS, now=NOW) is True
