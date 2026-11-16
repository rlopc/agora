import json
import tomllib
from pathlib import Path

import pytest

from agora.agents import resolve
from agora.tools import TOOLS
from agora.agents.environment import Environment
from agora.tools.codex.config import generate_codex

@pytest.fixture
def aegis(tmp_path):
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
        "participants": [{"id": "aegis", "name": "A", "type": "agent", "folder": "aegis", "tool": "codex"}],
    }
    (tmp_path / "aegis").mkdir()
    [p] = resolve(config, tmp_path, TOOLS)
    environment = Environment(url="http://127.0.0.1:8766", project=Path("/KnowledgeBase"))
    return p, environment


def read(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_generates_full_config(aegis):
    p, environment = aegis
    changed = generate_codex(p, environment)
    config = p.tool_options.home / "config.toml"
    assert set(changed) == {config, p.tool_options.home / "agents" / "subagent.toml", p.folder / ".codex" / "hooks.json"}
    c = read(config)
    assert (c["model"], c["model_reasoning_effort"]) == ("gpt-5.6-terra", "high")
    assert "model_provider" not in c
    assert (c["sandbox_mode"], c["check_for_update_on_startup"]) == ("workspace-write", False)
    # Nobody approves in the console during the debate: the sandbox limits and Codex does not ask.
    assert c["approval_policy"] == "never"
    assert c["cli_auth_credentials_store"] == "auto"
    assert c["projects"][str(p.folder)]["trust_level"] == "trusted"
    room = c["mcp_servers"]["agora"]
    assert room["command"] == "uv"
    assert room["args"] == ["run", "--quiet", "--frozen", "--project", "/KnowledgeBase", "agora-mcp"]
    assert room["env"] == {"AGORA_PARTICIPANT": "aegis", "AGORA_URL": "http://127.0.0.1:8766"}
    assert {t: v["approval_mode"] for t, v in room["tools"].items()} == dict.fromkeys(
        ["pending", "send", "mark_read", "history"], "approve"
    )
    assert "`aegis`" in c["developer_instructions"] and 'agent_type = "subagent"' in c["developer_instructions"]
    sub = c["agents"]["subagent"]
    assert sub["config_file"] == str(p.tool_options.home / "agents" / "subagent.toml") and sub["description"]
    assert read(Path(sub["config_file"])) == {"model": "gpt-5.6-terra", "model_reasoning_effort": "medium"}


def test_keeps_foreign_content(aegis):
    p, environment = aegis
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


def test_existing_project_with_another_spelling(aegis):
    p, environment = aegis
    p.tool_options.home.mkdir(parents=True)
    key = str(p.folder) + "/"
    (p.tool_options.home / "config.toml").write_text(f"[projects.'{key}']\ntrust_level = \"trusted\"\n", encoding="utf-8")
    generate_codex(p, environment)
    assert list(read(p.tool_options.home / "config.toml")["projects"]) == [key]


def test_idempotent(aegis):
    p, environment = aegis
    generate_codex(p, environment)
    config = p.tool_options.home / "config.toml"
    before = config.stat().st_mtime_ns
    assert generate_codex(p, environment) == []
    assert config.stat().st_mtime_ns == before


def test_write_false_touches_nothing(aegis):
    p, environment = aegis
    assert len(generate_codex(p, environment, write=False)) == 3
    assert not p.tool_options.home.exists() and not (p.folder / ".codex").exists()


def test_hooks_json(aegis):
    p, environment = aegis
    hooks = p.folder / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    foreign = {"type": "command", "command": "echo hello"}
    hooks.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [foreign]}]}}), encoding="utf-8-sig")
    generate_codex(p, environment)
    data = json.loads(hooks.read_text(encoding="utf-8"))["hooks"]
    assert set(data) == {"SessionStart", "UserPromptSubmit"}
    commands = [h["command"] for group in data["SessionStart"] for h in group["hooks"]]
    assert commands == [
        "echo hello",
        "uv run --quiet --frozen --project /KnowledgeBase agora-pending --participant aegis "
        "--url http://127.0.0.1:8766 --json-event SessionStart",
    ]
    assert data["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 10


def test_extra_write_dirs_in_writable_roots(aegis, tmp_path):
    from dataclasses import replace

    p, environment = aegis
    p = replace(p, write_dirs=(tmp_path,))
    generate_codex(p, environment)
    assert read(p.tool_options.home / "config.toml")["sandbox_workspace_write"]["writable_roots"] == [str(tmp_path)]


def test_notices_ask_to_log_in_when_the_config_changes(aegis):
    p, environment = aegis
    notices = TOOLS["codex"].notices(p, generate_codex(p, environment))
    assert f"CODEX_HOME={p.tool_options.home} codex login" in notices[0]
    assert "trust the hooks" in notices[1]
    assert TOOLS["codex"].notices(p, generate_codex(p, environment)) == []
