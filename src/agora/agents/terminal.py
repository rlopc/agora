"""Open a command in its own tmux window, all in the «agora» session."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SESSION = "agora"


def open_window(title: str, argv: list[str], cwd: Path) -> None:
    """Window `title` of the «agora» tmux session; the first one creates the session, without attaching to it."""
    tmux = shutil.which("tmux")
    if not tmux:
        raise SystemExit("tmux is needed to open the room and the agents (brew install tmux or apt install tmux).")
    exists = subprocess.run([tmux, "has-session", "-t", f"={SESSION}"], capture_output=True).returncode == 0
    command = ["new-window", "-t", f"={SESSION}:"] if exists else ["new-session", "-d", "-s", SESSION]
    subprocess.run([tmux, *command, "-n", title, "-c", str(cwd), "--", *argv], check=True)
