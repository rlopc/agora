"""Configuration of an Antigravity agent in its folder: `.agents/mcp_config.json`, `.agents/hooks.json` and its rule."""

from __future__ import annotations

from pathlib import Path

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment, mcp_server, pending_command
from agora.agents.merge import update_json, update_text
from agora.agents.notes import note

HOOK = "agora"
# PreInvocation runs before every model call: the closest thing to a wake, since the room cannot wake agy.
EVENTS = ("SessionStart", "PreInvocation")


def own_note(p: ResolvedAgent, environment: Environment) -> list[str]:
    return [
        "- The room cannot wake you: its pending messages reach you before each model call; handle them with `pending()`."
    ]


def generate_antigravity(p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
    mcp = p.folder / ".agents" / "mcp_config.json"
    hooks = p.folder / ".agents" / "hooks.json"
    rule = p.folder / ".agents" / "rules" / "agora.md"

    def change_mcp(data: dict) -> None:
        data.setdefault("mcpServers", {})["agora"] = mcp_server(p.id, environment)

    def change_hooks(data: dict) -> None:
        own = {"type": "command", "command": f"{pending_command(p.id, environment)} --inject-steps", "timeout": 10}
        data[HOOK] = {event: [own] for event in EVENTS}

    # Files in `rules/` without a valid trigger are silently ignored.
    text = (
        f"---\ntrigger: always_on\ndescription: Agora room\n---\n\n{note(p, environment, own_note(p, environment))}\n"
    )

    changed = []
    if update_json(mcp, change_mcp, write):
        changed.append(mcp)
    if update_json(hooks, change_hooks, write):
        changed.append(hooks)
    if update_text(rule, text, write):
        changed.append(rule)
    return changed
