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
    issues; otherwise it is timezone-aware (UTC) when populated by
    `BdAdapter` -- `bd`'s `Z`-suffixed ISO8601 timestamps are confirmed
    against a live install (see `BdAdapter.get_status`). `br`'s timestamp
    *string format* has NOT been verified the same way: docs/architecture.md
    §1a confirms `bd`/`br` share field names (`updated_at`, etc.) but never
    samples `br`'s actual timestamp format -- BrAdapter (adhd-dash-8d2.4)
    must confirm this against a live install and normalize to tz-aware
    before populating this field, or a naive `br` timestamp here would raise
    `TypeError` the first time a staleness comparison touches it.
    """

    percent_complete: float | None
    last_beads_activity_at: datetime | None
    total_issues: int
    closed_issues: int
