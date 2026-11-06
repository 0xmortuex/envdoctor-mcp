"""envdoctor-mcp: diagnose environment-variable configuration without ever showing a value."""

from __future__ import annotations

import argparse
import functools
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import analysis, gitcheck, shapes
from .errors import EnvDoctorError
from .scan import Project, load_project

mcp = MCPServer(
    "envdoctor",
    instructions=(
        "Diagnose environment-variable problems in a project. Read-only, and values are never "
        "shown: you get variable names, line numbers, lengths and type guesses. Start with "
        "env_overview, then compare_env (.env vs .env.example), find_usage (which variables the "
        "code reads, and which are undocumented), validate_env (placeholders, bad URLs/ports, "
        "whitespace and quoting mistakes, secrets exposed to the browser), check_gitignore "
        "(is .env ignored, tracked, or in history) and generate_example (a safe .env.example "
        "proposal as text). All paths are relative to the server's --dir sandbox."
    ),
)

_root: Path = Path.cwd().resolve()

F = TypeVar("F", bound=Callable[..., Any])


def set_root(path: str) -> None:
    global _root
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise EnvDoctorError(f"--dir {path!r} is not a directory")
    _root = p


def tool(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EnvDoctorError as e:
            raise ToolError(str(e)) from e
        except PermissionError as e:
            raise ToolError(f"permission denied reading {e.filename and Path(str(e.filename)).name}") from e
        except OSError as e:
            raise ToolError(f"file system error: {type(e).__name__}") from e

    mcp.tool()(wrapper)
    return fn


def _resolve(path: str, must_be_dir: bool = True, label: str | None = None) -> Path:
    """Resolve ``path`` inside the sandbox or refuse. ``label`` is what errors call it."""
    shown = label if label is not None else path
    p = Path(path) if os.path.isabs(path) else _root / path
    try:
        resolved = p.resolve()
        resolved.relative_to(_root)
    except (ValueError, OSError):
        raise EnvDoctorError(f"{shown!r} is outside the sandbox folder {_root.name!r} (set with --dir)") from None
    if not resolved.exists():
        raise EnvDoctorError(f"{shown!r} does not exist inside the sandbox folder {_root.name!r}")
    if must_be_dir and not resolved.is_dir():
        raise EnvDoctorError(f"{shown!r} is not a folder")
    return resolved


def _load(path: str) -> Project:
    scan = _resolve(path)
    return load_project(_root, scan)


def _file_rel(p: Project, file: str | None) -> str | None:
    """Turn a file argument (relative to the scanned folder) into a sandbox-relative path."""
    if not file:
        return None
    full = _resolve(file if os.path.isabs(file) else str(p.scan / file), must_be_dir=False, label=file)
    return p.rel(full)


def _guard(text: str, p: Project) -> str:
    """Last line of defence: refuse to return text containing a secret-looking value."""
    for f in p.all_env_files():
        for e in f.parsed.entries:
            if e.value and len(e.value) >= 6 and shapes.is_secret_name(e.name) and e.value in text:
                raise EnvDoctorError(
                    f"internal redaction guard tripped for {e.name} in {f.rel}; output withheld. "
                    "This is a bug in envdoctor-mcp: please report it (without the value)."
                )
    return text


@tool
def env_overview(path: str = ".") -> str:
    """Map every environment-variable source under a folder. Finds .env, .env.local,
    .env.<environment>, templates (.env.example/.sample/.template), docker-compose
    `environment:`/`env_file:`, and CI workflow `env:` blocks; groups them by env root (a
    monorepo has several) and reports variable counts, duplicates, syntax problems, BOM/CRLF,
    and which framework client prefixes (VITE_, NEXT_PUBLIC_, ...) are in use. Never shows
    values. Start here.

    path: folder to scan, relative to the server's --dir sandbox (default: the whole sandbox).
    """
    p = _load(path)
    return _guard(analysis.overview(p), p)


@tool
def compare_env(path: str = ".", file: str | None = None, against: str | None = None) -> str:
    """Compare .env with its template (.env.example/.sample/.template) in every env root.
    Reports names missing from .env, extra names the template does not document, variables
    empty in .env but required by the template (a `# optional` comment above a template line
    exempts it), duplicated names, and values whose type differs from the template's.
    Commented-out template lines (`# NAME=`) count as documented.

    path: folder to scan. file / against: compare two specific env files instead (paths
    relative to `path`), for example file=".env.production", against=".env.example".
    """
    p = _load(path)
    return _guard(analysis.compare(p, _file_rel(p, file), _file_rel(p, against)), p)


@tool
def find_usage(path: str = ".") -> str:
    """Scan source code for environment reads and cross-check them with the templates.
    Understands process.env.X / process.env["X"] / destructuring, import.meta.env.X,
    Deno.env.get, os.environ[...] / os.environ.get / os.getenv, ENV['X'] / ENV.fetch,
    std::env::var / env!, os.Getenv / os.LookupEnv, System.getenv, Environment.GetEnvironmentVariable,
    getenv / $_ENV / Laravel env(), System.get_env, Prisma env(), and ${X} in compose files.
    Reports variables used in code but undocumented in the template (with file:line), documented
    but never read, and client-exposed secret-looking names. Ambient variables (PATH, HOME,
    NODE_ENV, GITHUB_*) are ignored. It is a text scan: reads inside comments count, dynamic
    names (process.env[name]) are invisible.

    path: folder to scan, relative to the sandbox.
    """
    p = _load(path)
    return _guard(analysis.usage_report(p), p)


@tool
def validate_env(path: str = ".", file: str | None = None) -> str:
    """Check env files for mistakes, from names and shapes only (no value is ever printed).
    Flags: placeholders (changeme, your_key_here), empty values, trailing whitespace, unquoted
    spaces, CRLF, a BOM, values wrapped in literal quotes, undefined ${REF}s, duplicates, syntax
    errors; names that promise a type but disagree (*_URL not a URL, *_PORT out of range, booleans,
    integers, emails, JWTs, *_PATH that does not exist); localhost URLs in production files;
    real-looking secrets inside a template; and secret-looking names with a browser-exposed
    prefix (VITE_, NEXT_PUBLIC_, REACT_APP_, ...).

    path: folder to scan. file: validate only this env file (relative to `path`).
    """
    p = _load(path)
    return _guard(analysis.validate(p, _file_rel(p, file)), p)


@tool
def check_gitignore(path: str = ".") -> str:
    """Is each real env file safe from git? For every .env/.env.* file (templates excluded) says
    whether it is ignored, tracked, or present in any commit on any branch, and lists env files
    that were committed once and deleted (still in history). Uses `git check-ignore`,
    `git ls-files` and `git log --all` by file name only: no content is read, no history is
    rewritten. Also flags a template that is itself ignored. Requires git on PATH.

    path: folder inside the repository to check.
    """
    p = _load(path)
    return _guard(gitcheck.check(p), p)


@tool
def generate_example(path: str = ".") -> str:
    """Propose a safe .env.example as text (one section per env root). It lists every variable
    seen in env files, templates and source code, each with a type hint, whether it looks
    secret, where the code reads it, and a warning if a secret would be exposed to the browser.
    Every line is `NAME=` with nothing after the equals sign: no value, not even from your
    templates. Nothing is written to disk; paste or save the text yourself.

    path: folder to scan.
    """
    p = _load(path)
    return _guard(analysis.generate_example(p), p)


def main() -> None:
    parser = argparse.ArgumentParser(prog="envdoctor-mcp", description=__doc__)
    parser.add_argument(
        "--dir",
        default=".",
        help="sandbox folder: every tool reads only inside it (default: the current directory)",
    )
    args = parser.parse_args()
    try:
        set_root(args.dir)
    except EnvDoctorError as e:
        parser.error(str(e))
    mcp.run()


if __name__ == "__main__":
    main()
