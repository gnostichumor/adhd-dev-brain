"""Local filesystem scan for candidate tracked projects.

See docs/architecture.md and PRD R2: a project is a candidate if EITHER a
`.beads/` directory exists OR it's a plain git checkout (has a `.git` entry)
with no Beads initialized. `.beads` takes precedence when both are present.

This is deliberately a *local, filesystem-only* concern -- scanning a remote
Tailscale host's filesystem is a separate mechanism-of-execution concern
(mirroring how `BdAdapter`/`BrAdapter` separate "what to run" from "run it
locally or over SSH" via an injectable runner), out of scope for this issue.

"Git repo" here means a normal checkout identified by a `.git` entry
(directory or file -- git worktrees/submodules use a `.git` *file* containing
a `gitdir:` pointer). It does NOT mean a git-technical `--bare` repository:
a bare repo has no `.git` subdirectory at all, so detecting one would require
checking for `HEAD`/`objects`/`refs` at a directory's top level -- which
false-positives on every `.git/` directory itself. That detection is
intentionally not implemented here.
"""

from pathlib import Path

from pydantic import BaseModel

_METADATA_DIR_NAMES = frozenset({".git", ".beads"})


class ProjectRef(BaseModel):
    """A pre-persistence discovery candidate.

    Deliberately minimal -- this is not the eventual `TrackedProject` DB row
    (`adhd_dash.models`). No `host` field: discovery is local-filesystem-only
    for this issue: see module docstring.
    """

    path: str
    beads_initialized: bool


def discover_projects(root: Path) -> list[ProjectRef]:
    """Recursively walk `root`, returning a `ProjectRef` for every directory
    that has a `.beads/` subdirectory or a `.git` entry (directory or file).

    Nested projects are detected and returned separately -- recursion
    continues into a matched directory's other children (just not into its
    own `.git`/`.beads` entries, which are metadata, not candidate
    subdirectories). Symlinks are not followed, to avoid cycles. Results are
    sorted by `path` for deterministic test output.

    An unreadable subdirectory (`PermissionError`/`OSError` -- real home and
    dev directory trees routinely contain a few, e.g. restricted caches or
    other-user directories) is skipped, not fatal: this is an expected,
    routine absence for a broad recursive scan, the same way `GithubClient`
    treats a 404 as "no data" rather than a bug (see `github_client.py`'s
    module docstring) -- not the "real misconfiguration" class of failure
    `BdAdapter`/`BrAdapter` raise on. Everything else already found,
    including inside the unreadable subtree's siblings, is still returned.
    """
    results: list[ProjectRef] = []
    _walk(root, results)
    return sorted(results, key=lambda ref: ref.path)


def _walk(directory: Path, results: list[ProjectRef]) -> None:
    try:
        has_beads = (directory / ".beads").is_dir()
        has_git = (directory / ".git").exists()
    except OSError:
        return

    if has_beads:
        results.append(ProjectRef(path=str(directory), beads_initialized=True))
    elif has_git:
        results.append(ProjectRef(path=str(directory), beads_initialized=False))

    try:
        children = list(directory.iterdir())
    except OSError:
        # Directory is a confirmed match (or not) above, but its contents
        # aren't listable (e.g. execute-but-not-read permission) -- the
        # match already recorded still stands, we just can't recurse further.
        return

    for child in children:
        if not child.is_dir() or child.is_symlink():
            continue
        if child.name in _METADATA_DIR_NAMES:
            continue
        _walk(child, results)
