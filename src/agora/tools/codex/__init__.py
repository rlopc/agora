"""Codex as an agent tool: its own CODEX_HOME, hooks in its folder and wake with `codex queue`."""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment
from agora.agents.tool import Launch, UnconfiguredWaker, WakeContext, model_flag
from agora.core import Waker
from agora.tools.codex.config import CodexOptions, generate_codex, own_note, resolve_options
from agora.tools.codex.waker import CodexQueueWaker

# Codex does not save the session until the first message; without it, the room does not know whom to wake.
FIRST_MESSAGE = "Attend to the room: call pending() and answer what concerns you."


def _thread_in_config(path: Path, participant: str) -> str:
    try:
        with path.open("rb") as f:
            config = tomllib.load(f)
    except OSError, tomllib.TOMLDecodeError:
        return ""
    for data in config.get("participants", []):
        if str(data.get("id", "")).lower() == participant:
            return str(data.get("codex_thread", ""))
    return ""


class Codex:
    name = "codex"
    default_waker = "codex_queue"
    own_keys = frozenset({"codex_home"})
    loads_parent_instructions = False
    first_run = "trust the hooks (again whenever `agora-prepare` changes them)"

    def resolve(self, data: Mapping[str, Any], defaults: Mapping[str, Any], base: Path) -> CodexOptions:
        return resolve_options(data, defaults, base)

    def generate(self, p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
        return generate_codex(p, environment, write)

    def note(self, p: ResolvedAgent, environment: Environment) -> list[str]:
        return own_note(p, environment)

    def launch(self, p: ResolvedAgent, base_env: Mapping[str, str]) -> Launch:
        return Launch(
            ["codex", *model_flag(p), FIRST_MESSAGE], p.folder, {**base_env, "CODEX_HOME": str(p.tool_options.home)}
        )

    def create_waker(self, p: ResolvedAgent, context: WakeContext) -> Waker:
        variable = f"AGORA_{p.id.upper()}_THREAD"
        thread: str | Callable[[], str] = context.env.get(variable) or p.options.get("codex_thread", "")
        home = p.tool_options.home
        if not thread and context.config_path is None and home is None:
            return UnconfiguredWaker(f"missing `codex_thread` or `homes` in agora.toml, or the {variable} variable")
        if not context.env.get(variable) and context.config_path is not None:
            # Re-read on every wake: switching Codex sessions does not require restarting the room.
            config_path = context.config_path

            def read_thread() -> str:
                return _thread_in_config(config_path, p.id)

            thread = read_thread
        return CodexQueueWaker(
            thread,
            executable=p.options.get("executable"),
            timeout=float(p.options.get("timeout", 10)),
            codex_home=str(home) if home else None,
            cwd=p.folder,
        )

    def notices(self, p: ResolvedAgent, changed: Sequence[Path]) -> list[str]:
        notices = []
        if p.tool_options.home and p.tool_options.home / "config.toml" in changed:
            notices.append(f"If this agent has not logged in yet: CODEX_HOME={p.tool_options.home} codex login")
        if any(c.name == "hooks.json" for c in changed):
            notices.append("Codex will ask to trust the hooks again when the session opens.")
        return notices

    def startup_warning(self, p: ResolvedAgent, waker: Waker) -> str | None:
        if not isinstance(waker, CodexQueueWaker) or waker.current_thread():
            return None
        if waker.codex_home:
            return f"@{p.id} cannot be woken until there is a Codex session open in {waker.cwd}"
        return f"@{p.id} cannot be woken until `codex_thread` or `homes` is set in agora.toml"
