"""Tests for `adhd_dash.staleness.evaluate_project` (adhd-dash-oui.3, PRD R18).

`ProjectStatus.is_stale`'s own combination table (both-fresh, both-stale,
either-signal-stale, boundary cases) is already covered by
`tests/test_project_status_staleness.py` -- these tests instead cover the
bridging logic in `staleness.py` itself: the `.beads/`-directory guard, and
deriving/consuming the GitHub signal via `git_remote.get_github_owner_repo`
and `GithubClient.get_latest_commit_activity`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import adhd_dash.staleness as staleness
from adhd_dash.adapters.models import CannotEvaluateStalenessError
from adhd_dash.github_client import GithubClient
from adhd_dash.models import TrackedProject
from adhd_dash.staleness import BeadsNotSupportedError, evaluate_project

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)
THRESHOLD_DAYS = 14

COMMIT_JSON = [
    {
        "sha": "abc123",
        "commit": {
            "committer": {"date": "2026-06-30T12:34:56Z"},
        },
    }
]


def make_github_client(handler: Callable[[httpx.Request], httpx.Response]) -> GithubClient:
    """Copied from tests/test_github_client.py's make_client() helper -- a
    GithubClient backed by an httpx.MockTransport so no real network access
    is needed."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://api.github.com")
    return GithubClient(token=None, check_ttl_minutes=60, client=http_client)


async def test_evaluate_project_raises_beads_not_supported_when_beads_dir_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".beads").mkdir()
    project = TrackedProject(host="local", path=str(tmp_path))

    async def _boom(path: str) -> tuple[str, str] | None:
        raise AssertionError("get_github_owner_repo must not be called when .beads/ is present")

    monkeypatch.setattr(staleness, "get_github_owner_repo", _boom)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the GitHub client must never be hit when .beads/ is present")

    client = make_github_client(handler)

    with pytest.raises(BeadsNotSupportedError):
        await evaluate_project(project, client, threshold_days=THRESHOLD_DAYS, now=NOW)


async def test_evaluate_project_evaluated_fresh_github_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = TrackedProject(host="local", path=str(tmp_path))

    async def _fake_owner_repo(path: str) -> tuple[str, str] | None:
        return ("octocat", "hello-world")

    monkeypatch.setattr(staleness, "get_github_owner_repo", _fake_owner_repo)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/commits"
        return httpx.Response(200, json=COMMIT_JSON)

    client = make_github_client(handler)

    result = await evaluate_project(project, client, threshold_days=THRESHOLD_DAYS, now=NOW)

    assert result.is_stale is False
    assert result.last_github_activity_at == datetime(2026, 6, 30, 12, 34, 56, tzinfo=UTC)
    assert result.percent_complete is None
    assert result.last_beads_activity_at is None


async def test_evaluate_project_evaluated_stale_github_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = TrackedProject(host="local", path=str(tmp_path))

    async def _fake_owner_repo(path: str) -> tuple[str, str] | None:
        return ("octocat", "hello-world")

    monkeypatch.setattr(staleness, "get_github_owner_repo", _fake_owner_repo)

    stale_commit_json = [
        {
            "sha": "def456",
            "commit": {
                "committer": {"date": "2026-01-01T00:00:00Z"},
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=stale_commit_json)

    client = make_github_client(handler)

    result = await evaluate_project(project, client, threshold_days=THRESHOLD_DAYS, now=NOW)

    assert result.is_stale is True
    assert result.last_github_activity_at == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


async def test_evaluate_project_no_derivable_github_repo_raises_cannot_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = TrackedProject(host="local", path=str(tmp_path))

    async def _no_remote(path: str) -> tuple[str, str] | None:
        return None

    monkeypatch.setattr(staleness, "get_github_owner_repo", _no_remote)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no owner/repo derivable -- the GitHub client must never be called")

    client = make_github_client(handler)

    with pytest.raises(CannotEvaluateStalenessError):
        await evaluate_project(project, client, threshold_days=THRESHOLD_DAYS, now=NOW)


async def test_evaluate_project_remote_present_but_client_returns_none_raises_cannot_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The important "collapse case": a derivable owner/repo does NOT imply
    the project is evaluable.

    `get_github_owner_repo` only tells us a remote URL parses as a GitHub
    remote -- it says nothing about whether that repo is actually reachable
    (private, deleted, empty, rate-limited, etc.). `GithubClient` resolves
    all of those to `None` (see `_fetch_latest_commit_activity`), and with
    no Beads signal ever populated by this module, that `None` must still
    propagate all the way to `CannotEvaluateStalenessError`. A future
    maintainer could easily assume "has a derivable owner/repo" is
    sufficient to guarantee an evaluable project and skip handling this
    path -- this test pins down that it is not.
    """
    project = TrackedProject(host="local", path=str(tmp_path))

    async def _fake_owner_repo(path: str) -> tuple[str, str] | None:
        return ("octocat", "hello-world")

    monkeypatch.setattr(staleness, "get_github_owner_repo", _fake_owner_repo)

    def handler(request: httpx.Request) -> httpx.Response:
        # Matches github_client.py's "private/unreachable repo" case: any
        # non-200 status resolves get_latest_commit_activity to None.
        return httpx.Response(404, json={"message": "Not Found"})

    client = make_github_client(handler)

    with pytest.raises(CannotEvaluateStalenessError):
        await evaluate_project(project, client, threshold_days=THRESHOLD_DAYS, now=NOW)
