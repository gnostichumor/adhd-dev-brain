from pathlib import Path

import pytest

from adhd_dash.discovery import ProjectRef, discover_projects


def test_beads_dir_present(tmp_path: Path) -> None:
    project = tmp_path / "with-beads"
    (project / ".beads").mkdir(parents=True)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=True)]


def test_git_repo_as_directory_no_beads(tmp_path: Path) -> None:
    project = tmp_path / "plain-git"
    (project / ".git").mkdir(parents=True)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=False)]


def test_git_repo_as_file_no_beads(tmp_path: Path) -> None:
    """Git worktrees/submodules use a `.git` *file* containing a `gitdir:`
    pointer rather than a `.git` directory -- detection must not assume
    `.git` is always a directory."""
    project = tmp_path / "worktree-checkout"
    project.mkdir(parents=True)
    (project / ".git").write_text("gitdir: /some/other/place/.git/worktrees/worktree-checkout\n")

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=False)]


def test_neither_beads_nor_git_not_returned_but_children_still_scanned(tmp_path: Path) -> None:
    non_project = tmp_path / "just-a-folder"
    nested_project = non_project / "actual-project"
    (nested_project / ".beads").mkdir(parents=True)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(nested_project), beads_initialized=True)]


def test_both_beads_and_git_beads_takes_precedence(tmp_path: Path) -> None:
    project = tmp_path / "both"
    (project / ".beads").mkdir(parents=True)
    (project / ".git").mkdir(parents=True)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=True)]


def test_nested_projects_both_returned_separately(tmp_path: Path) -> None:
    """A project directory's own subtree can contain another matching
    directory -- both must appear in the returned list, not just the
    outermost one."""
    outer = tmp_path / "outer-git-project"
    inner = outer / "vendored" / "inner-beads-project"
    (outer / ".git").mkdir(parents=True)
    (inner / ".beads").mkdir(parents=True)

    refs = discover_projects(tmp_path)

    assert refs == sorted(
        [
            ProjectRef(path=str(outer), beads_initialized=False),
            ProjectRef(path=str(inner), beads_initialized=True),
        ],
        key=lambda r: r.path,
    )


def test_empty_root_returns_empty_list(tmp_path: Path) -> None:
    (tmp_path / "unrelated-dir").mkdir()

    refs = discover_projects(tmp_path)

    assert refs == []


def test_root_itself_matching_is_included(tmp_path: Path) -> None:
    (tmp_path / ".beads").mkdir()

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(tmp_path), beads_initialized=True)]


def test_symlink_cycle_does_not_cause_infinite_recursion(tmp_path: Path) -> None:
    """A symlink inside a project pointing back up to an ancestor directory
    is a genuine cycle -- the scan must terminate (not hang/recurse forever)
    and must not treat the cycle as producing extra/duplicate candidates."""
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "loop").symlink_to(tmp_path, target_is_directory=True)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=False)]


def test_unreadable_subdirectory_skipped_scan_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PermissionError (or other OSError) while listing one subdirectory's
    contents must not abort the whole scan or discard results already found
    elsewhere -- real home/dev directory trees routinely have a few
    unreadable directories (restricted caches, other-user dirs, mounts)."""
    good_project = tmp_path / "good-project"
    (good_project / ".beads").mkdir(parents=True)
    locked = tmp_path / "locked-dir"
    (locked / "hidden-project" / ".beads").mkdir(parents=True)

    original_iterdir = Path.iterdir

    def fake_iterdir(self: Path) -> list[Path]:
        if self == locked:
            raise PermissionError(f"Permission denied: {self}")
        return list(original_iterdir(self))

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    refs = discover_projects(tmp_path)

    # good_project is still found; hidden-project (only reachable by listing
    # locked/) is correctly NOT found, since listing locked/ raises.
    assert refs == [ProjectRef(path=str(good_project), beads_initialized=True)]


def test_project_dir_still_matched_even_if_its_own_contents_unlistable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory can be stat-able (enough to check for `.beads`/`.git` by
    name) while not being listable (e.g. execute-but-not-read permission) --
    the match itself must still be reported even though recursion into it
    can't proceed."""
    project = tmp_path / "project-with-unlistable-contents"
    (project / ".beads").mkdir(parents=True)

    original_iterdir = Path.iterdir

    def fake_iterdir(self: Path) -> list[Path]:
        if self == project:
            raise PermissionError(f"Permission denied: {self}")
        return list(original_iterdir(self))

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=True)]


def test_does_not_descend_into_git_internals(tmp_path: Path) -> None:
    """`.git` directories can contain arbitrarily deep subdirectories --
    something inside `.git/` that would itself look like a `.beads` dir (or
    a nested git repo) must NOT be picked up as a separate candidate."""
    project = tmp_path / "repo"
    decoy = project / ".git" / "modules" / "some-submodule"
    (decoy / ".beads").mkdir(parents=True)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=False)]
