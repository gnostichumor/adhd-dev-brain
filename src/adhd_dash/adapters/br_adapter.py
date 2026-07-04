"""Adapter for the Dicklesworthstone `br` (`beads_rust`) Beads CLI
(docs/architecture.md §1a).

Normalizes `br status --json` and `br list --format json` output into the
same `ProjectStatus` shape `BdAdapter` produces, so downstream
staleness/percent-complete logic never needs to know which CLI a tracked
project uses. See `bd_adapter.py`'s module docstring for the shared
`CommandRunner`-injection rationale and the "non-zero exit is a real bug,
not a routine absence of data" philosophy -- both apply identically here.

**Critical difference from `BdAdapter`: `br` has no `-C`/`--directory` global
flag** (confirmed empirically: `br -C <path> status --json` errors
`unexpected argument '-C' found`; `br --help` only exposes `--db <path>`, an
explicit database *file* path, not a project directory). Consequently:

- Local execution passes `path` as the subprocess's `cwd`, not as an
  appended CLI argument -- `br` auto-discovers `.beads/*.db` from the
  current directory the same way it would if invoked by hand from inside
  the project.
- Remote execution builds `cd {path} && {argv}` (both `shlex.quote`d) instead
  of appending `-C {path}` the way `BdAdapter`'s `_build_remote_command`
  does.
"""

import asyncio
import json
import shlex
from collections.abc import Awaitable, Callable
from datetime import datetime

import asyncssh

from adhd_dash.adapters.models import ProjectStatus
from adhd_dash.config import HostConfig

# Runs `argv` (e.g. ["br", "status", "--json"]) against the project at
# `path`, either locally (`host=None`) or over SSH (`host` given), and
# returns decoded stdout. Raises `RuntimeError` on non-zero exit.
CommandRunner = Callable[[HostConfig | None, str, list[str]], Awaitable[str]]


async def _run_local(path: str, argv: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"command {argv} (cwd={path}) exited {process.returncode}: "
            f"{stderr.decode(errors='replace')}"
        )
    return stdout.decode()


def _build_remote_command(argv: list[str], path: str) -> str:
    """Build the shell string asyncssh's `conn.run(str)` executes remotely.

    `br` has no `-C`/`--directory` flag (unlike `bd`), so the project
    directory can't be appended as a CLI argument the way
    `bd_adapter._build_remote_command` does -- it must be `cd`'d into
    instead. Every token, including `path` inside the `cd`, must be
    `shlex.quote`d: an unquoted path with a shell separator (e.g.
    `; rm -rf /`) would let the remote shell run a second, attacker-chosen
    command. Pulled out as its own function so this quoting can be tested
    directly, independently of the SSH transport -- see
    `bd_adapter._build_remote_command`'s sibling PR, which shipped exactly
    this bug in its first version and had to fix it.
    """
    quoted_path = shlex.quote(path)
    quoted_argv = " ".join(shlex.quote(arg) for arg in argv)
    return f"cd {quoted_path} && {quoted_argv}"


async def _run_remote(host: HostConfig, path: str, argv: list[str]) -> str:
    command = _build_remote_command(argv, path)
    # known_hosts intentionally omitted: asyncssh then verifies against the
    # system's default known_hosts file. Even on a Tailscale-only network,
    # skipping host-key verification (known_hosts=None) would accept any
    # host key silently -- a real MITM/host-reuse risk, not just a formality.
    async with asyncssh.connect(
        host.ssh_host,
        username=host.ssh_user,
        client_keys=[host.ssh_key_path],
    ) as conn:
        result = await conn.run(command)
        if result.exit_status != 0:
            raise RuntimeError(
                f"command {command!r} on {host.name} exited {result.exit_status}: {result.stderr!r}"
            )
        return str(result.stdout)


async def _default_runner(host: HostConfig | None, path: str, argv: list[str]) -> str:
    if host is None:
        return await _run_local(path, argv)
    return await _run_remote(host, path, argv)


class BrAdapter:
    """Reads `ProjectStatus` from a `.beads`-tracked project via the `br` CLI.

    The command runner is injectable via `runner` -- tests supply a fake
    that returns canned JSON keyed on `argv`, so no real subprocess or SSH
    connection is ever made. If omitted, a real runner is used: local
    projects (`host=None`) run `br` via a subprocess with `path` as `cwd`
    (see module docstring for why -- `br` has no `-C` flag); remote projects
    (`host` a `HostConfig`) run it over `asyncssh` via `cd <path> && ...`.
    """

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or _default_runner

    async def get_status(self, path: str, host: HostConfig | None = None) -> ProjectStatus:
        """Return the current `ProjectStatus` for the `br`-tracked project at `path`.

        Two `br` invocations, both required:

        - `br status --json` for `summary.total_issues`/`summary.closed_issues`.
          These field names are confirmed identical to `bd`'s (verified
          against a live `br` v0.2.15 install). Same facet-overlap caveat as
          `BdAdapter`: `br`'s per-status counts (`open_issues`,
          `blocked_issues`, etc.) are facets, not a partition, so they must
          never be summed as a denominator. `percent_complete` is
          `closed_issues / total_issues`, guarded to `None` when
          `total_issues == 0` (PRD R18: no issues means no defined
          completion percentage, not 0%).
        - `br list --format json --all --sort updated_at --limit 1` for the
          single most-recently-updated issue's `updated_at`, which becomes
          `last_beads_activity_at`. Unlike `bd list --json` (a bare array),
          `br list --format json`'s response is a wrapper object
          (`{"issues": [...], "total", "limit", "offset", "has_more"}`) --
          `.issues` must be unwrapped before indexing. `--all` is required:
          verified empirically that without it, `br list` defaults to
          excluding closed issues entirely (would silently under-report
          activity on projects where the most recent update was a close).
          `--sort updated_at` defaults to descending (most-recent-first) --
          verified empirically against a live install (without `--reverse`,
          the first two results were the two newest `updated_at` values;
          with `--reverse`, the first result was the oldest), the same
          convention `bd`'s `--sort updated` uses. `--reverse` must NOT be
          passed, or `--limit 1` would return the oldest issue instead. An
          empty `.issues` (zero issues) yields `last_beads_activity_at =
          None`.

        `br`'s timestamps are confirmed `Z`-suffixed ISO8601 with
        microsecond precision (e.g. `"2026-06-15T04:58:18.381241Z"`),
        parseable directly by `datetime.fromisoformat` on Python 3.12 into a
        timezone-aware UTC `datetime` -- no separate normalization step
        needed, unlike the hedge this field's docstring carried before this
        was verified against a live install (see `models.py`).
        """
        status_raw = await self._runner(host, path, ["br", "status", "--json"])
        summary = json.loads(status_raw)["summary"]
        total_issues: int = summary["total_issues"]
        closed_issues: int = summary["closed_issues"]

        list_raw = await self._runner(
            host,
            path,
            ["br", "list", "--format", "json", "--all", "--sort", "updated_at", "--limit", "1"],
        )
        issues = json.loads(list_raw)["issues"]

        last_beads_activity_at: datetime | None = None
        if issues:
            last_beads_activity_at = datetime.fromisoformat(issues[0]["updated_at"])

        percent_complete = closed_issues / total_issues if total_issues else None

        return ProjectStatus(
            percent_complete=percent_complete,
            last_beads_activity_at=last_beads_activity_at,
            last_github_activity_at=None,
            total_issues=total_issues,
            closed_issues=closed_issues,
        )
