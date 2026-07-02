from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import time_machine

from adhd_dash.github_client import GithubClient, Release

RELEASE_JSON = {
    "tag_name": "v1.2.3",
    "html_url": "https://github.com/octocat/hello-world/releases/tag/v1.2.3",
}

COMMIT_JSON = [
    {
        "sha": "abc123",
        "commit": {
            "committer": {"date": "2026-06-30T12:34:56Z"},
        },
    }
]


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    token: str | None = None,
    check_ttl_minutes: int = 60,
) -> GithubClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://api.github.com")
    return GithubClient(token=token, check_ttl_minutes=check_ttl_minutes, client=http_client)


# --- get_latest_release --------------------------------------------------


async def test_get_latest_release_has_releases() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/releases/latest"
        return httpx.Response(200, json=RELEASE_JSON)

    client = make_client(handler)

    release = await client.get_latest_release("octocat", "hello-world")

    assert release == Release(
        tag="v1.2.3",
        html_url="https://github.com/octocat/hello-world/releases/tag/v1.2.3",
    )


async def test_get_latest_release_no_releases_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = make_client(handler)

    release = await client.get_latest_release("octocat", "hello-world")

    assert release is None


async def test_get_latest_release_private_repo_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    client = make_client(handler)

    release = await client.get_latest_release("octocat", "private-repo")

    assert release is None


async def test_get_latest_release_network_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)

    release = await client.get_latest_release("octocat", "unreachable")

    assert release is None


async def test_get_latest_release_invalid_owner_repo_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never hit the transport for an empty owner/repo")

    client = make_client(handler)

    assert await client.get_latest_release("", "") is None
    assert await client.get_latest_release("octocat", "") is None
    assert await client.get_latest_release("", "hello-world") is None


# --- get_latest_commit_activity ------------------------------------------


async def test_get_latest_commit_activity_has_commits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/commits"
        assert request.url.params["per_page"] == "1"
        return httpx.Response(200, json=COMMIT_JSON)

    client = make_client(handler)

    activity = await client.get_latest_commit_activity("octocat", "hello-world")

    assert activity == datetime(2026, 6, 30, 12, 34, 56, tzinfo=UTC)


async def test_get_latest_commit_activity_empty_repo_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "Git Repository is empty."})

    client = make_client(handler)

    activity = await client.get_latest_commit_activity("octocat", "empty-repo")

    assert activity is None


async def test_get_latest_commit_activity_private_repo_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = make_client(handler)

    activity = await client.get_latest_commit_activity("octocat", "private-repo")

    assert activity is None


async def test_get_latest_commit_activity_network_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)

    activity = await client.get_latest_commit_activity("octocat", "unreachable")

    assert activity is None


async def test_get_latest_commit_activity_invalid_owner_repo_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never hit the transport for an empty owner/repo")

    client = make_client(handler)

    assert await client.get_latest_commit_activity("", "") is None


# --- token handling --------------------------------------------------------


async def test_authorization_header_sent_when_token_configured() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=RELEASE_JSON)

    client = make_client(handler, token="ghp_supersecret")

    await client.get_latest_release("octocat", "hello-world")

    assert captured["authorization"] == "Bearer ghp_supersecret"


async def test_authorization_header_omitted_when_no_token() -> None:
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=RELEASE_JSON)

    client = make_client(handler, token="")

    await client.get_latest_release("octocat", "hello-world")

    assert captured["authorization"] is None


# --- TTL caching -------------------------------------------------------


async def test_get_latest_release_cached_within_ttl() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=RELEASE_JSON)

    client = make_client(handler, check_ttl_minutes=60)

    with time_machine.travel(datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC), tick=False) as traveler:
        first = await client.get_latest_release("octocat", "hello-world")
        traveler.shift(timedelta(minutes=30))
        second = await client.get_latest_release("octocat", "hello-world")

    assert first == second
    assert call_count == 1


async def test_get_latest_release_refetches_after_ttl_expires() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=RELEASE_JSON)

    client = make_client(handler, check_ttl_minutes=60)

    with time_machine.travel(datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC), tick=False) as traveler:
        await client.get_latest_release("octocat", "hello-world")
        traveler.shift(timedelta(minutes=61))
        await client.get_latest_release("octocat", "hello-world")

    assert call_count == 2


async def test_get_latest_commit_activity_cached_within_ttl() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=COMMIT_JSON)

    client = make_client(handler, check_ttl_minutes=60)

    with time_machine.travel(datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC), tick=False) as traveler:
        first = await client.get_latest_commit_activity("octocat", "hello-world")
        traveler.shift(timedelta(minutes=30))
        second = await client.get_latest_commit_activity("octocat", "hello-world")

    assert first == second
    assert call_count == 1


async def test_get_latest_commit_activity_refetches_after_ttl_expires() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=COMMIT_JSON)

    client = make_client(handler, check_ttl_minutes=60)

    with time_machine.travel(datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC), tick=False) as traveler:
        await client.get_latest_commit_activity("octocat", "hello-world")
        traveler.shift(timedelta(minutes=61))
        await client.get_latest_commit_activity("octocat", "hello-world")

    assert call_count == 2


async def test_release_and_commit_caches_are_independent() -> None:
    release_calls = 0
    commit_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal release_calls, commit_calls
        if "releases" in request.url.path:
            release_calls += 1
            return httpx.Response(200, json=RELEASE_JSON)
        commit_calls += 1
        return httpx.Response(200, json=COMMIT_JSON)

    client = make_client(handler, check_ttl_minutes=60)

    await client.get_latest_release("octocat", "hello-world")
    await client.get_latest_release("octocat", "hello-world")
    await client.get_latest_commit_activity("octocat", "hello-world")
    await client.get_latest_commit_activity("octocat", "hello-world")

    assert release_calls == 1
    assert commit_calls == 1


async def test_caches_are_independent_per_repo() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=RELEASE_JSON)

    client = make_client(handler, check_ttl_minutes=60)

    await client.get_latest_release("octocat", "repo-one")
    await client.get_latest_release("octocat", "repo-two")

    assert call_count == 2


async def test_no_release_result_is_cached_too() -> None:
    """A resolved None (no releases) must be cached like any other value --
    otherwise a repo with no releases would bypass the TTL and hit the
    transport on every call."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(404, json={"message": "Not Found"})

    client = make_client(handler, check_ttl_minutes=60)

    with time_machine.travel(datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC), tick=False) as traveler:
        first = await client.get_latest_release("octocat", "no-releases")
        traveler.shift(timedelta(minutes=30))
        second = await client.get_latest_release("octocat", "no-releases")

    assert first is None
    assert second is None
    assert call_count == 1
