"""Agent tools the room supports; each implements `agora.agents.tool.Tool`."""

from collections.abc import Mapping

from agora.agents.tool import Tool
from agora.tools.antigravity import Antigravity
from agora.tools.claude import Claude
from agora.tools.codex import Codex

TOOLS: Mapping[str, Tool] = {t.name: t for t in (Claude(), Codex(), Antigravity())}
