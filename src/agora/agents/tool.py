"""What each agent tool (Claude Code, Codex…) implements so the room can prepare, launch and wake it."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment
from agora.core import Message, Participant, Waker, WakeError


@dataclass(frozen=True)
class Launch:
    argv: list[str]
    cwd: Path
    env: dict[str, str]


def model_flag(p: ResolvedAgent) -> list[str]:
    """`--model`, as the three tools spell it; without a model, the tool's default."""
    return ["--model", p.model] if p.model else []


@dataclass(frozen=True)
class WakeContext:
    env: Mapping[str, str]
    config_path: Path | None
    # Whether the participant already watches the room (`agora-watch`); bound to the room once it exists.
    listening: Callable[[], bool]


class UnconfiguredWaker:
    """Makes missing configuration visible in the room, instead of failing at startup."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def wake(self, participant: Participant, pending: Sequence[Message]) -> None:
        raise WakeError(self.reason)


class Tool(Protocol):
    name: str

    @property
    def default_waker(self) -> str | None:
        """None: the room cannot wake it."""
        ...

    # Keys of `[[participants]]` the tool interprets; they do not go into `Participant.options`.
    own_keys: frozenset[str]
    # The tool loads the instructions of parent folders: its agent cannot be inside another's folder.
    loads_parent_instructions: bool

    def resolve(self, data: Mapping[str, Any], defaults: Mapping[str, Any], base: Path) -> Any:
        """The tool's own options (`ResolvedAgent.tool_options`) from the participant and `[tools.<name>]`."""
        ...

    def generate(self, p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
        """Writes the agent's configuration; returns the files that change (all of them, if `write` is False)."""
        ...

    def note(self, p: ResolvedAgent, environment: Environment) -> list[str]:
        """The tool's lines in the agent's note; `notes.note` adds the common one."""
        ...

    def launch(self, p: ResolvedAgent, base_env: Mapping[str, str]) -> Launch: ...

    def create_waker(self, p: ResolvedAgent, context: WakeContext) -> Waker: ...

    def notices(self, p: ResolvedAgent, changed: Sequence[Path]) -> list[str]:
        """What the user should know after preparing the agent."""
        ...

    def startup_warning(self, p: ResolvedAgent, waker: Waker) -> str | None: ...
