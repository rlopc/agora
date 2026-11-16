"""Local web channel: HTTP API, live events (SSE) and the user interface."""

from agora.web.app import create_app
from agora.web.channel import WebChannel

__all__ = ["WebChannel", "create_app"]
