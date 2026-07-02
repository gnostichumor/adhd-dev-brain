"""Normalized Beads-ingestion result shared by `BdAdapter` and `BrAdapter`.

See docs/architecture.md §1: two Beads CLIs (`bd`, `br`) with different raw
JSON schemas both normalize to this single `ProjectStatus` shape, so the
staleness/percent-complete logic downstream never needs to know which CLI a
tracked project uses.

Deliberately minimal for this issue (adhd-dash-8d2.3): only the fields a
Beads adapter alone can populate. GitHub activity, staleness classification,
and anything else are populated by other adapters/epics later -- see
architecture.md §5.
"""

from datetime import datetime

from pydantic import BaseModel


class ProjectStatus(BaseModel):
    """A tracked project's Beads-derived status, as of the last poll.

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
    """

    percent_complete: float | None
    last_beads_activity_at: datetime | None
    total_issues: int
    closed_issues: int
