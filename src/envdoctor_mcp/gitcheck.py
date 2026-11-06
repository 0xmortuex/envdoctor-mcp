"""Is each real env file safe from git? Answers from file names and commit hashes only."""

from __future__ import annotations

import os
import subprocess

from .errors import EnvDoctorError
from .scan import Project, classify_env_file

_TIMEOUT = 30
HISTORY_LIMIT = 25


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_PAGER": "cat"}
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            env=env,
        )
    except FileNotFoundError as e:
        raise EnvDoctorError("git was not found on PATH; install git to use check_gitignore") from e
    except subprocess.TimeoutExpired as e:
        raise EnvDoctorError(f"git did not answer within {_TIMEOUT}s; the repository may be too large to scan") from e


def _is_ignored(cwd: str, path: str) -> bool:
    res = _git(cwd, "check-ignore", "-q", "--no-index", "--", path)
    if res.returncode not in (0, 1):
        first = (res.stderr.strip().splitlines() or ["unknown error"])[0]
        raise EnvDoctorError(f"git check-ignore failed: {first}")
    return res.returncode == 0


def check(p: Project) -> str:
    top = _git(str(p.scan), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return (
            f"{p.rel(p.scan)} is not inside a git repository, so there is nothing to check yet.\n"
            "When you `git init`, make sure .env and .env.* (except templates) are in .gitignore first."
        )
    toplevel = os.path.realpath(top.stdout.strip())
    cwd = toplevel

    def top_rel(rel: str) -> str:
        return os.path.relpath(os.path.realpath(str(p.root / rel)), toplevel).replace("\\", "/")

    out = [f"git repository: {os.path.basename(toplevel)} (checking env files by name only; no content is read)"]
    real = [f for f in p.all_env_files() if f.kind.kind == "real"]
    templates = [f for f in p.all_env_files() if f.kind.kind == "template"]
    problems = 0
    if not real:
        out.append("No real env files in the working tree.")
    for f in real:
        full = top_rel(f.rel)
        ignored = _is_ignored(cwd, full)
        tracked = bool(_git(cwd, "ls-files", "--", full).stdout.strip())
        count = len([ln for ln in _git(cwd, "log", "--all", "--format=%h", "--", full).stdout.split() if ln])
        status: list[str] = []
        bad = False
        if tracked:
            status.append("TRACKED by git" + (" (matches an ignore pattern, but ignoring does not untrack it)" if ignored else ""))
            bad = True
        elif not ignored:
            status.append("untracked but NOT ignored: one `git add .` away from being committed")
            bad = True
        else:
            status.append("ignored and untracked")
        if count:
            status.append(f"appears in {count} commit(s) across all branches")
            bad = True
        problems += bad
        out.append(f"  {f.rel}: " + "; ".join(status))
    for f in templates:
        full = top_rel(f.rel)
        if _is_ignored(cwd, full):
            problems += 1
            out.append(f"  {f.rel}: template is IGNORED, so teammates will not get it")
    # Env files that were committed at some point and are gone from the working tree now.
    hist = _git(cwd, "log", "--all", "--diff-filter=A", "--name-only", "--pretty=format:")
    scan_rel = os.path.relpath(os.path.realpath(str(p.scan)), toplevel).replace("\\", "/")
    here = {top_rel(f.rel) for f in real}
    ever: list[str] = []
    for line in hist.stdout.splitlines():
        name = line.strip()
        if not name or name in ever:
            continue
        kind = classify_env_file(os.path.basename(name))
        inside = scan_rel == "." or name == scan_rel or name.startswith(scan_rel + "/")
        if kind and kind.kind == "real" and inside:
            ever.append(name)
    gone = [n for n in ever if n not in here]
    if gone:
        problems += len(gone)
        out.append("Real env files that were committed once and are gone from the working tree (still in history):")
        for n in gone[:HISTORY_LIMIT]:
            c = len(_git(cwd, "log", "--all", "--format=%h", "--", n).stdout.split())
            out.append(f"  {n}: in {c} commit(s). Treat every secret in it as leaked and rotate it.")
    if problems:
        out.append(
            f"\n{problems} problem(s). To stop tracking a file: `git rm --cached <file>` and add it to .gitignore. "
            "Removing it from history needs `git filter-repo`; rotating the secrets is the real fix."
        )
    else:
        out.append("\nAll real env files are ignored, untracked and absent from history.")
    out.extend(f"note: {n}" for n in p.notes)
    return "\n".join(out)
