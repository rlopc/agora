import pytest

from agora.agents.prepare import prepare
from agora.cli import main_prepare as main
from agora.tools import TOOLS

TEXT = """
[room]
port = 8766
db = "data/agora.db"

[tools.claude]
model = "opus"

[tools.codex]
model = "gpt-5.6-terra"
homes = '{homes}'

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

"""


@pytest.fixture
def room(tmp_path):
    (tmp_path / "aegis").mkdir()
    path = tmp_path / "agora.toml"
    path.write_text(TEXT.format(homes=tmp_path / "homes"), encoding="utf-8")
    return path


def test_prepares_each_agent_with_its_tool(room, tmp_path):
    main(["--config", str(room)])
    assert (tmp_path / ".mcp.json").is_file() and not (tmp_path / "homes" / "moderator").exists()
    assert (tmp_path / "homes" / "aegis" / "config.toml").is_file()
    assert (tmp_path / "aegis" / ".codex" / "hooks.json").is_file()


def test_agent_without_tool_exits_with_message(tmp_path):
    path = tmp_path / "agora.toml"
    path.write_text(
        '[[participants]]\nid = "c"\nname = "C"\ntype = "agent"\nwaker = "concierge"\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="c does not declare `tool`"):
        main(["--config", str(path)])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["agora.toml"]


def test_without_agents_generates_nothing(tmp_path, capsys):
    path = tmp_path / "agora.toml"
    path.write_text('[[participants]]\nid = "u"\nname = "U"\ntype = "human"\n', encoding="utf-8")
    main(["--config", str(path)])
    assert "agora.toml declares no agents" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["agora.toml"]


def test_second_time_up_to_date(room, capsys):
    main(["--config", str(room)])
    output = capsys.readouterr().out
    assert "moderator (claude, opus): 3 files updated" in output
    assert "Codex will ask to trust the hooks again" in output
    main(["--config", str(room)])
    output = capsys.readouterr().out
    assert "moderator (claude, opus): up to date" in output and "aegis (codex, gpt-5.6-terra): up to date" in output
    assert "trust" not in output


def test_prepare_without_writing_lists_what_is_pending(room, tmp_path):
    import tomllib

    config = tomllib.loads(room.read_text(encoding="utf-8"))
    pending = prepare(config, tmp_path, TOOLS, write=False)
    assert set(pending) == {"moderator", "aegis"} and all(pending.values())
    assert not (tmp_path / ".mcp.json").exists()


def test_claude_agent_inside_another_folder_is_rejected_without_writing(room, tmp_path):
    # Claude loads the CLAUDE.md files of parent folders: aegis would read the moderator's role.
    room.write_text(room.read_text(encoding="utf-8").replace('tool = "codex"', 'tool = "claude"'), encoding="utf-8")
    with pytest.raises(SystemExit, match=r"aegis.*is inside that of moderator"):
        main(["--config", str(room)])
    assert not (tmp_path / ".mcp.json").exists() and not (tmp_path / "aegis" / ".mcp.json").exists()


def test_two_agents_in_the_same_folder_are_rejected(tmp_path):
    path = tmp_path / "agora.toml"
    path.write_text(
        '[tools.codex]\nhomes = \'' + str(tmp_path / "h") + "'\n\n"
        '[[participants]]\nid = "a"\nname = "A"\ntype = "agent"\nfolder = "."\ntool = "codex"\n\n'
        '[[participants]]\nid = "b"\nname = "B"\ntype = "agent"\ntool = "codex"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="a and b share a folder"):
        main(["--config", str(path)])
