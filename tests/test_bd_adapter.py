import json
from datetime import UTC, datetime

import pytest

from adhd_dash.adapters.bd_adapter import BdAdapter, CommandRunner
from adhd_dash.config import HostConfig

# Captured verbatim from a live `bd` install against this repo's own
# `.beads` data -- see adhd-dash-8d2.3's issue description. Per-status
# counts (open_issues=34, blocked_issues=31) are facets that overlap and
# deliberately sum to more than total_issues=39; only total_issues/
# closed_issues feed percent_complete.
STATUS_JSON_POPULATED = {
    "schema_version": 1,
    "summary": {
        "average_lead_time_hours": 0,
        "blocked_issues": 31,
        "closed_issues": 4,
        "deferred_issues": 0,
        "epics_eligible_for_closure": 0,
        "in_progress_issues": 1,
        "open_issues": 34,
        "pinned_issues": 0,
        "ready_issues": 3,
        "total_issues": 39,
    },
}

LIST_JSON_POPULATED = [
    {
        "id": "adhd-dash-8d2.3",
        "title": "Define ProjectStatus model and implement BdAdapter",
        "status": "in_progress",
        "priority": 1,
        "issue_type": "task",
        "created_at": "2026-07-02T18:14:26Z",
        "created_by": "gnostichumor",
        "updated_at": "2026-07-02T18:14:55Z",
        "dependency_count": 0,
        "dependent_count": 0,
        "comment_count": 0,
    }
]

# Captured from a throwaway freshly-`bd init`'d empty project.
STATUS_JSON_EMPTY = {
    "schema_version": 1,
    "summary": {
        "average_lead_time_hours": 0,
        "blocked_issues": 0,
        "closed_issues": 0,
        "deferred_issues": 0,
        "epics_eligible_for_closure": 0,
        "in_progress_issues": 0,
        "open_issues": 0,
        "pinned_issues": 0,
        "ready_issues": 0,
        "total_issues": 0,
    },
}

LIST_JSON_EMPTY: list[dict[str, object]] = []


def make_runner(status_json: object, list_json: object) -> CommandRunner:
    async def runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
        if argv[:2] == ["bd", "status"]:
            return json.dumps(status_json)
        assert argv[:2] == ["bd", "list"]
        return json.dumps(list_json)

    return runner


# --- get_status: populated project ----------------------------------------


async def test_get_status_populated_project() -> None:
    adapter = BdAdapter(runner=make_runner(STATUS_JSON_POPULATED, LIST_JSON_POPULATED))

    status = await adapter.get_status("/srv/projects/foo")

    assert status.total_issues == 39
    assert status.closed_issues == 4
    assert status.percent_complete == pytest.approx(4 / 39)
    assert status.last_beads_activity_at == datetime(2026, 7, 2, 18, 14, 55, tzinfo=UTC)


# --- get_status: zero-issue project ----------------------------------------


async def test_get_status_zero_issues_project() -> None:
    adapter = BdAdapter(runner=make_runner(STATUS_JSON_EMPTY, LIST_JSON_EMPTY))

    status = await adapter.get_status("/srv/projects/empty")

    assert status.total_issues == 0
    assert status.closed_issues == 0
    assert status.percent_complete is None
    assert status.last_beads_activity_at is None


# --- get_status: facet counts never summed as denominator ------------------


async def test_percent_complete_uses_total_issues_not_summed_facets() -> None:
    """Regression guard: open_issues + blocked_issues (34 + 31 = 65) must
    never be used as the denominator -- only summary.total_issues (39)."""
    adapter = BdAdapter(runner=make_runner(STATUS_JSON_POPULATED, LIST_JSON_POPULATED))

    status = await adapter.get_status("/srv/projects/foo")

    assert status.percent_complete == pytest.approx(4 / 39)
    assert status.percent_complete != pytest.approx(4 / 65)


# --- get_status: command failure -------------------------------------------


async def test_get_status_raises_on_nonzero_exit() -> None:
    async def failing_runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
        raise RuntimeError(f"command {argv} -C {path} exited 1: bd: not a beads project")

    adapter = BdAdapter(runner=failing_runner)

    with pytest.raises(RuntimeError):
        await adapter.get_status("/srv/projects/broken")


# --- get_status: host routing ----------------------------------------------


async def test_get_status_passes_host_none_for_local() -> None:
    captured_hosts: list[HostConfig | None] = []

    async def runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
        captured_hosts.append(host)
        if argv[:2] == ["bd", "status"]:
            return '{"summary": {"total_issues": 0, "closed_issues": 0}}'
        return "[]"

    adapter = BdAdapter(runner=runner)

    await adapter.get_status("/srv/projects/local-project")

    assert captured_hosts == [None, None]


async def test_get_status_passes_through_host_config_for_remote() -> None:
    host = HostConfig(
        name="homelab-box",
        ssh_host="homelab-box.tailnet.ts.net",
        ssh_user="deploy",
        ssh_key_path="/run/secrets/ssh_key",
        roots=["/srv/projects"],
    )
    captured_hosts: list[HostConfig | None] = []

    async def runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
        captured_hosts.append(host)
        if argv[:2] == ["bd", "status"]:
            return '{"summary": {"total_issues": 0, "closed_issues": 0}}'
        return "[]"

    adapter = BdAdapter(runner=runner)

    await adapter.get_status("/srv/projects/remote-project", host=host)

    assert captured_hosts == [host, host]
