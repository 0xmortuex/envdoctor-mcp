"""Type guessing and shape checks.

Everything here takes a value and returns a *description* of it (a type word, a
yes/no, a fixed message). No function returns, slices or quotes any part of a value.
Careful: exceptions raised while parsing a value (``urlsplit``, ``int``) can embed
the value in their text, so their messages are never used.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

_BOOLS = {"true", "false", "yes", "no", "on", "off", "y", "n", "1", "0"}
_BOOL_STRICT = {"true", "false", "yes", "no", "on", "off"}
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")
_DSN_SCHEMES = {
    "postgres", "postgresql", "mysql", "mariadb", "mongodb", "mongodb+srv", "redis",
    "rediss", "amqp", "amqps", "sqlite", "mssql", "sqlserver", "oracle", "cockroachdb",
    "clickhouse", "kafka", "nats", "memcached",
}  # fmt: skip
_JWT = re.compile(r"^eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$")
_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_PLACEHOLDER = re.compile(
    r"^(change[-_ ]?me.*|replace[-_ ]?me.*|your[-_ ].*|<.*>|x{3,}|\*{3,}|\.{3}|todo.*|tbd|fixme.*"
    r"|placeholder.*|insert[-_ ].*|put[-_ ].*|enter[-_ ].*|add[-_ ].*here|.*[-_ ]here"
    r"|dummy|fake|example|sample|test[-_]?(key|token|secret)|secret|password|xxx.*)$",
    re.IGNORECASE,
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}

_SECRET_NAME = re.compile(
    r"(SECRET|PASSWORD|PASSWD|PASSPHRASE|PRIVATE|TOKEN|CREDENTIAL|API_?KEY|ACCESS_?KEY|"
    r"SERVICE_ROLE|SIGNING|SALT|AUTH_KEY|ENCRYPTION_KEY|DATABASE_URL|_DSN$|WEBHOOK_URL)",
    re.IGNORECASE,
)
_NOT_SECRET = re.compile(r"(PUBLISHABLE|ANON|PUBLIC_KEY|SITE_KEY|CLIENT_ID|APP_ID|TOKEN_URL|TOKEN_TTL)", re.I)

# (framework, prefix): variables with this prefix are inlined into browser bundles.
CLIENT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Vite", "VITE_"),
    ("Next.js", "NEXT_PUBLIC_"),
    ("Create React App", "REACT_APP_"),
    ("Nuxt", "NUXT_PUBLIC_"),
    ("Expo", "EXPO_PUBLIC_"),
    ("Astro/SvelteKit", "PUBLIC_"),
    ("Gatsby", "GATSBY_"),
)


def is_secret_name(name: str) -> bool:
    return _SECRET_NAME.search(name) is not None and _NOT_SECRET.search(name) is None


def client_exposure(name: str) -> tuple[str, str] | None:
    """(framework, prefix) when ``name`` would be shipped to browsers."""
    for framework, prefix in CLIENT_PREFIXES:
        if name.startswith(prefix):
            return framework, prefix
    return None


def exposure_warning(name: str) -> str | None:
    hit = client_exposure(name)
    if hit and is_secret_name(name):
        framework, prefix = hit
        return (
            f"{framework} inlines every {prefix}* variable into the browser bundle, and the "
            "name looks like a secret. Remove the prefix and read it on the server only."
        )
    return None


def is_placeholder(value: str) -> bool:
    return bool(value) and _PLACEHOLDER.match(value.strip()) is not None


def _url_host(value: str) -> str | None:
    try:
        return urlsplit(value).hostname
    except ValueError:
        return None


def points_at_localhost(value: str) -> bool:
    if not _SCHEME.match(value):
        return False
    host = _url_host(value)
    if host is None:
        # urlsplit rejects some DSNs (multi-host lists); look at the authority text.
        authority = value.split("://", 1)[1].split("/", 1)[0].rsplit("@", 1)[-1]
        host = authority.rsplit(":", 1)[0].strip("[]")
    return host.lower() in _LOCAL_HOSTS


def expected_type(name: str) -> str | None:
    """What the *name* promises about the value, or None."""
    n = name.upper()
    if n.endswith("_PORT") or n == "PORT":
        return "port"
    if n.endswith(("_URL", "_URI", "_DSN", "_ENDPOINT")) or n in {"DSN", "URL"}:
        return "url"
    if n.endswith(("_EMAIL", "_MAIL_FROM")) or n in {"EMAIL", "MAIL_FROM"}:
        return "email"
    if n.startswith(("ENABLE_", "DISABLE_", "IS_", "USE_", "ALLOW_", "HAS_")) or n.endswith(
        ("_ENABLED", "_DISABLED", "_DEBUG")
    ) or n in {"DEBUG", "VERBOSE"}:
        return "bool"
    if n.endswith(
        ("_TIMEOUT", "_COUNT", "_SIZE", "_LIMIT", "_TTL", "_MAX", "_MIN", "_SECONDS", "_MS",
         "_RETRIES", "_INTERVAL", "_WORKERS", "_CONNECTIONS")
    ):  # fmt: skip
        return "int"
    if n.endswith(("_PATH", "_DIR", "_FILE", "_FOLDER")):
        return "path"
    if n.endswith("_JWT") or n in {"JWT", "JWT_TOKEN"}:
        return "jwt"
    return None


def guess_type(name: str, value: str | None) -> str:
    """One word describing the value: empty bool int float port url dsn email jwt uuid json path string."""
    if value is None or value == "":
        return "empty"
    v = value.strip()
    low = v.lower()
    exp = expected_type(name)
    if low in _BOOL_STRICT or (low in ("0", "1") and exp == "bool"):
        return "bool"
    if _INT.match(v):
        if exp == "port" and 1 <= int(v) <= 65535:
            return "port"
        return "int"
    if _FLOAT.match(v):
        return "float"
    if _JWT.match(v):
        return "jwt"
    if _UUID.match(v):
        return "uuid"
    sm = _SCHEME.match(v)
    if sm:
        return "dsn" if sm.group(1).lower() in _DSN_SCHEMES else "url"
    if _EMAIL.match(v):
        return "email"
    if v[:1] in "{[":
        try:
            json.loads(v)
            return "json"
        except ValueError:
            pass
    if v.startswith(("/", "./", "../", "~", ".\\", "..\\")) or _WIN_PATH.match(v):
        return "path"
    return "string"


def type_family(t: str) -> str:
    if t in ("int", "port", "float"):
        return "number"
    if t in ("url", "dsn"):
        return "url"
    return t


def check_value(
    name: str, value: str, base_dir: str, check_paths: bool
) -> list[tuple[str, str, str]]:
    """Shape problems for one non-empty value: (severity, code, message). No value text."""
    problems: list[tuple[str, str, str]] = []
    exp = expected_type(name)
    got = guess_type(name, value)
    v = value.strip()
    if exp == "url":
        if got not in ("url", "dsn"):
            problems.append(("warning", "bad-url", f"name says URL but the value looks like {got} "
                             "(a URL needs a scheme such as https://)"))
        else:
            bad = False
            try:
                parts = urlsplit(v)
                _ = parts.port
                if not parts.netloc and parts.scheme not in ("sqlite", "file", "unix"):
                    bad = True
            except ValueError:
                bad = True
            if " " in v:
                bad = True
            if bad:
                problems.append(("warning", "bad-url", "URL does not parse (check host, port and spaces)"))
    elif exp == "port":
        if not _INT.match(v):
            problems.append(("error", "bad-port", f"name says port but the value looks like {got}"))
        elif not 1 <= int(v) <= 65535:
            problems.append(("error", "bad-port", "port is outside 1-65535"))
    elif exp == "bool":
        if v.lower() not in _BOOLS:
            problems.append(("warning", "bad-bool", f"name says boolean but the value looks like {got} "
                             "(use true/false)"))
    elif exp == "int":
        if not _INT.match(v):
            problems.append(("warning", "bad-int", f"name says integer but the value looks like {got}"))
    elif exp == "email":
        if got != "email":
            problems.append(("warning", "bad-email", f"name says email but the value looks like {got}"))
    elif exp == "jwt":
        if got != "jwt":
            problems.append(("warning", "bad-jwt", f"name says JWT but the value looks like {got}"))
    elif exp == "path" and check_paths:
        if "$" not in v and not v.startswith("~"):
            full = v if os.path.isabs(v) or _WIN_PATH.match(v) else os.path.join(base_dir, v)
            if not os.path.exists(full):
                problems.append(("warning", "path-missing", "path does not exist on this machine "
                                 "(relative paths are resolved from the env file's folder)"))
    return problems
