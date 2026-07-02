from pathlib import Path

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


def test_does_not_descend_into_git_internals(tmp_path: Path) -> None:
    """`.git` directories can contain arbitrarily deep subdirectories --
    something inside `.git/` that would itself look like a `.beads` dir (or
    a nested git repo) must NOT be picked up as a separate candidate."""
    project = tmp_path / "repo"
    decoy = project / ".git" / "modules" / "some-submodule"
    (decoy / ".beads").mkdir(parents=True)

    refs = discover_projects(tmp_path)

    assert refs == [ProjectRef(path=str(project), beads_initialized=False)]
