"""Type guessing and shape checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from envdoctor_mcp import shapes


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("X", "", "empty"),
        ("X", None, "empty"),
        ("X", "true", "bool"),
        ("X", "No", "bool"),
        ("ENABLE_FOO", "1", "bool"),
        ("X", "1", "int"),
        ("X", "-42", "int"),
        ("PORT", "8080", "port"),
        ("PORT", "99999", "int"),
        ("X", "3.14", "float"),
        ("X", "https://example.com/a?b=c", "url"),
        ("X", "postgres://u:p@h:5432/db", "dsn"),
        ("X", "redis://localhost:6379", "dsn"),
        ("X", "a@b.io", "email"),
        ("X", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc_-", "jwt"),
        ("X", "123e4567-e89b-12d3-a456-426614174000", "uuid"),
        ("X", '{"a": 1}', "json"),
        ("X", "{not json", "string"),
        ("X", "/etc/app.conf", "path"),
        ("X", "C:\\data\\x", "path"),
        ("X", "hello", "string"),
    ],
)
def test_guess_type(name: str, value: str | None, expected: str) -> None:
    assert shapes.guess_type(name, value) == expected


@pytest.mark.parametrize(
    "value",
    ["changeme", "CHANGE_ME", "your_api_key_here", "your-key", "<token>", "xxxx", "TODO", "replace_me", "placeholder"],
)
def test_placeholders_detected(value: str) -> None:
    assert shapes.is_placeholder(value)


@pytest.mark.parametrize("value", ["sk_live_abc123", "production", "3000", "https://api.acme.test"])
def test_real_values_are_not_placeholders(value: str) -> None:
    assert not shapes.is_placeholder(value)


def test_secret_names_and_exposure() -> None:
    assert shapes.is_secret_name("STRIPE_SECRET_KEY")
    assert shapes.is_secret_name("DATABASE_URL")
    assert shapes.is_secret_name("GITHUB_TOKEN")
    assert not shapes.is_secret_name("NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY")
    assert not shapes.is_secret_name("SUPABASE_ANON_KEY")
    assert not shapes.is_secret_name("PORT")
    assert shapes.exposure_warning("NEXT_PUBLIC_API_SECRET") is not None
    assert "Next.js" in (shapes.exposure_warning("NEXT_PUBLIC_API_SECRET") or "")
    assert shapes.exposure_warning("VITE_AUTH_TOKEN") is not None
    assert shapes.exposure_warning("REACT_APP_PASSWORD") is not None
    assert shapes.exposure_warning("NEXT_PUBLIC_SITE_NAME") is None
    assert shapes.exposure_warning("API_SECRET") is None


def test_localhost_detection() -> None:
    assert shapes.points_at_localhost("postgres://u:p@localhost:5432/db")
    assert shapes.points_at_localhost("http://127.0.0.1:3000")
    assert shapes.points_at_localhost("redis://0.0.0.0")
    assert not shapes.points_at_localhost("https://api.acme.test")
    assert not shapes.points_at_localhost("localhost")


def test_check_value_problems(tmp_path: Path) -> None:
    base = str(tmp_path)
    (tmp_path / "exists.txt").write_bytes(b"x")
    chk = shapes.check_value
    assert chk("API_URL", "https://a.test", base, True) == []
    assert chk("API_URL", "not a url", base, True)[0][1] == "bad-url"
    assert chk("API_URL", "http://host:abc/x", base, True)[0][1] == "bad-url"
    assert chk("PORT", "70000", base, True) == [("error", "bad-port", "port is outside 1-65535")]
    assert chk("DB_PORT", "five", base, True)[0][1] == "bad-port"
    assert chk("DEBUG", "maybe", base, True)[0][1] == "bad-bool"
    assert chk("DEBUG", "off", base, True) == []
    assert chk("REQUEST_TIMEOUT", "30s", base, True)[0][1] == "bad-int"
    assert chk("ADMIN_EMAIL", "nope", base, True)[0][1] == "bad-email"
    assert chk("SESSION_JWT", "abc", base, True)[0][1] == "bad-jwt"
    assert chk("DATA_FILE", "exists.txt", base, True) == []
    assert chk("DATA_FILE", "missing.txt", base, True)[0][1] == "path-missing"
    assert chk("DATA_FILE", "missing.txt", base, False) == []


def test_messages_never_contain_the_value(tmp_path: Path) -> None:
    secret = "PLANTED_SHAPE_VALUE_4412"
    for name in ("API_URL", "PORT", "DEBUG", "X_TIMEOUT", "A_EMAIL", "S_JWT", "D_FILE"):
        for _sev, _code, msg in shapes.check_value(name, secret, str(tmp_path), True):
            assert secret not in msg
    # urlsplit raises ValueError whose text embeds the value; it must not leak.
    for _sev, _code, msg in shapes.check_value("API_URL", "http://h:" + secret, str(tmp_path), True):
        assert secret not in msg
