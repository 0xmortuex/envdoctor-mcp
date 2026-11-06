"""No value from a planted .env may appear in any output or error."""

from __future__ import annotations

from pathlib import Path

import pytest

from envdoctor_mcp import analysis, server
from envdoctor_mcp.errors import EnvDoctorError

from .conftest import PLANTED, git, write


@pytest.fixture(autouse=True)
def _sandbox_reset() -> None:
    saved = server._root
    yield  # type: ignore[misc]
    server._root = saved


def hostile_project(root: Path) -> Path:
    """Every awkward way a secret can sit in a file."""
    write(root, ".gitignore", ".env\n")
    write(root, ".env", (
        f"STRIPE_SECRET_KEY={PLANTED['stripe']}\n"
        f"DATABASE_URL=postgres://admin:{PLANTED['dbpass']}@db.internal:5432/app\n"
        f"JWT={PLANTED['jwt']}\n"
        f"GITHUB_TOKEN='{PLANTED['token']}'\n"
        f"SESSION=\"{PLANTED['session']}\"\n"
        f"API_ENDPOINT=https://{PLANTED['api_host']}/v1\n"
        f"BAD_PORT={PLANTED['session']}\n"
        f"BAD_URL=http://host:{PLANTED['stripe']}\n"
        f"WITH_SPACE={PLANTED['session']} and more\n"
        f"TRAILING={PLANTED['token']}   \n"
        f"QUOTED_LITERAL=\"'{PLANTED['stripe']}'\"\n"
        f"ADMIN_EMAIL={PLANTED['dbpass']}\n"
        f"DEBUG={PLANTED['dbpass']}\n"
        f"REQUEST_TIMEOUT={PLANTED['dbpass']}\n"
        f"CONFIG_FILE={PLANTED['dbpass']}\n"
        f"SESSION_JWT={PLANTED['token']}\n"
        f"VITE_STRIPE_SECRET_KEY={PLANTED['stripe']}\n"
        f"DUP={PLANTED['dbpass']}\n"
        f"DUP={PLANTED['token']}\n"
        f"REF=${{NOPE}}{PLANTED['session']}\n"
        "# OLD_KEY=" + PLANTED["commented"] + "\n"
    ))
    write(root, ".env.staging", (
        f"OK=1\n{PLANTED['garbage_line']}\n"
        f'K="x" {PLANTED["after_quote"]}\n'
        f"{PLANTED['garbage_line'].split()[3]}\n"
    ))
    write(root, ".env.test", f"A=1\nB='{PLANTED['unterminated']}\nmore {PLANTED['unterminated']}\n")
    write(root, ".env.production", f"DB_PASSWORD={PLANTED['prod_db']}\nDB_URL=postgres://u:{PLANTED['prod_db']}@localhost/db\n")
    write(root, ".env.example", (
        f"API_SECRET={PLANTED['token']}\nDATABASE_URL=postgres://u:{PLANTED['dbpass']}@h/db\nOTHER=\n"
    ))
    write(root, "app.py", "import os\nos.environ['STRIPE_SECRET_KEY']\nos.environ['DATABASE_URL']\nos.environ['NEW']\n")
    write(root, "web/src/a.ts", "import.meta.env.VITE_STRIPE_SECRET_KEY\n")
    write(root, "docker-compose.yml", f"services:\n  a:\n    environment:\n      - TOKEN_X={PLANTED['token']}\n      - OTHER=${{OTHER}}\n")
    write(root, ".github/workflows/ci.yml", f"env:\n  CI_SECRET: {PLANTED['stripe']}\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-f", ".env.production", ".gitignore")
    git(root, "commit", "-q", "-m", "init")
    return root


def all_outputs(root: Path) -> list[tuple[str, str]]:
    server.set_root(str(root))
    outs: list[tuple[str, str]] = []
    for name, call in [
        ("env_overview", lambda: server.env_overview()),
        ("compare_env", lambda: server.compare_env()),
        ("compare_env(file)", lambda: server.compare_env(file=".env", against=".env.example")),
        ("compare_env(staging)", lambda: server.compare_env(file=".env.staging")),
        ("find_usage", lambda: server.find_usage()),
        ("validate_env", lambda: server.validate_env()),
        ("validate_env(each)", lambda: "\n".join(server.validate_env(file=f) for f in
                                                  (".env", ".env.staging", ".env.test", ".env.production", ".env.example"))),
        ("check_gitignore", lambda: server.check_gitignore()),
        ("generate_example", lambda: server.generate_example()),
    ]:
        outs.append((name, call()))
    errors: list[str] = []
    for bad in (
        lambda: server.validate_env(file="nope"),
        lambda: server.validate_env(file=".env.nonexistent"),
        lambda: server.compare_env(file="app.py"),
        lambda: server.env_overview(".env"),
        lambda: server.env_overview(".."),
    ):
        with pytest.raises(EnvDoctorError) as ei:
            bad()
        errors.append(str(ei.value))
    outs.append(("errors", "\n".join(errors)))
    return outs


def test_no_planted_value_in_any_output(tmp_path: Path) -> None:
    root = hostile_project(tmp_path / "proj")
    outs = all_outputs(root)
    assert len(outs) == 10
    for tool_name, text in outs:
        assert text
        for key, value in PLANTED.items():
            assert value not in text, f"{key} leaked through {tool_name}"
        # also the distinctive fragments, in case a value was truncated or sliced
        for frag in ("PLANTED", "sk_live", "ghp_", "eyJhbG", "planted-host"):
            assert frag not in text, f"fragment {frag!r} leaked through {tool_name}"


def test_hostile_project_still_reports_the_problems(tmp_path: Path) -> None:
    """Redaction must not be achieved by saying nothing."""
    root = hostile_project(tmp_path / "proj")
    server.set_root(str(root))
    text = server.validate_env()
    for needle in (
        "STRIPE_SECRET_KEY",
        "line 7 BAD_PORT: bad-port",
        "line 8 BAD_URL: bad-url",
        "line 9 WITH_SPACE: unquoted-space",
        "line 10 TRAILING: trailing-whitespace",
        "line 11 QUOTED_LITERAL: quoted-literal",
        "line 17 VITE_STRIPE_SECRET_KEY: client-exposed-secret",
        "line 18 DUP: duplicate",
        "line 20 REF: undefined-reference",
        "line 2: syntax - unexpected text after the variable name",
        "line 3: syntax - unexpected text after the closing quote",
        "line 2: syntax - unterminated quoted value (runs to end of file)",
        "real-looking-secret-in-template",
        "localhost-in-production",
    ):
        assert needle in text, needle


def test_secrets_never_appear_in_stdout_or_stderr_of_a_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = hostile_project(tmp_path / "proj")
    all_outputs(root)
    captured = capsys.readouterr()
    for value in PLANTED.values():
        assert value not in captured.out and value not in captured.err


def test_guard_trips_when_a_value_would_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, ".env", f"API_SECRET={PLANTED['stripe']}\n")
    server.set_root(str(tmp_path))
    monkeypatch.setattr(analysis, "overview", lambda p: "oops " + PLANTED["stripe"])
    with pytest.raises(EnvDoctorError, match=r"redaction guard tripped for API_SECRET in \.env; output withheld"):
        server.env_overview()


def test_guard_error_does_not_contain_the_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, ".env", f"API_SECRET={PLANTED['stripe']}\n")
    server.set_root(str(tmp_path))
    monkeypatch.setattr(analysis, "validate", lambda p, f: PLANTED["stripe"])
    with pytest.raises(EnvDoctorError) as ei:
        server.validate_env()
    assert PLANTED["stripe"] not in str(ei.value)


def test_tools_write_nothing(tmp_path: Path) -> None:
    root = hostile_project(tmp_path / "proj")

    def snapshot() -> dict[str, tuple[int, int]]:
        return {
            p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*")
            if p.is_file() and ".git/" not in p.relative_to(root).as_posix() + "/"
            and not p.relative_to(root).as_posix().startswith(".git/")
        }

    before = snapshot()
    all_outputs(root)
    assert snapshot() == before
