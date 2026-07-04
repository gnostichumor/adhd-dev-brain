"""Derive a GitHub (owner, repo) pair from a local git checkout's `origin`
remote (PRD R18, adhd-dash-oui.3).

Local-filesystem-only, matching `discovery.py`/`polling.py`'s own documented
scope (architecture.md §6): this reads the git checkout at `path` via a
local subprocess, unconditionally -- it does not attempt anything over
`asyncssh` for a `TrackedProject` whose `host` points at a remote Tailscale
host. That is a real, currently-unaddressed limitation: for a
remote-hosted project, `path` describes a directory on the *remote* host,
and this module will either find nothing at that path locally (typical --
returns `None`) or, worse, find an unrelated local directory sharing the
path string. Extending this to SSH-executed `git remote get-url origin`
(mirroring `BdAdapter`/`BrAdapter`'s `CommandRunner` injection pattern) is a
follow-up, out of scope for this issue.

"No derivable owner/repo" (no git checkout, no `origin` remote, a remote
pointing at a non-GitHub host, `git` not installed) is treated as an
expected, routine absence -- returns `None`, never raises -- mirroring
`github_client.py`'s own "absence is None, not an error" philosophy.
"""

import asyncio
import re
from urllib.parse import urlparse

_GITHUB_HOSTS = frozenset({"github.com"})

# Matches the SCP-style form `git@github.com:owner/repo(.git)?` -- urlparse
# does not parse this as a URL (no scheme), so it needs its own regex path.
_SCP_STYLE_RE = re.compile(r"^git@(?P<host>[^:]+):(?P<owner>[^/]+)/(?P<repo>[^/]+?)(\.git)?/?$")


def parse_github_owner_repo(remote_url: str) -> tuple[str, str] | None:
    """Parse `(owner, repo)` out of a git remote URL, or `None` if it isn't
    a recognizable GitHub remote.

    Supports:
    - `https://github.com/owner/repo.git` / `https://github.com/owner/repo`
    - `git@github.com:owner/repo.git` / `git@github.com:owner/repo` (SCP-style)
    - `ssh://git@github.com/owner/repo.git`

    Returns `None` for a non-GitHub host, a malformed URL, or a URL missing
    an owner or repo segment. Pure string parsing, no I/O.
    """
    scp_match = _SCP_STYLE_RE.match(remote_url.strip())
    if scp_match:
        # Lowercase before comparing -- DNS hostnames are case-insensitive,
        # and `urlparse().hostname` below already lowercases automatically,
        # so a mixed-case SCP-style host (e.g. `git@GitHub.com:...`) must be
        # treated the same as its https-form equivalent.
        if scp_match.group("host").lower() not in _GITHUB_HOSTS:
            return None
        return scp_match.group("owner"), scp_match.group("repo")

    parsed = urlparse(remote_url.strip())
    if parsed.hostname not in _GITHUB_HOSTS:
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return None

    owner, repo = segments[0], segments[1]
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return None
    return owner, repo


async def get_origin_remote_url(path: str) -> str | None:
    """Run `git -C <path> remote get-url origin` and return its stdout
    (stripped), or `None` on any non-zero exit (no such repo, no `origin`
    remote, `git` not on PATH, etc.) -- all treated as routine absence.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            path,
            "remote",
            "get-url",
            "origin",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await process.communicate()
    except OSError:
        return None
    if process.returncode != 0:
        return None
    return stdout.decode().strip()


async def get_github_owner_repo(path: str) -> tuple[str, str] | None:
    """Convenience: `get_origin_remote_url` + `parse_github_owner_repo`."""
    remote_url = await get_origin_remote_url(path)
    if remote_url is None:
        return None
    return parse_github_owner_repo(remote_url)
