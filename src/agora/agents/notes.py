"""The tool's note for the agent: who it is, how the room wakes it and its subagents."""

from __future__ import annotations

from collections.abc import Sequence

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment


def note(p: ResolvedAgent, environment: Environment, own: Sequence[str]) -> str:
    """The common line plus the tool's own ones (`Tool.note`)."""
    return "\n".join([f"- You are the participant `{p.id}` of the room (MCP `agora`, {environment.url}).", *own])
