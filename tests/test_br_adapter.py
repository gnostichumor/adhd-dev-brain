import json
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adhd_dash.adapters.br_adapter import BrAdapter, CommandRunner, _build_remote_command
from adhd_dash.config import HostConfig

# Captured verbatim from a live `br` v0.2.15 install against the pokemonAgent
# project's real `.beads` data (67 issues). Per-status counts
# (open_issues=5, in_progress_issues=1) are facets that deliberately do NOT
# sum to total_issues=67; only total_issues/closed_issues feed
# percent_complete.
STATUS_JSON_POPULATED = {
    "summary": {
        "total_issues": 67,
        "open_issues": 5,
        "in_progress_issues": 1,
        "closed_issues": 61,
        "blocked_issues": 0,
        "deferred_issues": 0,
        "draft_issues": 0,
        "ready_issues": 5,
        "tombstone_issues": 0,
        "pinned_issues": 0,
        "epics_eligible_for_closure": 0,
        "average_lead_time_hours": 2.6885245901639343,
    },
    "recent_activity": {
        "hours_tracked": 24,
        "commit_count": 0,
        "issues_created": 0,
        "issues_closed": 0,
        "issues_updated": 0,
        "issues_reopened": 0,
        "total_changes": 0,
    },
}

LIST_JSON_POPULATED = {
    "issues": [
        {
            "id": "bd-hbf",
            "title": "Setup wizard: model field should auto-populate the gateway dropdown",
            "description": "Model field has a datalist + manual 'Probe' button.",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": "2026-06-15T04:58:18.381241Z",
            "created_by": "kj",
            "updated_at": "2026-06-15T04:58:18.381241Z",
            "source_repo": ".",
            "compaction_level": 0,
            "original_size": 0,
            "labels": ["console", "setup"],
            "dependency_count": 0,
            "dependent_count": 0,
        }
    ],
    "total": 67,
    "limit": 1,
    "offset": 0,
    "has_more": True,
}

# Captured from a throwaway freshly-`br init`'d empty project.
STATUS_JSON_EMPTY = {
    "summary": {
        "total_issues": 0,
        "open_issues": 0,
        "in_progress_issues": 0,
        "closed_issues": 0,
        "blocked_issues": 0,
        "deferred_issues": 0,
        "draft_issues": 0,
        "ready_issues": 0,
        "tombstone_issues": 0,
        "pinned_issues": 0,
        "epics_eligible_for_closure": 0,
    },
}

LIST_JSON_EMPTY: dict[str, object] = {
    "issues": [],
    "total": 0,
    "limit": 1,
    "offset": 0,
    "has_more": False,
}


# --- _build_remote_command: shell-injection safety --------------------------


def test_build_remote_command_round_trips_via_shlex() -> None:
    command = _build_remote_command(["br", "status", "--json"], "/srv/projects/my project")

    assert command == "cd '/srv/projects/my project' && br status --json"
    assert shlex.split(command.split(" && ", 1)[1]) == ["br", "status", "--json"]


def test_build_remote_command_prevents_shell_injection(tmp_path: Path) -> None:
    """An unquoted path with a shell separator would run a second command
    when handed to asyncssh's conn.run(str) -- this is the exact class of
    bug BdAdapter's sibling PR shipped and had to fix in its own remote
    command builder. Quoting correctly means `cd` fails (the literal
    directory `foo; touch ...` doesn't exist) rather than the shell
    splitting on `;` and running `touch` as a second command -- so a
    non-zero exit here is the *expected*, safe outcome; `check=True` is
    deliberately omitted."""
    marker = tmp_path / "should_not_exist"
    malicious_path = f"/srv/projects/foo; touch {marker}"

    command = _build_remote_command(["true"], malicious_path)
    subprocess.run(["sh", "-c", command], check=False)

    assert not marker.exists()


def make_runner(status_json: object, list_json: object) -> CommandRunner:
    async def runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
        if argv[:2] == ["br", "status"]:
            return json.dumps(status_json)
        assert argv[:2] == ["br", "list"]
        return json.dumps(list_json)

    return runner


# --- get_status: populated project ----------------------------------------


async def test_get_status_populated_project() -> None:
    adapter = BrAdapter(runner=make_runner(STATUS_JSON_POPULATED, LIST_JSON_POPULATED))

    status = await adapter.get_status("/srv/projects/foo")

    assert status.total_issues == 67
    assert status.closed_issues == 61
    assert status.percent_complete == pytest.approx(61 / 67)
    assert status.last_beads_activity_at == datetime(2026, 6, 15, 4, 58, 18, 381241, tzinfo=UTC)


# --- get_status: zero-issue project ----------------------------------------


async def test_get_status_zero_issues_project() -> None:
    adapter = BrAdapter(runner=make_runner(STATUS_JSON_EMPTY, LIST_JSON_EMPTY))

    status = await adapter.get_status("/srv/projects/empty")

    assert status.total_issues == 0
    assert status.closed_issues == 0
    assert status.percent_complete is None
    assert status.last_beads_activity_at is None


# --- get_status: facet counts never summed as denominator ------------------


async def test_percent_complete_uses_total_issues_not_summed_facets() -> None:
    """Regression guard: open_issues + in_progress_issues (5 + 1 = 6) must
    never be used as the denominator -- only summary.total_issues (67)."""
    adapter = BrAdapter(runner=make_runner(STATUS_JSON_POPULATED, LIST_JSON_POPULATED))

    status = await adapter.get_status("/srv/projects/foo")

    assert status.percent_complete == pytest.approx(61 / 67)
    assert status.percent_complete != pytest.approx(61 / 6)


# --- get_status: command failure -------------------------------------------


async def test_get_status_raises_on_nonzero_exit() -> None:
    async def failing_runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
        raise RuntimeError(f"command {argv} (cwd={path}) exited 1: br: not a beads project")

    adapter = BrAdapter(runner=failing_runner)

    with pytest.raises(RuntimeError):
        await adapter.get_status("/srv/projects/broken")


# --- get_status: malformed/missing summary fields ---------------------------


async def test_get_status_raises_on_missing_summary_fields() -> None:
    """A `br status --json` payload missing `total_issues`/`closed_issues` is
    a malformed-response bug surface, not a routine "no data" case -- this
    adapter fails loudly (KeyError) rather than defaulting silently, matching
    `BdAdapter`'s convention."""
    adapter = BrAdapter(runner=make_runner({"summary": {}}, LIST_JSON_EMPTY))

    with pytest.raises(KeyError):
        await adapter.get_status("/srv/projects/malformed")


# --- get_status: host routing ----------------------------------------------


async def test_get_status_passes_host_none_for_local() -> None:
    captured_hosts: list[HostConfig | None] = []

    async def runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
        captured_hosts.append(host)
        if argv[:2] == ["br", "status"]:
            return '{"summary": {"total_issues": 0, "closed_issues": 0}}'
        return '{"issues": []}'

    adapter = BrAdapter(runner=runner)

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
        if argv[:2] == ["br", "status"]:
            return '{"summary": {"total_issues": 0, "closed_issues": 0}}'
        return '{"issues": []}'

    adapter = BrAdapter(runner=runner)

    await adapter.get_status("/srv/projects/remote-project", host=host)

    assert captured_hosts == [host, host]
