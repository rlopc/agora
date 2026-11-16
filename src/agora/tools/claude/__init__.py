"""Claude Code as an agent tool: configuration in its folder and wake through the concierge."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment
from agora.agents.tool import Launch, WakeContext, model_flag
from agora.core import Waker
from agora.tools.claude.config import generate_claude, own_note
from agora.tools.claude.waker import ConciergeWaker


class Claude:
    name = "claude"
    default_waker = "concierge"
    own_keys = frozenset()
    loads_parent_instructions = True  # the CLAUDE.md files of parent folders

    def resolve(self, data: Mapping[str, Any], defaults: Mapping[str, Any], base: Path) -> None:
        return None

    def generate(self, p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
        return generate_claude(p, environment, write)

    def note(self, p: ResolvedAgent, environment: Environment) -> list[str]:
        return own_note(p, environment)

    def launch(self, p: ResolvedAgent, base_env: Mapping[str, str]) -> Launch:
        # The CLAUDE* variables of a Claude Code session that runs this command would confuse the new one.
        env = {k: v for k, v in base_env.items() if not k.upper().startswith("CLAUDE")}
        return Launch(["claude", *model_flag(p), "--permission-mode", "acceptEdits"], p.folder, env)

    def create_waker(self, p: ResolvedAgent, context: WakeContext) -> Waker:
        return ConciergeWaker(
            cwd=p.folder,
            executable=p.options.get("executable"),
            model=p.options.get("concierge_model", "haiku"),
            timeout=float(p.options.get("timeout", 180)),
            listening=context.listening,
        )

    def notices(self, p: ResolvedAgent, changed: Sequence[Path]) -> list[str]:
        return []

    def startup_warning(self, p: ResolvedAgent, waker: Waker) -> str | None:
        return None
