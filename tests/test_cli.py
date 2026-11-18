from pathlib import Path

import pytest

from agora import cli
from agora.agents import resolve
from agora.tools import TOOLS
from agora.tools.codex import FIRST_MESSAGE

CONFIG = {
    "room": {"port": 8766},
    "tools": {"claude": {"model": "opus"}, "codex": {"model": "gpt-5.6-terra", "homes": r"/homes"}},
    "participants": [
        {"id": "user", "name": "U", "type": "human"},
        {"id": "moderator", "name": "M", "type": "agent", "folder": ".", "tool": "claude"},
        {"id": "alpha", "name": "A", "type": "agent", "folder": "alpha", "tool": "codex"},
        {"id": "beta", "name": "V", "type": "agent", "folder": "beta", "tool": "codex"},
    ],
}
BASE = Path(r"/Debate")


def agent(id):
    return {p.id: p for p in resolve(CONFIG, BASE, TOOLS)}[id]


def test_launch_codex():
    launch = TOOLS["codex"].launch(agent("alpha"), {"PATH": "x"})
    # With a first message, Codex creates its session on start and the room finds it to wake it.
    assert launch.argv == ["codex", "--model", "gpt-5.6-terra", FIRST_MESSAGE]
    assert "pending" in FIRST_MESSAGE
    assert launch.cwd == BASE / "alpha"
    assert launch.env == {"PATH": "x", "CODEX_HOME": r"/homes/alpha"}


def test_launch_without_model_uses_the_tool_default():
    config = {"participants": [{"id": "a", "name": "A", "type": "agent", "tool": "claude"}]}
    [p] = resolve(config, BASE, TOOLS)
    assert "--model" not in TOOLS["claude"].launch(p, {}).argv


def test_launch_claude_without_claude_variables():
    launch = TOOLS["claude"].launch(agent("moderator"), {"PATH": "x", "CLAUDECODE": "1", "claude_code_sse_port": "9"})
    assert launch.argv == ["claude", "--model", "opus", "--permission-mode", "acceptEdits"]
    assert launch.cwd == BASE and launch.env == {"PATH": "x"}


def test_start_opens_room_and_agents_in_order(monkeypatch, tmp_path, capsys):
    for folder in ("alpha", "beta"):
        (tmp_path / folder).mkdir()
    path = tmp_path / "agora.toml"
    path.write_text(
        "[room]\nport = 8766\n\n[tools.codex]\nhomes = '" + str(tmp_path / "homes") + "'\n\n"
        '[[participants]]\nid = "user"\nname = "U"\ntype = "human"\n\n'
        '[[participants]]\nid = "moderator"\nname = "M"\ntype = "agent"\nfolder = "."\ntool = "claude"\n\n'
        '[[participants]]\nid = "alpha"\nname = "A"\ntype = "agent"\nfolder = "alpha"\ntool = "codex"\n\n'
        '[[participants]]\nid = "beta"\nname = "V"\ntype = "agent"\nfolder = "beta"\ntool = "codex"\n',
        encoding="utf-8",
    )
    opened = []
    monkeypatch.setattr(cli, "open_window", lambda title, argv, cwd: opened.append((title, argv)))
    monkeypatch.setattr(cli, "wait_for_room", lambda url, timeout=15: True)
    monkeypatch.setattr(cli, "room_running", lambda url: False)
    cli.main_start(["--config", str(path)])
    assert [t for t, _ in opened] == ["agora", "moderator", "alpha", "beta"]
    assert opened[0][1][-3:] == ["agora", "--config", str(path)]
    env, variable = opened[0][1][:2]
    token = variable.removeprefix("AGORA_TOKEN=")
    assert env == "env" and len(token) >= 32
    assert opened[2][1][-4:] == ["agora-launch", "--config", str(path), "alpha"]
    assert (tmp_path / "homes" / "alpha" / "config.toml").is_file()  # prepared before opening
    out = capsys.readouterr().out
    assert f"Room at http://127.0.0.1:8766/?token={token};" in out
    assert "- claude (moderator): trust the folder" in out
    assert "- codex (alpha, beta): trust the hooks" in out


def test_start_does_not_open_agents_if_the_room_does_not_answer(monkeypatch, tmp_path):
    path = tmp_path / "agora.toml"
    path.write_text(
        '[[participants]]\nid = "m"\nname = "M"\ntype = "agent"\nfolder = "."\ntool = "claude"\n',
        encoding="utf-8",
    )
    opened = []
    monkeypatch.setattr(cli, "open_window", lambda title, argv, cwd: opened.append(title))
    monkeypatch.setattr(cli, "wait_for_room", lambda url, timeout=15: False)
    monkeypatch.setattr(cli, "room_running", lambda url: False)
    with pytest.raises(SystemExit, match="The room is not answering"):
        cli.main_start(["--config", str(path)])
    assert opened == ["agora"]


def test_start_stops_if_a_room_is_already_running(monkeypatch, tmp_path):
    path = tmp_path / "agora.toml"
    path.write_text(
        '[[participants]]\nid = "m"\nname = "M"\ntype = "agent"\nfolder = "."\ntool = "claude"\n',
        encoding="utf-8",
    )
    opened = []
    monkeypatch.setattr(cli, "open_window", lambda title, argv, cwd: opened.append(title))
    monkeypatch.setattr(cli, "room_running", lambda url: True)
    with pytest.raises(SystemExit, match="A room is already running"):
        cli.main_start(["--config", str(path)])
    assert opened == []


def test_prepare_warns_if_the_room_is_already_running(monkeypatch, tmp_path, capsys):
    path = tmp_path / "agora.toml"
    path.write_text(
        '[[participants]]\nid = "m"\nname = "M"\ntype = "agent"\nfolder = "."\ntool = "claude"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "room_running", lambda url: True)
    cli.main_prepare(["--config", str(path)])
    assert "restart it" in capsys.readouterr().out


def test_archive_leaves_the_room_empty(monkeypatch, tmp_path, capsys):
    path = tmp_path / "agora.toml"
    path.write_text('[room]\ndb = "data/room.db"\n', encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "room.db").touch()
    monkeypatch.setattr(cli, "room_running", lambda url: False)
    cli.main_archive(["--config", str(path)])
    assert not (tmp_path / "data" / "room.db").exists()
    [archived] = (tmp_path / "data" / "archive").iterdir()
    assert archived.name.startswith("room-")
    assert str(archived) in capsys.readouterr().out


def test_archive_without_database(monkeypatch, tmp_path, capsys):
    path = tmp_path / "agora.toml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "room_running", lambda url: False)
    cli.main_archive(["--config", str(path)])
    assert "Nothing to archive" in capsys.readouterr().out


def test_archive_stops_if_the_room_is_running(monkeypatch, tmp_path):
    path = tmp_path / "agora.toml"
    path.write_text("", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "agora.db").touch()
    monkeypatch.setattr(cli, "room_running", lambda url: True)
    with pytest.raises(SystemExit, match="stop it before archiving"):
        cli.main_archive(["--config", str(path)])
    assert (tmp_path / "data" / "agora.db").exists()


def test_prepare_and_launch_without_model_name_the_default(monkeypatch, tmp_path, capsys):
    path = tmp_path / "agora.toml"
    path.write_text(
        '[[participants]]\nid = "m"\nname = "M"\ntype = "agent"\nfolder = "."\ntool = "claude"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "room_running", lambda url: False)
    cli.main_prepare(["--config", str(path)])
    out = capsys.readouterr().out
    assert "- m (claude, default model): " in out and "None" not in out

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/true")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: type("Done", (), {"returncode": 0})())
    with pytest.raises(SystemExit):
        cli.main_launch(["--config", str(path), "m"])
    assert "with its default model in" in capsys.readouterr().out
