"""MCP stdio test: launches the real installed server over stdio against a planted
project, calls every tool, and asserts no planted value appears anywhere, including
in errors, and that actionable errors reach the agent."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

from .conftest import PLANTED  # noqa: E402
from .test_redaction import hostile_project  # noqa: E402

EXPECTED_TOOLS = {
    "env_overview",
    "compare_env",
    "find_usage",
    "validate_env",
    "check_gitignore",
    "generate_example",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def params(root: Path) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "envdoctor_mcp", "--dir", str(root)])


@pytest.mark.anyio
async def test_tools_and_no_planted_value_over_stdio(tmp_path: Path) -> None:
    root = hostile_project(tmp_path / "proj")
    seen: list[str] = []
    async with stdio_client(params(root)) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.server_info.name == "envdoctor"
        tools = (await session.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS
        for t in tools:
            seen.append(t.name + (t.description or "") + str(t.input_schema))
            assert t.description and len(t.description) > 80
        for name in sorted(EXPECTED_TOOLS):
            res = await session.call_tool(name, {})
            assert not res.is_error, (name, res.content)
            seen.append(res.content[0].text)  # type: ignore[union-attr]
        res = await session.call_tool("validate_env", {"file": ".env"})
        assert "BAD_PORT: bad-port" in res.content[0].text  # type: ignore[union-attr]
        seen.append(res.content[0].text)  # type: ignore[union-attr]
        res = await session.call_tool("compare_env", {"file": ".env", "against": ".env.example"})
        seen.append(res.content[0].text)  # type: ignore[union-attr]
    blob = "\n".join(seen)
    for key, value in PLANTED.items():
        assert value not in blob, key


@pytest.mark.anyio
async def test_instructive_errors_reach_the_agent(tmp_path: Path) -> None:
    root = hostile_project(tmp_path / "proj")
    async with stdio_client(params(root)) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        res = await session.call_tool("env_overview", {"path": ".."})
        assert res.is_error and "outside the sandbox folder 'proj' (set with --dir)" in res.content[0].text  # type: ignore[union-attr]
        res = await session.call_tool("validate_env", {"file": "missing.env"})
        assert res.is_error and "'missing.env' does not exist inside the sandbox folder" in res.content[0].text  # type: ignore[union-attr]
        res = await session.call_tool("compare_env", {"file": "app.py"})
        assert res.is_error and "app.py is not an env file under the scanned folder" in res.content[0].text  # type: ignore[union-attr]
        for r in (res,):
            for value in PLANTED.values():
                assert value not in r.content[0].text  # type: ignore[union-attr]


def test_dir_must_exist(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "envdoctor_mcp", "--dir", str(tmp_path / "nope")],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert proc.returncode == 2 and "is not a directory" in proc.stderr
