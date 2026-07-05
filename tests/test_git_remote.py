import subprocess
from pathlib import Path

import pytest

from adhd_dash.git_remote import (
    get_github_owner_repo,
    get_origin_remote_url,
    parse_github_owner_repo,
)

# --- parse_github_owner_repo (pure string parsing) --------------------------


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        pytest.param(
            "https://github.com/octocat/hello-world.git",
            ("octocat", "hello-world"),
            id="https_with_dot_git",
        ),
        pytest.param(
            "https://github.com/octocat/hello-world",
            ("octocat", "hello-world"),
            id="https_without_dot_git",
        ),
        pytest.param(
            "https://github.com/octocat/hello-world/",
            ("octocat", "hello-world"),
            id="https_trailing_slash",
        ),
        pytest.param(
            "git@github.com:octocat/hello-world.git",
            ("octocat", "hello-world"),
            id="scp_style_with_dot_git",
        ),
        pytest.param(
            "git@github.com:octocat/hello-world",
            ("octocat", "hello-world"),
            id="scp_style_without_dot_git",
        ),
        pytest.param(
            "ssh://git@github.com/octocat/hello-world.git",
            ("octocat", "hello-world"),
            id="ssh_scheme",
        ),
        pytest.param(
            "git@GitHub.com:octocat/hello-world.git",
            ("octocat", "hello-world"),
            id="scp_style_mixed_case_host",
        ),
        pytest.param(
            "https://gitlab.com/octocat/hello-world.git",
            None,
            id="non_github_host_https",
        ),
        pytest.param(
            "git@gitlab.com:octocat/hello-world.git",
            None,
            id="non_github_host_scp_style",
        ),
        pytest.param(
            "not a url at all",
            None,
            id="garbage_non_url_input",
        ),
        pytest.param(
            "https://github.com/octocat",
            None,
            id="missing_repo_segment",
        ),
        pytest.param(
            "https://github.com/",
            None,
            id="missing_owner_and_repo",
        ),
    ],
)
def test_parse_github_owner_repo(remote_url: str, expected: tuple[str, str] | None) -> None:
    assert parse_github_owner_repo(remote_url) == expected


# --- get_origin_remote_url / get_github_owner_repo (real git repos) --------


def init_repo(path: Path, origin_url: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin_url],
            cwd=path,
            check=True,
        )


async def test_get_origin_remote_url_returns_configured_remote(tmp_path: Path) -> None:
    init_repo(tmp_path, "https://github.com/octocat/hello-world.git")

    remote_url = await get_origin_remote_url(str(tmp_path))

    assert remote_url == "https://github.com/octocat/hello-world.git"


async def test_get_origin_remote_url_no_origin_remote_returns_none(tmp_path: Path) -> None:
    init_repo(tmp_path)

    remote_url = await get_origin_remote_url(str(tmp_path))

    assert remote_url is None


async def test_get_origin_remote_url_not_a_git_repo_returns_none(tmp_path: Path) -> None:
    remote_url = await get_origin_remote_url(str(tmp_path))

    assert remote_url is None


async def test_get_github_owner_repo_resolves_from_real_repo(tmp_path: Path) -> None:
    init_repo(tmp_path, "https://github.com/octocat/hello-world.git")

    owner_repo = await get_github_owner_repo(str(tmp_path))

    assert owner_repo == ("octocat", "hello-world")


async def test_get_github_owner_repo_no_origin_remote_returns_none(tmp_path: Path) -> None:
    init_repo(tmp_path)

    owner_repo = await get_github_owner_repo(str(tmp_path))

    assert owner_repo is None


async def test_get_github_owner_repo_not_a_git_repo_returns_none(tmp_path: Path) -> None:
    owner_repo = await get_github_owner_repo(str(tmp_path))

    assert owner_repo is None


async def test_get_github_owner_repo_non_github_origin_returns_none(tmp_path: Path) -> None:
    init_repo(tmp_path, "https://gitlab.com/octocat/hello-world.git")

    assert (
        await get_origin_remote_url(str(tmp_path)) == "https://gitlab.com/octocat/hello-world.git"
    )
    assert await get_github_owner_repo(str(tmp_path)) is None
