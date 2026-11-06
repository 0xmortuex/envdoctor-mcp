"""check_gitignore against real git repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from envdoctor_mcp import server

from .conftest import git, write


@pytest.fixture(autouse=True)
def _sandbox_reset() -> None:
    saved = server._root
    yield  # type: ignore[misc]
    server._root = saved


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


def test_mixed_repo_exact_findings(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo, ".gitignore", ".env\n.env.production\n")
    write(repo, ".env", "SECRET_A=PLANTED_GIT_A_1\n")
    write(repo, ".env.example", "SECRET_A=\n")
    write(repo, ".env.local", "SECRET_B=PLANTED_GIT_B_2\n")  # not ignored, untracked
    write(repo, ".env.production", "SECRET_C=PLANTED_GIT_C_3\n")  # ignored but force-added
    write(repo, "old/.env", "SECRET_D=PLANTED_GIT_D_4\n")  # committed then deleted
    git(repo, "add", ".gitignore", ".env.example")
    git(repo, "add", "-f", "old/.env")
    git(repo, "add", "-f", ".env.production")
    git(repo, "commit", "-q", "-m", "initial")
    (repo / "old" / ".env").unlink()
    (repo / "old").rmdir()
    git(repo, "commit", "-q", "-am", "remove old env")
    server.set_root(str(repo))
    lines = server.check_gitignore().splitlines()
    assert lines[0].startswith("git repository: repo (checking env files by name only")
    assert lines[1:7] == [
        "  .env: ignored and untracked",
        "  .env.local: untracked but NOT ignored: one `git add .` away from being committed",
        "  .env.production: TRACKED by git (matches an ignore pattern, but ignoring does not untrack it); "
        "appears in 1 commit(s) across all branches",
        "Real env files that were committed once and are gone from the working tree (still in history):",
        "  old/.env: in 2 commit(s). Treat every secret in it as leaked and rotate it.",
        "",
    ]
    assert lines[7].startswith("3 problem(s).")
    assert "PLANTED" not in "\n".join(lines)


def test_clean_repo(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo, ".gitignore", ".env\n.env.*\n!.env.example\n")
    write(repo, ".env", "A=1\n")
    write(repo, ".env.example", "A=\n")
    git(repo, "add", ".gitignore", ".env.example")
    git(repo, "commit", "-q", "-m", "init")
    server.set_root(str(repo))
    out = server.check_gitignore()
    assert "  .env: ignored and untracked" in out
    assert out.endswith("All real env files are ignored, untracked and absent from history.")


def test_ignored_template_is_flagged(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo, ".gitignore", ".env*\n")
    write(repo, ".env", "A=1\n")
    write(repo, ".env.example", "A=\n")
    server.set_root(str(repo))
    assert "  .env.example: template is IGNORED, so teammates will not get it" in server.check_gitignore()


def test_subfolder_and_history_scope(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(repo, "svc/a/.env", "A=1\n")
    write(repo, "svc/b/.env", "B=1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x")
    server.set_root(str(repo))
    out = server.check_gitignore("svc/a")
    assert "  svc/a/.env: TRACKED by git; appears in 1 commit(s) across all branches" in out
    assert "svc/b" not in out


def test_not_a_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    plain = tmp_path / "plain"
    write(plain, ".env", "A=1\n")
    server.set_root(str(plain))
    assert server.check_gitignore().startswith(". is not inside a git repository")


def test_git_missing_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from envdoctor_mcp.errors import EnvDoctorError

    write(tmp_path, ".env", "A=1\n")
    server.set_root(str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(EnvDoctorError, match="git was not found on PATH"):
        server.check_gitignore()
