"""Every document tool as two domain tools on one endpoint, `action` plus `args`.

A model connected to both tiers reads 13 tool names on every turn. The domain
endpoint lists two, and each `action` is one of those 13 tools
by its own name -- its schema read from the tier, its call run by the tier's
own `run()`, so validation, wrappers and answers are the tier's.
"""

from __future__ import annotations

import asyncio
import base64
import importlib

import pytest

from servers.docs_domain.server import DOMAINS, mcp

TIERS = ["docs_read", "docs_edit"]


def _tier_listed() -> set[str]:
    names: set[str] = set()
    for tier in TIERS:
        server = importlib.import_module(f"servers.{tier}.server").mcp
        names |= {t.name for t in asyncio.run(server.list_tools())}
    return names


def _tier_tool(name: str):
    for tier in TIERS:
        tools = importlib.import_module(f"servers.{tier}.server").mcp._tool_manager._tools
        if name in tools:
            return tools[name]
    raise KeyError(name)


def _listed():
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def _call(tool: str, action: str, args: dict | None = None) -> dict:
    payload = {"action": action} if args is None else {"action": action, "args": args}
    return asyncio.run(mcp._tool_manager._tools[tool].run(payload))


@pytest.fixture
def csv(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path / "data"))
    path = tmp_path / "notes.md"
    path.write_text("# Findings\n\nRevenue rose.\n\n## Next steps\n\nShip it.\n", encoding="utf-8")
    return str(path)


class TestTwoNotThirteen:
    def test_two_tools_are_listed(self):
        assert sorted(_listed()) == sorted(DOMAINS)
        assert len(_listed()) == 2

    def test_every_listed_tier_tool_is_exactly_one_action(self):
        actions = [a for t in _listed().values() for a in t.inputSchema["properties"]["action"]["enum"]]
        assert len(actions) == len(set(actions)), "an action is in two domains"
        assert set(actions) == _tier_listed(), "a tier tool is missing from the domains, or a retired one crept in"

    def test_the_schema_is_the_pipeline_shape(self):
        for tool in _listed().values():
            schema = tool.inputSchema
            assert schema["required"] == ["action"]
            assert schema["properties"]["args"]["additionalProperties"] is False
            for action in schema["properties"]["action"]["enum"]:
                assert f"- {action}:" in tool.description, (tool.name, action)

    def test_each_argument_says_which_actions_take_and_require_it(self):
        for tool in _listed().values():
            actions = tool.inputSchema["properties"]["action"]["enum"]
            tiers = {a: mcp_tool for a in actions for mcp_tool in [_tier_tool(a)]}
            for param, prop in tool.inputSchema["properties"]["args"]["properties"].items():
                required = [a for a in actions if param in (tiers[a].parameters.get("required") or [])]
                text = prop["description"]
                if not required:
                    assert "required by" not in text, (tool.name, param, text)
                elif len(required) == len(actions):
                    assert text.endswith("required by every action"), (tool.name, param, text)
                else:
                    assert text.endswith(f"required by {', '.join(required)}"), (tool.name, param, text)
        source = _listed()["docs_read"].inputSchema["properties"]["args"]["properties"]["source"]
        assert "every action" in source["description"]


class TestAnActionIsTheTierTool:
    @pytest.mark.parametrize(
        ("tool", "action", "args"),
        [
            ("docs_read", "probe", {}),
            ("docs_read", "outline", {}),
            ("docs_read", "to_markdown", {}),
        ],
    )
    def test_it_answers_as_the_tier_does(self, csv, tool, action, args):
        result = _call(tool, action, {"source": csv, **args})
        assert result["success"] is True, result

    def test_the_tiers_wrappers_still_run(self, csv, tmp_path):
        uri = "data:text/markdown;name=sent.md;base64," + base64.b64encode(b"# Sent\n\nBody.\n").decode()
        result = _call("docs_read", "probe", {"source": uri})
        assert result["success"] is True, result
        assert "base64" not in str(result)


class TestARefusalSaysWhatToDo:
    def test_an_action_of_another_tool_is_pointed_home(self):
        result = _call("docs_read", "redact", {})
        assert result["success"] is False and "docs_edit" in result["hint"]

    def test_an_unknown_action_lists_the_actions(self):
        result = _call("docs_edit", "no_such_thing", {})
        assert result["success"] is False and "convert" in result["hint"]

    def test_an_argument_the_action_does_not_take_is_named(self, csv):
        result = _call("docs_read", "probe", {"source": csv, "colour": "red"})
        assert result["success"] is False and "colour" in result["error"]
        assert "source" in result["hint"]

    def test_a_missing_required_argument_is_an_answer(self):
        result = _call("docs_read", "outline", {})
        assert result["success"] is False and "source" in (result["hint"] + result["error"])


def test_the_unified_server_serves_it_at_mcp():
    from starlette.testclient import TestClient

    import unified_server

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    }
    with TestClient(unified_server.app) as client:
        assert client.get("/").json()["mcp"] == "/mcp"
        reply = client.post("/mcp", json=body, headers={"Accept": "application/json, text/event-stream"})
        assert reply.status_code == 200 and '"serverInfo"' in reply.text
        assert client.get("/read/health").status_code == 200


def test_a_connector_can_discover_how_to_sign_in_to_mcp(tmp_path):
    """RFC 9728 discovery for /mcp agrees with itself, and the tiers' is unchanged.

    Mounted at the root, the SDK's own metadata route answered
    /.well-known/oauth-protected-resource with the bare origin as the resource,
    and /.well-known/oauth-protected-resource/mcp -- the path a client derives
    from https://host/mcp -- was a 404. Run in a fresh interpreter, since the
    OAuth bridge exists only when a key is configured at import time.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "DOCS_API_KEY": "test-key-not-a-secret", "DOCS_PUBLIC_URL": "https://docs.example.test"}
    for tier in ("READ", "EDIT", "DOMAIN"):
        env[f"DOCS_{tier}_OAUTH_STATE_DIR"] = str(tmp_path / tier.lower())
    probe = (
        "import json\n"
        "from starlette.testclient import TestClient\n"
        "import unified_server\n"
        "with TestClient(unified_server.app) as c:\n"
        "    out = {p: c.get(p).json() for p in ('/.well-known/oauth-protected-resource/mcp',\n"
        "        '/.well-known/oauth-protected-resource', '/.well-known/oauth-authorization-server',\n"
        "        '/read/.well-known/oauth-protected-resource')}\n"
        "    r = c.post('/mcp', json={}, headers={'Accept': 'application/json, text/event-stream'})\n"
        "    out['status'] = r.status_code\n"
        "    out['hint'] = r.headers.get('www-authenticate', '')\n"
        "print(json.dumps(out))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    at_mcp = out["/.well-known/oauth-protected-resource/mcp"]
    assert at_mcp["resource"].endswith("/mcp")
    assert out["/.well-known/oauth-protected-resource"] == at_mcp
    assert at_mcp["authorization_servers"] == [out["/.well-known/oauth-authorization-server"]["issuer"]]
    assert out["status"] == 401
    assert out["/read/.well-known/oauth-protected-resource"]["resource"].endswith("/read/mcp")
