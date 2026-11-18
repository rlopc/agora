import json
import tomllib
from pathlib import Path

import pytest

from agora.agents import resolve
from agora.agents.environment import Environment
from agora.tools import TOOLS
from agora.tools.codex.config import generate_codex


@pytest.fixture
def alpha(tmp_path):
    config = {
        "room": {"port": 8766},
        "tools": {
            "codex": {
                "model": "gpt-5.6-terra",
                "effort": "high",
                "subagents": {"model": "gpt-5.6-terra", "effort": "medium"},
                "homes": str(tmp_path / "homes"),
            }
        },
        "participants": [{"id": "alpha", "name": "A", "type": "agent", "folder": "alpha", "tool": "codex"}],
    }
    (tmp_path / "alpha").mkdir()
    [p] = resolve(config, tmp_path, TOOLS)
    environment = Environment(url="http://127.0.0.1:8766", project=Path("/work/room"))
    return p, environment


def read(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_generates_full_config(alpha):
    p, environment = alpha
    changed = generate_codex(p, environment)
    config = p.tool_options.home / "config.toml"
    assert set(changed) == {
        config,
        p.tool_options.home / "agents" / "subagent.toml",
        p.folder / ".codex" / "hooks.json",
    }
    c = read(config)
    assert (c["model"], c["model_reasoning_effort"]) == ("gpt-5.6-terra", "high")
    assert "model_provider" not in c
    assert (c["sandbox_mode"], c["check_for_update_on_startup"]) == ("workspace-write", False)
    # Codex asks to leave the sandbox, and the room's PermissionRequest hook lets the user answer.
    assert c["approval_policy"] == "on-request"
    assert c["cli_auth_credentials_store"] == "auto"
    assert c["projects"][str(p.folder)]["trust_level"] == "trusted"
    room = c["mcp_servers"]["agora"]
    assert room["command"] == "uv"
    assert room["args"] == ["run", "--quiet", "--frozen", "--project", "/work/room", "agora-mcp"]
    assert room["env"] == {"AGORA_PARTICIPANT": "alpha", "AGORA_URL": "http://127.0.0.1:8766"}
    assert {t: v["approval_mode"] for t, v in room["tools"].items()} == dict.fromkeys(
        ["pending", "send", "mark_read", "history", "close_round"], "approve"
    )
    assert "`alpha`" in c["developer_instructions"] and 'agent_type = "subagent"' in c["developer_instructions"]
    # Without retrying outside the sandbox, Codex never asks and the room never sees the request.
    assert "retry it asking for approval" in c["developer_instructions"]
    sub = c["agents"]["subagent"]
    assert sub["config_file"] == str(p.tool_options.home / "agents" / "subagent.toml") and sub["description"]
    assert read(Path(sub["config_file"])) == {"model": "gpt-5.6-terra", "model_reasoning_effort": "medium"}


def test_keeps_foreign_content(alpha):
    p, environment = alpha
    p.tool_options.home.mkdir(parents=True)
    previous = (
        "# user comment\n"
        'model = "old"\n\n'
        "[tui]\nscreen_reader_detection_done = true\n\n"
        "[hooks.state.'/x/hooks.json:session_start:0:0']\n"
        'trusted_hash = "sha256:abc"\n'
    )
    (p.tool_options.home / "config.toml").write_text(previous, encoding="utf-8")
    generate_codex(p, environment)
    text = (p.tool_options.home / "config.toml").read_text(encoding="utf-8")
    assert "# user comment" in text
    c = tomllib.loads(text)
    assert c["tui"] == {"screen_reader_detection_done": True}
    assert c["hooks"]["state"] == {"/x/hooks.json:session_start:0:0": {"trusted_hash": "sha256:abc"}}
    assert c["model"] == "gpt-5.6-terra"


def test_existing_project_with_another_spelling(alpha):
    p, environment = alpha
    p.tool_options.home.mkdir(parents=True)
    key = str(p.folder) + "/"
    (p.tool_options.home / "config.toml").write_text(
        f"[projects.'{key}']\ntrust_level = \"trusted\"\n", encoding="utf-8"
    )
    generate_codex(p, environment)
    assert list(read(p.tool_options.home / "config.toml")["projects"]) == [key]


def test_idempotent(alpha):
    p, environment = alpha
    generate_codex(p, environment)
    config = p.tool_options.home / "config.toml"
    before = config.stat().st_mtime_ns
    assert generate_codex(p, environment) == []
    assert config.stat().st_mtime_ns == before


def test_write_false_touches_nothing(alpha):
    p, environment = alpha
    assert len(generate_codex(p, environment, write=False)) == 3
    assert not p.tool_options.home.exists() and not (p.folder / ".codex").exists()


def test_hooks_json(alpha):
    p, environment = alpha
    hooks = p.folder / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    foreign = {"type": "command", "command": "echo hello"}
    hooks.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [foreign]}]}}), encoding="utf-8-sig")
    generate_codex(p, environment)
    data = json.loads(hooks.read_text(encoding="utf-8"))["hooks"]
    assert set(data) == {"SessionStart", "UserPromptSubmit", "PermissionRequest"}
    commands = [h["command"] for group in data["SessionStart"] for h in group["hooks"]]
    assert commands == [
        "echo hello",
        "uv run --quiet --frozen --project /work/room agora-pending --participant alpha "
        "--url http://127.0.0.1:8766 --json-event SessionStart",
    ]
    assert data["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 10
    [[permission]] = [g["hooks"] for g in data["PermissionRequest"]]
    assert permission == {
        "type": "command",
        "command": "uv run --quiet --frozen --project /work/room agora-permission --participant alpha "
        "--url http://127.0.0.1:8766",
        "timeout": 600,
    }


def test_replaces_its_own_permission_hook(alpha):
    p, environment = alpha
    hooks = p.folder / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    old = {"type": "command", "command": "uv run agora-permission --participant alpha --url http://old", "timeout": 5}
    foreign = {"type": "command", "command": "echo audit"}
    hooks.write_text(json.dumps({"hooks": {"PermissionRequest": [{"hooks": [foreign, old]}]}}), encoding="utf-8")
    generate_codex(p, environment)
    groups = json.loads(hooks.read_text(encoding="utf-8"))["hooks"]["PermissionRequest"]
    assert [[h["command"] for h in g["hooks"]] for g in groups] == [
        ["echo audit"],
        [
            "uv run --quiet --frozen --project /work/room agora-permission --participant alpha --url http://127.0.0.1:8766"
        ],
    ]


def test_extra_write_dirs_in_writable_roots(alpha, tmp_path):
    from dataclasses import replace

    p, environment = alpha
    p = replace(p, write_dirs=(tmp_path,))
    generate_codex(p, environment)
    assert read(p.tool_options.home / "config.toml")["sandbox_workspace_write"]["writable_roots"] == [str(tmp_path)]


def test_notices_ask_to_log_in_when_the_config_changes(alpha):
    p, environment = alpha
    notices = TOOLS["codex"].notices(p, generate_codex(p, environment))
    assert f"CODEX_HOME={p.tool_options.home} codex login" in notices[0]
    assert "trust the hooks" in notices[1]
    assert TOOLS["codex"].notices(p, generate_codex(p, environment)) == []


def test_relative_homes_are_relative_to_the_config(tmp_path):
    def home(codex, participant=None):
        config = {
            "tools": {"codex": codex},
            "participants": [{"id": "Alpha", "name": "A", "type": "agent", "tool": "codex", **(participant or {})}],
        }
        [p] = resolve(config, tmp_path, TOOLS)
        return p.tool_options.home

    assert home({"homes": "homes"}) == tmp_path / "homes" / "alpha"
    assert home({}, {"codex_home": "mine"}) == tmp_path / "mine"
    assert home({"homes": "/abs/homes"}) == Path("/abs/homes/alpha")


def test_the_subagent_file_exists_without_subagent_settings(tmp_path):
    # Codex ignores the agent role if its config_file does not exist.
    config = {
        "tools": {"codex": {"homes": "homes"}},
        "participants": [{"id": "alpha", "name": "A", "type": "agent", "tool": "codex"}],
    }
    [p] = resolve(config, tmp_path, TOOLS)
    generate_codex(p, Environment(url="http://127.0.0.1:8766", project=Path("/work/room")))
    assert (p.tool_options.home / "agents" / "subagent.toml").read_text() == ""
    assert generate_codex(p, Environment(url="http://127.0.0.1:8766", project=Path("/work/room"))) == []
