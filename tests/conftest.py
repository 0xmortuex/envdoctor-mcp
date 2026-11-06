"""Shared fixtures: realistic temp projects with planted fake secrets.

Every planted value below is fake and unique, so a leak anywhere in any output is
detectable with a plain substring search.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PLANTED = {
    "stripe": "sk_live_PLANTED_stripe_4eC39HqLyjWDarjtT1zdp7dc",
    "dbpass": "PLANTEDpw_Zx81qLmN3vB",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.PLANTEDpayloadQ2xhdWRl.PLANTEDsig_7YhGfDs",
    "token": "ghp_PLANTEDtoken0123456789abcdefABCDEF",
    "session": "PLANTED-session-secret-value-9981",
    "api_host": "internal-api.planted-host.example",
    "garbage_line": "this line has PLANTED_GARBAGE_SECRET_5521 and is not an assignment",
    "unterminated": "PLANTED_UNTERMINATED_8842",
    "after_quote": "PLANTED_AFTER_QUOTE_3310",
    "commented": "PLANTED_COMMENTED_OUT_6673",
    "prod_db": "PLANTEDprodpw_Qq7733",
}


def write(root: Path, rel: str, text: str) -> Path:
    """Write UTF-8 with exactly the newlines in ``text`` (no translation)."""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))
    return p


def git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }  # fmt: skip
    res = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, env=env, check=True,
    )  # fmt: skip
    return res.stdout


@pytest.fixture
def monorepo(tmp_path: Path) -> Path:
    """A Vite web app + an Express api + compose + CI, with planted secrets everywhere."""
    web = tmp_path / "apps" / "web"
    api = tmp_path / "apps" / "api"
    write(web, ".env", (
        "VITE_API_URL=https://api.acme.test\n"
        f"VITE_STRIPE_SECRET_KEY={PLANTED['stripe']}\n"
        "PORT=99999\n"
        "DEBUG=maybe\n"
        f"DATABASE_URL=postgres://admin:{PLANTED['dbpass']}@localhost:5432/app\n"
        "API_TOKEN=changeme\n"
        "EMPTY_ONE=\n"
        "VITE_API_URL=https://dup.acme.test\n"
    ))
    write(web, ".env.example", (
        "VITE_API_URL=\n"
        "VITE_STRIPE_SECRET_KEY=\n"
        "PORT=3000\n"
        "DEBUG=false\n"
        "DATABASE_URL=\n"
        "API_TOKEN=\n"
        "EMPTY_ONE=\n"
        "# optional: only for the admin build\n"
        "ADMIN_URL=\n"
        "# COMMENTED_ONLY=\n"
        "UNUSED_VAR=\n"
    ))
    write(web, "src/main.ts", (
        "const api = import.meta.env.VITE_API_URL;\n"
        "const k = import.meta.env.VITE_STRIPE_SECRET_KEY;\n"
        "const flag = import.meta.env['VITE_NEW_FLAG'];\n"
        "console.log(import.meta.env.MODE, process.env.NODE_ENV);\n"
    ))
    write(api, ".env", (
        f"JWT_SECRET={PLANTED['jwt']}\n"
        f"SESSION_SECRET='{PLANTED['session']}'\n"
        "PORT=4000\n"
        "REDIS_URL=redis://localhost:6379\n"
    ))
    write(api, ".env.production", (
        "PORT=8080\n"
        f"DATABASE_URL=postgres://app:{PLANTED['prod_db']}@localhost:5432/prod\n"
        "REDIS_URL=redis://cache.internal:6379\n"
        "UPLOAD_DIR=/definitely/not/a/real/dir\n"
    ))
    write(api, ".env.example", "JWT_SECRET=\nSESSION_SECRET=\nPORT=\nREDIS_URL=\n")
    write(api, "src/server.js", (
        "const { PORT, REDIS_URL, ...rest } = process.env;\n"
        "const s = process.env.JWT_SECRET;\n"
        "const t = process.env['SESSION_SECRET'];\n"
        "const u = process.env.STRIPE_WEBHOOK_SECRET;\n"
        "const w = process.env.GITHUB_SHA;\n"
    ))
    write(tmp_path, "docker-compose.yml", (
        "services:\n"
        "  api:\n"
        "    image: acme/api\n"
        "    environment:\n"
        "      - PORT=4000\n"
        "      - REDIS_URL=${REDIS_URL}\n"
        "      - FROM_COMPOSE\n"
        "    env_file:\n"
        "      - apps/api/.env\n"
        "  web:\n"
        "    environment:\n"
        "      VITE_API_URL: ${VITE_API_URL:-http://x}\n"
    ))
    write(tmp_path, ".github/workflows/ci.yml", (
        "name: ci\n"
        "env:\n"
        "  CI_FLAG: '1'\n"
        "jobs:\n"
        "  t:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo hi\n"
        "        env:\n"
        "          DATABASE_URL: ${{ secrets.DATABASE_URL }}\n"
    ))
    return tmp_path
