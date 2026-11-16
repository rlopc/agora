"""Wakes an open Codex session with `codex queue`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from agora.core import Message, Participant, WakeError
from agora.paths import same_path
from agora.tools.notice import compose_notice


def resolve_codex() -> list[str]:
    launcher = shutil.which("codex")
    if launcher is None:
        raise WakeError("the `codex` executable cannot be found")
    return [launcher]


def find_codex_session(codex_home: Path, cwd: str | Path) -> str | None:
    """Id of the most recent main Codex session open in `cwd` (subagent sessions do not count)."""
    candidates = []
    for file in (Path(codex_home) / "sessions").glob("**/rollout-*.jsonl"):
        try:
            with file.open(encoding="utf-8") as f:
                meta = json.loads(f.readline())
            data = meta["payload"]
            if (
                meta.get("type") == "session_meta"
                and same_path(str(data["cwd"]), str(cwd))
                and not (isinstance(data.get("source"), dict) and "subagent" in data["source"])
            ):
                candidates.append((str(data.get("timestamp", "")), str(data["id"])))
        except OSError, ValueError, KeyError, TypeError:
            continue
    return max(candidates)[1] if candidates else None


class CodexQueueWaker:
    def __init__(
        self,
        thread: str | Callable[[], str],
        executable: Sequence[str] | None = None,
        timeout: float = 10.0,
        max_chars: int = 1000,
        codex_home: str | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        if not thread and not (cwd and codex_home):
            raise ValueError("the Codex session id or name (`codex_thread`) is missing")
        self.thread = thread
        self._executable = list(executable) if executable else None
        self.timeout = timeout
        self.max_chars = max_chars
        # Codex sessions folder; without it, `codex queue` cannot find the session.
        self.codex_home = codex_home
        # The agent's working folder: if there is no `codex_thread`, its most recent session is looked up.
        self.cwd = str(cwd) if cwd else None

    def current_thread(self) -> str:
        """`thread` can be a function, to read it from the configuration on each wake."""
        thread = self.thread() if callable(self.thread) else self.thread
        if not thread and self.cwd and self.codex_home:
            thread = find_codex_session(Path(self.codex_home), self.cwd) or ""
        return thread

    def wake(self, participant: Participant, pending: Sequence[Message]) -> None:
        thread = self.current_thread()
        if not thread:
            if self.cwd and self.codex_home:
                raise WakeError(f"there is no Codex session open in {self.cwd}")
            raise WakeError("`codex_thread` is missing in agora.toml, or `homes` in [tools.codex]")
        command = (self._executable or resolve_codex()) + [
            "queue",
            "--thread",
            thread,
            "--message",
            compose_notice(pending, self.max_chars),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "CODEX_HOME": self.codex_home} if self.codex_home else None,
            )
        except subprocess.TimeoutExpired:
            raise WakeError(f"`codex queue` did not answer in {self.timeout:g} s") from None
        except OSError as exc:
            raise WakeError(f"could not run codex: {exc}") from None
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise WakeError(detail[-1] if detail else f"codex exited with code {result.returncode}")
