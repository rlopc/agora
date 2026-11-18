"""Configuration of an Antigravity agent in its folder: `.agents/mcp_config.json`, `.agents/hooks.json` and its rule."""

from __future__ import annotations

from pathlib import Path

from agora.agents.config import ResolvedAgent
from agora.agents.environment import Environment, hook_command, mcp_server, pending_command
from agora.agents.merge import update_json, update_text
from agora.agents.notes import note
from agora.core import PERMISSION_WAIT

HOOK = "agora"
# PreInvocation runs before every model call: the closest thing to a wake, since the room cannot wake agy.
EVENTS = ("SessionStart", "PreInvocation")


def own_note(p: ResolvedAgent, environment: Environment) -> list[str]:
    return [
        "- The room cannot wake you: its pending messages reach you before each model call; answer them in the room with `send`, not in your chat."
    ]


def generate_antigravity(p: ResolvedAgent, environment: Environment, write: bool = True) -> list[Path]:
    mcp = p.folder / ".agents" / "mcp_config.json"
    hooks = p.folder / ".agents" / "hooks.json"
    rule = p.folder / ".agents" / "rules" / "agora.md"

    def change_mcp(data: dict) -> None:
        data.setdefault("mcpServers", {})["agora"] = mcp_server(p.id, environment)

    def change_hooks(data: dict) -> None:
        own = {"type": "command", "command": f"{pending_command(p.id, environment)} --inject-steps", "timeout": 10}
        permission = {
            "type": "command",
            "command": f"{hook_command('agora-permission', p.id, environment)} --antigravity",
            # On timeout agy fails the call instead of asking: the hook must outlast the room's wait.
            "timeout": PERMISSION_WAIT + 30,
        }
        # In accept-edits mode agy asks before every command, unless a rule allows it; so does the room.
        data[HOOK] = {
            **{event: [own] for event in EVENTS},
            "PreToolUse": [{"matcher": "run_command", "hooks": [permission]}],
        }

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
