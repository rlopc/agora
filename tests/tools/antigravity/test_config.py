import json
from datetime import datetime
from pathlib import Path

import pytest

from agora.agents import resolve, watch
from agora.agents.environment import Environment
from agora.core import Message
from agora.server import create_waker
from agora.tools import TOOLS
from agora.tools.antigravity.config import generate_antigravity

COMMAND = "uv run --quiet --frozen --project /work/room agora-pending --participant gemini --url http://127.0.0.1:8766 --inject-steps"


@pytest.fixture
def gemini(tmp_path):
    config = {
        "tools": {"antigravity": {"model": "gemini-3.8-flash", "effort": "high"}},
        "participants": [
            {
                "id": "gemini",
                "name": "G",
                "type": "agent",
                "folder": "gemini",
                "tool": "antigravity",
                "read_dirs": ["/Docs"],
            }
        ],
    }
    (tmp_path / "gemini").mkdir()
    [p] = resolve(config, tmp_path, TOOLS)
    return p, Environment(url="http://127.0.0.1:8766", project=Path("/work/room"))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_generates_mcp_hooks_and_rule(gemini):
    p, environment = gemini
    agents = p.folder / ".agents"
    changed = generate_antigravity(p, environment)
    assert set(changed) == {agents / "mcp_config.json", agents / "hooks.json", agents / "rules" / "agora.md"}
    assert read(agents / "mcp_config.json")["mcpServers"]["agora"]["env"] == {
        "AGORA_PARTICIPANT": "gemini",
        "AGORA_URL": "http://127.0.0.1:8766",
    }
    own = [{"type": "command", "command": COMMAND, "timeout": 10}]
    permission = {
        "type": "command",
        "command": "uv run --quiet --frozen --project /work/room agora-permission --participant gemini "
        "--url http://127.0.0.1:8766 --antigravity",
        # Longer than the room waits (600 s): on timeout agy fails the call instead of asking.
        "timeout": 630,
    }
    assert read(agents / "hooks.json") == {
        "agora": {
            "SessionStart": own,
            "PreInvocation": own,
            "PreToolUse": [{"matcher": "run_command", "hooks": [permission]}],
        }
    }
    rule = (agents / "rules" / "agora.md").read_text(encoding="utf-8")
    # Without a valid trigger, Antigravity ignores the rule.
    assert rule.startswith("---\ntrigger: always_on\n") and "`gemini`" in rule and "cannot wake you" in rule
    assert generate_antigravity(p, environment) == []


def test_keeps_what_others_configured(gemini):
    p, environment = gemini
    agents = p.folder / ".agents"
    agents.mkdir()
    (agents / "mcp_config.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    (agents / "hooks.json").write_text(json.dumps({"format": {"PostToolUse": []}}), encoding="utf-8")
    generate_antigravity(p, environment)
    assert set(read(agents / "mcp_config.json")["mcpServers"]) == {"other", "agora"}
    assert set(read(agents / "hooks.json")) == {"format", "agora"}


def test_launch_allows_the_room_tools_only_for_the_session(gemini):
    p, _ = gemini
    launch = TOOLS["antigravity"].launch(p, {"PATH": "x"})
    assert launch.argv[:4] == ["agy", "--model", "gemini-3.8-flash", "--mode"]
    assert json.loads(launch.argv[launch.argv.index("--settings") + 1]) == {"permissions": {"allow": ["mcp(agora/*)"]}}
    assert launch.argv[-4:] == ["--effort", "high", "--add-dir", "/Docs"]
    assert launch.cwd == p.folder and launch.env == {"PATH": "x"}


def test_the_room_does_not_wake_it_and_says_so_once(gemini):
    p, environment = gemini
    assert p.waker is None and create_waker(p, TOOLS, None) is None
    tool = TOOLS["antigravity"]
    assert "cannot wake" in tool.notices(p, generate_antigravity(p, environment))[0]
    assert tool.notices(p, []) == []


def test_pending_as_inject_steps(monkeypatch, capsys):
    message = Message(1, "user", "hello", ("gemini",), None, 0, datetime(2026, 11, 16))
    monkeypatch.setattr(watch, "fetch_pending", lambda *a, **k: [message])
    watch.main_pending(["--participant", "gemini", "--inject-steps"])
    [step] = json.loads(capsys.readouterr().out)["injectSteps"]
    assert "hello" in step["systemMessage"]["systemMessage"]
    monkeypatch.setattr(watch, "fetch_pending", lambda *a, **k: [])
    watch.main_pending(["--participant", "gemini", "--inject-steps"])
    assert json.loads(capsys.readouterr().out) == {}
