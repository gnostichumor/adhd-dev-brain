"""GitHub REST API client (PRD R6, R17; see docs/architecture.md §1, §5).

Two responsibilities, same client: latest-Release lookup (R6, "release
detection") and latest-commit-on-default-branch lookup (R17, the second
staleness signal alongside Beads activity).

Per architecture.md §5, a project whose GitHub signal is unreachable/unknown
must fall back to Beads activity alone rather than blocking evaluation --
this client is what makes that fallback possible, so every failure mode
(404, 403, network error, malformed response) resolves to `None` rather than
raising. "No remote configured" is a caller-side concern: a project with no
GitHub remote URL simply never calls this client.
"""

from datetime import datetime

import httpx
from pydantic import BaseModel

_GITHUB_API_BASE_URL = "https://api.github.com"


class Release(BaseModel):
    """A GitHub Release: tag name + the web URL to view it."""

    tag: str
    html_url: str


class GithubClient:
    """Async, mockable client against the GitHub REST API.

    Constructed with the two fields already modeled on `GithubConfig`
    (`token`, `check_ttl_minutes`) -- callers pass `config.github.token` and
    `config.github.check_ttl_minutes` directly. An empty-string token is
    treated as "unauthenticated" (matches `config.yaml`'s shipped-blank
    convention for secret fields).

    An `httpx.AsyncClient` can be injected via `client` -- tests build one
    with `transport=httpx.MockTransport(...)` so no real network access or
    extra pinned test library is required. If omitted, a real client is
    built against the live GitHub API.
    """

    def __init__(
        self,
        token: str | None,
        check_ttl_minutes: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token or None
        self._check_ttl_minutes = check_ttl_minutes
        self._client = client or httpx.AsyncClient(base_url=_GITHUB_API_BASE_URL)

    async def aclose(self) -> None:
        """Close the underlying httpx client. Call during app shutdown."""
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def get_latest_release(self, owner: str, repo: str) -> Release | None:
        """Return the latest Release for `owner/repo`, or `None`.

        `None` covers every "no usable release" case identically: the repo
        has no releases (GitHub returns 404 for `.../releases/latest`, which
        is an expected, documented outcome -- not an error), the repo is
        private/unreachable (403/404), or a network error occurred. Callers
        (the future staleness evaluator) treat all of these the same way:
        this signal just isn't available.
        """
        if not owner or not repo:
            return None
        try:
            response = await self._client.get(
                f"/repos/{owner}/{repo}/releases/latest", headers=self._headers()
            )
        except httpx.RequestError:
            return None

        if response.status_code != 200:
            return None

        data = response.json()
        return Release(tag=data["tag_name"], html_url=data["html_url"])

    async def get_latest_commit_activity(self, owner: str, repo: str) -> datetime | None:
        """Return the timestamp of the latest commit on `owner/repo`'s default branch.

        Implementation choice: a single call to
        `GET /repos/{owner}/{repo}/commits?per_page=1` rather than two calls
        (fetch the repo for `default_branch`, then fetch that branch's
        commits). The commits endpoint defaults to the repo's default
        branch when no `sha`/branch is specified, so one request is
        sufficient and correct, and halves the request volume against
        GitHub's rate limit for a call site that will eventually run per
        tracked project on every poll cycle.

        Uses the commit's *committer* date (not author date) as the
        "activity" timestamp -- it reflects when the commit actually landed
        on the branch (e.g. via a merge/rebase), which is closer to "push
        activity" than the original authoring time.

        Returns `None` for: an empty repo (GitHub returns 409 for the
        commits endpoint, not an empty list -- handled the same as any
        other non-200), a private/unreachable repo (403/404), or a network
        error.
        """
        if not owner or not repo:
            return None
        try:
            response = await self._client.get(
                f"/repos/{owner}/{repo}/commits",
                params={"per_page": 1},
                headers=self._headers(),
            )
        except httpx.RequestError:
            return None

        if response.status_code != 200:
            return None

        data = response.json()
        if not data:
            return None

        date_str: str = data[0]["commit"]["committer"]["date"]
        return datetime.fromisoformat(date_str)
