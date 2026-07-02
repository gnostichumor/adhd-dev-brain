"""Adapter for the gastownhall `bd` Beads CLI (docs/architecture.md §1).

Normalizes `bd status --json` and `bd list --json` output into
`ProjectStatus`. A project may be local (bare filesystem path, `host=None`)
or on a remote Tailscale host reachable via `asyncssh` (`host: HostConfig`,
see `adhd_dash.config`) -- `BdAdapter` runs the same two `bd` invocations
either way, just over a different transport.

The command-invocation step is injected as a `CommandRunner`, mirroring how
`GithubClient` accepts an injectable `httpx.AsyncClient` -- tests supply a
fake runner keyed on `argv` so no real subprocess or SSH connection is ever
made. If no runner is given, a real one is built that shells out locally via
`asyncio.create_subprocess_exec` or remotely via `asyncssh.connect`.

Unlike `GithubClient`, a non-zero exit from `bd` is not treated as an
expected "signal unavailable" outcome -- it's raised as `RuntimeError`. A
`bd status --json` failure on a project this dashboard is supposedly
tracking indicates a real bug or misconfiguration (wrong path, `bd` not
installed, corrupt `.beads/`), not a routine absence of data the way a 404
from GitHub is.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime

import asyncssh

from adhd_dash.adapters.models import ProjectStatus
from adhd_dash.config import HostConfig

# Runs `argv` (e.g. ["bd", "status", "--json"]) against the project at
# `path`, either locally (`host=None`) or over SSH (`host` given), and
# returns decoded stdout. Raises `RuntimeError` on non-zero exit.
CommandRunner = Callable[[HostConfig | None, str, list[str]], Awaitable[str]]


async def _run_local(path: str, argv: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv,
        "-C",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"command {argv} -C {path} exited {process.returncode}: "
            f"{stderr.decode(errors='replace')}"
        )
    return stdout.decode()


async def _run_remote(host: HostConfig, path: str, argv: list[str]) -> str:
    command = " ".join(argv) + f" -C {path}"
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


class BdAdapter:
    """Reads `ProjectStatus` from a `.beads`-tracked project via the `bd` CLI.

    The command runner is injectable via `runner` -- tests supply a fake
    that returns canned JSON keyed on `argv`, so no real subprocess or SSH
    connection is ever made. If omitted, a real runner is used: local
    projects (`host=None`) run `bd` via a subprocess; remote projects
    (`host` a `HostConfig`) run it over `asyncssh`.
    """

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or _default_runner

    async def get_status(self, path: str, host: HostConfig | None = None) -> ProjectStatus:
        """Return the current `ProjectStatus` for the `bd`-tracked project at `path`.

        Two `bd` invocations, both required:

        - `bd status --json` for `summary.total_issues`/`summary.closed_issues`.
          These are the only two summary fields used -- `bd`'s per-status
          counts (`open_issues`, `blocked_issues`, etc.) are facets, not a
          partition (an issue can be both `open` and `blocked`
          simultaneously), so they must never be summed as a denominator.
          `percent_complete` is `closed_issues / total_issues`, guarded to
          `None` when `total_issues == 0` (PRD R18: no issues means no
          defined completion percentage, not 0%).
        - `bd list --json --all --sort updated --limit 1` for the single
          most-recently-updated issue's `updated_at`, which becomes
          `last_beads_activity_at`. `--sort updated` defaults to
          descending (most-recent-first) -- `--reverse` must NOT be passed,
          or `--limit 1` would return the oldest issue instead. `bd` bumps
          `updated_at` on close too, so this one field captures
          create/update/close activity without a separate query. An empty
          result (zero issues) yields `last_beads_activity_at = None`.
        """
        status_raw = await self._runner(host, path, ["bd", "status", "--json"])
        summary = json.loads(status_raw)["summary"]
        total_issues: int = summary["total_issues"]
        closed_issues: int = summary["closed_issues"]

        list_raw = await self._runner(
            host, path, ["bd", "list", "--json", "--all", "--sort", "updated", "--limit", "1"]
        )
        latest = json.loads(list_raw)

        last_beads_activity_at: datetime | None = None
        if latest:
            last_beads_activity_at = datetime.fromisoformat(latest[0]["updated_at"])

        percent_complete = closed_issues / total_issues if total_issues else None

        return ProjectStatus(
            percent_complete=percent_complete,
            last_beads_activity_at=last_beads_activity_at,
            total_issues=total_issues,
            closed_issues=closed_issues,
        )
