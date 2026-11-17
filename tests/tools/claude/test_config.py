import json
from pathlib import Path

import pytest

from agora.agents import resolve
from agora.agents.environment import Environment
from agora.tools import TOOLS
from agora.tools.claude.config import END, PROJECT_HOOK, PROJECT_MCP, READ_ONLY, START, generate_claude


@pytest.fixture
def moderator(tmp_path):
    config = {
        "tools": {"claude": {"model": "opus", "subagents": {"model": "sonnet"}}},
        "participants": [
            {
                "id": "moderator",
                "name": "M",
                "type": "agent",
                "role": "moderator",
                "folder": ".",
                "tool": "claude",
                "read_dirs": [r"/Dev/WorkSpace/AegisWS"],
            }
        ],
    }
    [p] = resolve(config, tmp_path, TOOLS)
    return p, Environment(url="http://127.0.0.1:8766", project=Path("/KnowledgeBase"))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


COMMAND = (
    "uv run --quiet --frozen --project /KnowledgeBase agora-pending --participant moderator --url http://127.0.0.1:8766"
)

PERMISSION = COMMAND.replace("agora-pending", "agora-permission")


def test_generates_mcp_settings_and_claude_md(moderator, tmp_path):
    p, environment = moderator
    changed = generate_claude(p, environment)
    assert set(changed) == {tmp_path / ".mcp.json", tmp_path / ".claude" / "settings.json", tmp_path / "CLAUDE.md"}
    assert read(tmp_path / ".mcp.json")["mcpServers"]["agora"]["env"] == {
        "AGORA_PARTICIPANT": "moderator",
        "AGORA_URL": "http://127.0.0.1:8766",
    }
    settings = read(tmp_path / ".claude" / "settings.json")
    for event in ("SessionStart", "UserPromptSubmit"):
        assert settings["hooks"][event] == [{"hooks": [{"type": "command", "command": COMMAND, "timeout": 10}]}]
    assert settings["hooks"]["PermissionRequest"] == [
        {"hooks": [{"type": "command", "command": PERMISSION, "timeout": 10}]}
    ]
    assert settings["permissions"]["allow"] == ["mcp__agora", *READ_ONLY]
    assert all(not r.startswith("Bash(rm") for r in READ_ONLY)
    assert settings["permissions"]["additionalDirectories"] == [r"/Dev/WorkSpace/AegisWS"]
    claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.startswith("@AGENTS.md\n")
    assert START in claude_md and END in claude_md and "`model: sonnet`" in claude_md and "`moderator`" in claude_md


def test_external_folder_uses_absolute_path(moderator, tmp_path):
    p, environment = moderator
    generate_claude(p, environment)
    assert "/KnowledgeBase" in read(tmp_path / ".mcp.json")["mcpServers"]["agora"]["args"]


def test_internal_folder_uses_the_session_folder(moderator, tmp_path):
    from dataclasses import replace

    p, environment = moderator
    environment = replace(environment, project=tmp_path)
    generate_claude(p, environment)
    assert read(tmp_path / ".mcp.json")["mcpServers"]["agora"]["args"] == [
        "run",
        "--quiet",
        "--frozen",
        "--project",
        PROJECT_MCP,
        "agora-mcp",
    ]
    settings = read(tmp_path / ".claude" / "settings.json")
    for event in ("SessionStart", "UserPromptSubmit"):
        [group] = settings["hooks"][event]
        assert group["hooks"][0]["command"] == COMMAND.replace("/KnowledgeBase", PROJECT_HOOK)
    [group] = settings["hooks"]["PermissionRequest"]
    assert group["hooks"][0]["command"] == PERMISSION.replace("/KnowledgeBase", PROJECT_HOOK)
    assert (
        str(tmp_path) not in (tmp_path / ".mcp.json").read_text() + (tmp_path / ".claude" / "settings.json").read_text()
    )


def test_keeps_foreign_hooks_and_permissions(moderator, tmp_path):
    p, environment = moderator
    (tmp_path / ".claude").mkdir()
    previous = {
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hello"}]}]},
        "permissions": {"allow": ["Bash(cp *)"]},
        "enableAllProjectMcpServers": True,
    }
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(previous), encoding="utf-8")
    generate_claude(p, environment)
    settings = read(tmp_path / ".claude" / "settings.json")
    assert settings["enableAllProjectMcpServers"] is True
    assert settings["permissions"]["allow"] == ["Bash(cp *)", "mcp__agora", *READ_ONLY]
    assert [h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]] == ["echo hello", COMMAND]


def test_reads_json_with_bom(moderator, tmp_path):
    p, environment = moderator
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8-sig")
    generate_claude(p, environment)
    assert set(read(tmp_path / ".mcp.json")["mcpServers"]) == {"other", "agora"}


def test_text_outside_the_section_untouched(moderator, tmp_path):
    p, environment = moderator
    (tmp_path / "CLAUDE.md").write_text(
        f"@AGENTS.md\n\nMy notes.\n\n{START}\nold\n{END}\n\nMore notes.\n", encoding="utf-8"
    )
    generate_claude(p, environment)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("@AGENTS.md\n\nMy notes.\n\n" + START) and text.endswith(f"{END}\n\nMore notes.\n")
    assert "old" not in text


def test_claude_md_without_section_is_appended_at_the_end(moderator, tmp_path):
    p, environment = moderator
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n\n## Notes for Claude Code\n\n- something\n", encoding="utf-8")
    generate_claude(p, environment)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("@AGENTS.md\n\n## Notes for Claude Code\n\n- something\n\n" + START)


def test_idempotent(moderator):
    p, environment = moderator
    generate_claude(p, environment)
    assert generate_claude(p, environment) == []


def test_extra_write_dirs_in_additional_directories(moderator, tmp_path):
    from dataclasses import replace

    p, environment = moderator
    p = replace(p, write_dirs=(Path(r"/Debate"),))
    generate_claude(p, environment)
    assert read(tmp_path / ".claude" / "settings.json")["permissions"]["additionalDirectories"] == [
        r"/Dev/WorkSpace/AegisWS",
        r"/Debate",
    ]
