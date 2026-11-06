"""The tools on realistic temp projects, asserting exact findings."""

from __future__ import annotations

from pathlib import Path

import pytest

from envdoctor_mcp import server
from envdoctor_mcp.errors import EnvDoctorError

from .conftest import PLANTED, write


@pytest.fixture(autouse=True)
def _sandbox_reset() -> None:
    saved = server._root
    yield  # type: ignore[misc]
    server._root = saved


def use(root: Path) -> None:
    server.set_root(str(root))


def test_overview_monorepo(monorepo: Path) -> None:
    use(monorepo)
    out = server.env_overview()
    assert out.splitlines() == [
        "Scanned .: 9 files seen, 2 env root(s).",
        "",
        "== env root: apps/api ==",
        "  .env  [real]  4 variable(s)",
        "  .env.example  [template]  4 variable(s)",
        "  .env.production  [real (production)]  4 variable(s)",
        "",
        "== env root: apps/web ==",
        "  .env  [real]  7 variable(s), 1 duplicated",
        "  .env.example  [template]  9 variable(s)",
        "",
        "== outside any env root ==",
        "  compose docker-compose.yml: environment names [FROM_COMPOSE, PORT, REDIS_URL, VITE_API_URL]",
        "  CI .github/workflows/ci.yml: env names [CI_FLAG, DATABASE_URL]",
        "",
        "Framework client prefixes in use: Vite (VITE_*): 3 name(s)",
    ]


def test_overview_scoped_to_subfolder(monorepo: Path) -> None:
    use(monorepo)
    out = server.env_overview("apps/web")
    assert "apps/api" not in out and "== env root: apps/web ==" in out


def test_compare_monorepo_exact(monorepo: Path) -> None:
    use(monorepo)
    assert server.compare_env().splitlines() == [
        "== apps/api/.env vs apps/api/.env.example ==",
        "Result: in sync.",
        "",
        "== apps/web/.env vs apps/web/.env.example ==",
        "Missing from apps/web/.env (2): ADMIN_URL (template line 9, optional), UNUSED_VAR (template line 11)",
        "Empty in apps/web/.env but required by the template (1): EMPTY_ONE (line 7)",
        "Duplicated in apps/web/.env: VITE_API_URL on lines 1, 8 (the last one wins)",
        "Type differs: DEBUG: template looks like bool, apps/web/.env looks like string",
        "Result: 5 difference(s).",
    ]


def test_compare_extra_and_specific_files(monorepo: Path) -> None:
    use(monorepo)
    out = server.compare_env("apps/api", file=".env.production", against=".env.example")
    assert out.splitlines() == [
        "== apps/api/.env.production vs apps/api/.env.example ==",
        "Missing from apps/api/.env.production (2): JWT_SECRET (template line 1), SESSION_SECRET (template line 2)",
        "Extra in apps/api/.env.production, not in the template (2): DATABASE_URL (line 2), UPLOAD_DIR (line 4)",
        "Result: 4 difference(s).",
    ]


def test_compare_without_template_and_without_env(tmp_path: Path) -> None:
    write(tmp_path, "a/.env", "A=1\n")
    write(tmp_path, "b/.env.example", "B=\nC=\n")
    use(tmp_path)
    out = server.compare_env()
    assert "a: .env exists but there is no template to compare it against." in out
    assert "b: .env.example exists but there is no .env to compare (2 variable(s) to set up)." in out


def test_compare_errors_are_actionable(monorepo: Path) -> None:
    use(monorepo)
    with pytest.raises(EnvDoctorError, match="'nope.txt' does not exist inside the sandbox folder"):
        server.compare_env(file="nope.txt")
    write(monorepo, "notes.txt", "hi\n")
    with pytest.raises(EnvDoctorError, match="notes.txt is not an env file under the scanned folder"):
        server.compare_env(file="notes.txt")
    with pytest.raises(EnvDoctorError, match="against= needs file= as well"):
        server.compare_env(against="apps/web/.env.example")


def test_find_usage_monorepo_exact(monorepo: Path) -> None:
    use(monorepo)
    out = server.find_usage().splitlines()
    assert out[0] == "Scanned 2 source file(s), 1 compose file(s)."
    assert "  STRIPE_WEBHOOK_SECRET: apps/api/src/server.js:4 [defined nowhere]" in out
    assert "  VITE_NEW_FLAG: apps/web/src/main.ts:3 [defined nowhere]" in out
    assert not any(line.startswith("  MODE:") for line in out)
    assert "Ignored ambient variables: GITHUB_SHA" in out
    assert (
        "Documented but never read by scanned code (8): ADMIN_URL, API_TOKEN, COMMENTED_ONLY, "
        "DATABASE_URL, DEBUG, EMPTY_ONE, PORT, UNUSED_VAR"
    ) in out
    warn = [line for line in out if line.startswith("  WARNING")]
    assert len(warn) == 1 and warn[0].startswith("  WARNING VITE_STRIPE_SECRET_KEY (read at apps/web/src/main.ts:2)")
    # PORT / REDIS_URL are read through destructuring and are documented, so they are not listed.
    assert not any(line.startswith("  PORT:") or line.startswith("  REDIS_URL: apps") for line in out)


def test_find_usage_no_template_and_defined_elsewhere(tmp_path: Path) -> None:
    write(tmp_path, ".env", "FROM_ENV=1\n")
    write(tmp_path, "app.py", "import os\na = os.environ['FROM_ENV']\nb = os.getenv('FROM_COMPOSE')\nc = os.environ['NOWHERE']\n")
    write(tmp_path, "docker-compose.yml", "services:\n  a:\n    environment:\n      - FROM_COMPOSE=1\n")
    use(tmp_path)
    out = server.find_usage().splitlines()
    assert "No .env.example/.sample/.template here, so every variable the code reads counts as undocumented." in out
    assert "  FROM_COMPOSE: app.py:3, docker-compose.yml:4 [defined in docker-compose.yml]" not in out  # compose has no ${}
    assert "  FROM_COMPOSE: app.py:3 [defined in docker-compose.yml]" in out
    assert "  FROM_ENV: app.py:2 [set in a real env file]" in out
    assert "  NOWHERE: app.py:4 [defined nowhere]" in out


def test_validate_exact_findings(monorepo: Path) -> None:
    use(monorepo)
    out = server.validate_env("apps/web", file=".env").splitlines()
    assert out[0] == "== apps/web/.env [real, 7 variable(s)] =="
    body = [
        "  [warning] line 1 VITE_API_URL: duplicate - defined again on line 8; this earlier value is ignored",
        "  [error] line 2 VITE_STRIPE_SECRET_KEY: client-exposed-secret - Vite inlines every VITE_* variable into the "
        "browser bundle, and the name looks like a secret. Remove the prefix and read it on the server only.",
        "  [error] line 3 PORT: bad-port - port is outside 1-65535",
        "  [warning] line 4 DEBUG: bad-bool - name says boolean but the value looks like string (use true/false)",
        "  [warning] line 6 API_TOKEN: placeholder - value looks like a placeholder (length 8) that was never replaced",
        "  [info] line 7 EMPTY_ONE: empty - empty value",
    ]
    assert out[1:7] == body
    assert out[-1].startswith("6 finding(s) in 1 file(s).")


def test_validate_production_and_template(monorepo: Path) -> None:
    use(monorepo)
    prod = server.validate_env("apps/api", file=".env.production")
    assert (
        "  [error] line 2 DATABASE_URL: localhost-in-production - production file points at localhost/127.0.0.1/0.0.0.0"
    ) in prod
    # production files are not checked for local path existence
    assert "path-missing" not in prod


def test_validate_text_mistakes(tmp_path: Path) -> None:
    content = (
        chr(0xFEFF) + "A=hello world\r\n"
        "B=trailing   \r\n"
        "C=\"'quoted'\"\r\n"
        "D=${NEVER_DEFINED}/x\r\n"
        "E\r\n"
        "F=ok\r\n"
        "this is junk\r\n"
    )
    write(tmp_path, ".env", content)
    use(tmp_path)
    assert server.validate_env().splitlines()[:-2] == [
        "== .env [real, 5 variable(s)] ==",
        "  [warning] line 1: bom - file starts with a UTF-8 BOM; some loaders glue it onto the first variable name",
        "  [info] file: crlf - 7 line(s) end with CRLF; shell `source` and docker --env-file can keep the "
        "\\r as part of the value",
        "  [error] line 5: syntax - no '=' after the name (a bare word is not an assignment)",
        "  [error] line 7: syntax - unexpected text after the variable name",
        "  [warning] line 1 A: unquoted-space - unquoted value contains a space; quote it (shell `source` would split it)",
        "  [warning] line 2 B: trailing-whitespace - unquoted value ends with spaces/tabs; some loaders keep them",
        "  [warning] line 3 C: quoted-literal - value itself starts and ends with quote characters, so the quotes are part of it",
        "  [warning] line 4 D: undefined-reference - references ${NEVER_DEFINED}, which is not defined earlier in this file",
    ]


def test_validate_template_with_real_looking_secret(tmp_path: Path) -> None:
    write(tmp_path, ".env.example", f"API_SECRET={PLANTED['token']}\nOTHER_TOKEN=changeme\nPORT=3000\n")
    use(tmp_path)
    out = server.validate_env().splitlines()
    assert out[1] == (
        "  [error] line 1 API_SECRET: real-looking-secret-in-template - template holds a non-placeholder "
        f"value (length {len(PLANTED['token'])}) for a secret-looking name; templates get committed"
    )
    assert out[2:] == ["", "1 finding(s) in 1 file(s). Values are never shown; only names, line numbers and lengths."]
    assert PLANTED["token"] not in "\n".join(out)


def test_validate_path_exists_and_next_public(tmp_path: Path) -> None:
    write(tmp_path, "data/cfg.json", "{}")
    write(tmp_path, ".env.local", "CONFIG_FILE=data/cfg.json\nLOG_DIR=nope/logs\nNEXT_PUBLIC_DB_PASSWORD=x1\n")
    use(tmp_path)
    out = server.validate_env()
    assert "line 1 CONFIG_FILE" not in out
    assert "line 2 LOG_DIR: path-missing" in out
    assert "line 3 NEXT_PUBLIC_DB_PASSWORD: client-exposed-secret - Next.js inlines every NEXT_PUBLIC_*" in out


def test_generate_example_exact(tmp_path: Path) -> None:
    write(tmp_path, ".env", f"PORT=3000\nDEBUG=true\nAPI_URL=https://x.test\nDB_PASSWORD={PLANTED['dbpass']}\n")
    write(tmp_path, ".env.example", "# optional: extra\nPORT=\n")
    write(tmp_path, "app.py", "import os\nos.environ['DB_PASSWORD']\nos.environ['NEW_ONE']\nos.environ['NEW_ONE']\n")
    use(tmp_path)
    assert server.generate_example().splitlines() == [
        "# .env.example for .",
        "# Names and type hints only. Fill in real values in your own .env (never commit it).",
        "",
        "# PORT: port - an integer from 1 to 65535 (optional)",
        "# PORT=",
        "",
        "# API_URL: url - a URL such as https://host:port/path",
        "API_URL=",
        "",
        "# DB_PASSWORD: string",
        "# secret: keep the real value out of git",
        "# read at app.py:2",
        "DB_PASSWORD=",
        "",
        "# DEBUG: bool - true or false",
        "DEBUG=",
        "",
        "# NEW_ONE: string",
        "# read at app.py:3 (+1 more)",
        "NEW_ONE=",
    ]
    # nothing is written
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env", ".env.example", "app.py"]


def test_sandbox_blocks_escape_and_missing(tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    write(inner, ".env", "A=1\n")
    write(tmp_path, ".env", "OUTSIDE=1\n")
    use(inner)
    with pytest.raises(EnvDoctorError, match="outside the sandbox folder"):
        server.env_overview("..")
    with pytest.raises(EnvDoctorError, match="outside the sandbox folder"):
        server.env_overview(str(tmp_path))
    with pytest.raises(EnvDoctorError, match="does not exist"):
        server.env_overview("nope")
    with pytest.raises(EnvDoctorError, match="is not a folder"):
        server.env_overview(".env")
    with pytest.raises(EnvDoctorError, match="outside the sandbox folder"):
        server.validate_env(file="../.env")
    assert "OUTSIDE" not in server.env_overview()


def test_symlink_escape_is_blocked(tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    outside = tmp_path / "outside"
    write(outside, "x.env", "OUTSIDE=1\n")
    inner.mkdir()
    try:
        (inner / "link.env").symlink_to(outside / "x.env")
        (inner / "linkdir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    use(inner)
    out = server.env_overview()
    assert "OUTSIDE" not in out and "link.env  [" not in out
    assert "skipped link.env: symlink points outside the sandbox" in out
    assert "skipped symlinked folder linkdir" in out
    with pytest.raises(EnvDoctorError, match="outside the sandbox folder"):
        server.env_overview("linkdir")


def test_empty_project(tmp_path: Path) -> None:
    use(tmp_path)
    assert "No .env files were found" in server.env_overview()
    assert server.validate_env() == "No .env files found to validate."
    assert server.find_usage() == "No env files and no environment reads found."
    assert server.generate_example().startswith("Nothing to generate from")
