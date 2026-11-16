import tomllib
from pathlib import Path

import pytest

from agora.agents import ConfigError, ResolvedAgent, Subagents, to_participant
from agora.agents import resolve as resolve_with
from agora.tools import TOOLS
from agora.tools.codex.config import CodexOptions

BASE = Path(r"/Dev/WorkSpace/Aegis_vs_Aivista")

NEW_TEXT = r"""
[room]
port = 8766
db = "data/agora.db"

[tools.claude]
model = "opus"
subagents = { model = "sonnet" }

[tools.codex]
model = "gpt-5.6-terra"
effort = "high"
subagents = { model = "gpt-5.6-terra", effort = "medium" }
homes = '/Dev/codex-homes'

[[participants]]
id = "user"
name = "User"
type = "human"

[[participants]]
id = "moderator"
name = "Moderator"
type = "agent"
role = "moderator"
folder = "."
tool = "claude"

[[participants]]
id = "aegis"
name = "AEGIS spokesperson"
type = "agent"
folder = "aegis"
tool = "codex"
codex_thread = ""

[[participants]]
id = "vista"
name = "AIVista spokesperson"
type = "agent"
folder = "vista"
tool = "codex"

"""
NEW = tomllib.loads(NEW_TEXT)


def resolve(config, base):
    return resolve_with(config, base, TOOLS)


def by_id(participants):
    return {p.id: p for p in participants}


def test_defaults_use_what_is_declared():
    m = by_id(resolve(NEW, BASE))["moderator"]
    assert (m.tool, m.model, m.subagents.model, m.folder, m.waker) == (
        "claude",
        "opus",
        "sonnet",
        BASE,
        "concierge",
    )
    assert by_id(resolve(NEW, BASE))["aegis"].folder == BASE / "aegis"


def test_codex_takes_what_is_missing_from_its_tool():
    a = by_id(resolve(NEW, BASE))["aegis"]
    assert (a.model, a.effort, a.subagents) == ("gpt-5.6-terra", "high", Subagents("gpt-5.6-terra", "medium"))
    assert a.tool_options == CodexOptions(Path(r"/Dev/codex-homes/aegis")) and a.waker == "codex_queue"


def test_human_without_tool():
    u = by_id(resolve(NEW, BASE))["user"]
    assert not isinstance(u, ResolvedAgent) and u.waker is None


def test_invalid_type():
    text = NEW_TEXT.replace('type = "human"', 'type = "robot"', 1)
    with pytest.raises(ConfigError, match=r"unknown type for user: robot\. Valid: agent, human"):
        resolve(tomllib.loads(text), BASE)


def test_to_participant_does_not_leak_the_agent_model():
    r = by_id(resolve(NEW, BASE))
    m = to_participant(r["moderator"])
    assert m.waker == "concierge" and m.options == {} and m.role == "moderator"
    assert to_participant(r["aegis"]).options == {"codex_thread": ""}


def test_explicit_codex_home_is_a_tool_option():
    config = tomllib.loads(NEW_TEXT.replace('codex_thread = ""', 'codex_thread = ""\ncodex_home = "/Other/aegis"'))
    a = by_id(resolve(config, BASE))["aegis"]
    assert a.tool_options == CodexOptions(Path("/Other/aegis")) and "codex_home" not in a.options


def test_concierge_model_goes_to_options():
    config = tomllib.loads(NEW_TEXT.replace('tool = "claude"\n', 'tool = "claude"\nconcierge_model = "sonnet"\n', 1))
    m = to_participant(by_id(resolve(config, BASE))["moderator"])
    assert m.options["concierge_model"] == "sonnet" and "model" not in m.options


@pytest.mark.parametrize(
    "change,message",
    [
        ('tool = "gemini"\ncodex_thread', "unknown tool for aegis: gemini. Valid: antigravity, claude, codex"),
        ("codex_thread", "aegis does not declare `tool`. Valid: antigravity, claude, codex"),
    ],
)
def test_invalid_tool(change, message):
    text = NEW_TEXT.replace('tool = "codex"\ncodex_thread', change)
    with pytest.raises(ConfigError, match=message):
        resolve(tomllib.loads(text), BASE)


def test_read_and_write_dirs_relative_to_the_room():
    config = tomllib.loads(
        NEW_TEXT.replace(
            'folder = "."\ntool = "claude"\n',
            'folder = "moderator"\ntool = "claude"\nwrite_dirs = ["."]\nread_dirs = [\'/Dev/Other\']\n',
            1,
        )
    )
    m = by_id(resolve(config, BASE))["moderator"]
    assert m.folder == BASE / "moderator"
    assert m.write_dirs == (BASE,) and m.read_dirs == (Path(r"/Dev/Other"),)
    assert "write_dirs" not in to_participant(m).options
