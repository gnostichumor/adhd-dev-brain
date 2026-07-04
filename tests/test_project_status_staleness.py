from datetime import UTC, datetime, timedelta

import pytest

from adhd_dash.adapters.models import ProjectStatus

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
        pytest.param(STALE, FRESH, True, id="beads_stale_github_fresh"),
        pytest.param(FRESH, STALE, True, id="beads_fresh_github_stale"),
        pytest.param(FRESH, None, False, id="beads_only_fresh"),
        pytest.param(STALE, None, True, id="beads_only_stale"),
        pytest.param(None, FRESH, False, id="github_only_fresh"),
        pytest.param(None, STALE, True, id="github_only_stale"),
        pytest.param(None, None, None, id="neither_available"),
    ],
)
def test_is_stale_combinations(
    beads: datetime | None, github: datetime | None, expected: bool | None
) -> None:
    """PRD R9 (equal-weight), R17 (either-signal, not max()), R18 (GitHub
    alone when no Beads init) -- the full combination table named in
    adhd-dash-oui's own epic acceptance criteria, architecture.md §5."""
    status = _status(beads, github)

    assert status.is_stale(threshold_days=THRESHOLD_DAYS, now=NOW) is expected


def test_is_stale_beads_stale_github_fresh_would_be_wrong_under_max() -> None:
    """The exact case a max()-based implementation gets wrong (R17): the
    freshest signal must NOT mask staleness on the other side."""
    status = _status(STALE, FRESH)

    assert status.is_stale(threshold_days=THRESHOLD_DAYS, now=NOW) is True
    # A max()-based rule would compare max(beads_age, github_age) = github_age
    # (fresh) against the threshold and wrongly call this "not stale".
    newest_activity = max(STALE, FRESH)
    assert (NOW - newest_activity) < timedelta(days=THRESHOLD_DAYS)


def test_is_stale_gives_symmetric_results_regardless_of_which_signal_is_stale() -> None:
    """R9: no implicit weighting/ordering bias between the two signals --
    swapping which signal is stale must not change the outcome."""
    beads_stale_result = _status(STALE, FRESH).is_stale(threshold_days=THRESHOLD_DAYS, now=NOW)
    github_stale_result = _status(FRESH, STALE).is_stale(threshold_days=THRESHOLD_DAYS, now=NOW)

    assert beads_stale_result == github_stale_result is True


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
