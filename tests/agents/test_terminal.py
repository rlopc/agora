from pathlib import Path

import pytest

from agora.agents import terminal


def test_open_window_creates_the_session_and_then_adds_windows(monkeypatch):
    commands = []
    session = {"exists": False}

    def run(argv, **kw):
        commands.append(argv)
        return type("R", (), {"returncode": 0 if session["exists"] else 1})()

    monkeypatch.setattr(terminal.subprocess, "run", run)
    monkeypatch.setattr(terminal.shutil, "which", lambda name: "/bin/tmux")
    terminal.open_window("agora", ["uv", "run", "agora"], Path("/Debate"))
    assert commands[-1] == [
        "/bin/tmux",
        "new-session",
        "-d",
        "-s",
        "agora",
        "-n",
        "agora",
        "-c",
        "/Debate",
        "--",
        "uv",
        "run",
        "agora",
    ]
    session["exists"] = True
    terminal.open_window("aegis", ["uv", "run", "agora-launch", "aegis"], Path("/Debate/aegis"))
    assert commands[-2] == ["/bin/tmux", "has-session", "-t", "=agora"]
    assert commands[-1] == [
        "/bin/tmux",
        "new-window",
        "-t",
        "=agora:",
        "-n",
        "aegis",
        "-c",
        "/Debate/aegis",
        "--",
        "uv",
        "run",
        "agora-launch",
        "aegis",
    ]


def test_open_window_without_tmux(monkeypatch):
    monkeypatch.setattr(terminal.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="tmux is needed"):
        terminal.open_window("agora", ["uv"], Path("/Debate"))
