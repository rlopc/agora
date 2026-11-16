"""Room HTTP API and basic web page."""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from importlib.resources import files
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from agora.core import Message, Room, RoomError, format_line
from agora.web.channel import WebChannel

_STATIC = files("agora.web") / "static"
_TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
_MAX_WAIT = 30.0
_INTERVAL = 0.5

Format = Literal["json", "text"]


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    author: str | None = None
    reply_to: int | None = None


class Read(BaseModel):
    participant: str
    ids: list[int]


class Pause(BaseModel):
    paused: bool


def _output(messages: list[Message], format: Format):
    if format == "text":
        return PlainTextResponse("\n".join(format_line(m) for m in messages))
    return [m.as_dict() for m in messages]


class OriginGuard:
    """Rejects with 403 a request whose Origin is not the room's own.

    Browsers send Origin on cross-site requests, so another web page cannot make the room act
    (a POST without a body, such as a wake, needs no preflight). A request without Origin, such as
    the agents' and the commands', is not rejected for it.
    """

    def __init__(self, app: ASGIApp, origins: Collection[str]) -> None:
        self._app = app
        self._origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origin = dict(scope["headers"]).get(b"origin")
            if origin is not None and origin.decode("latin-1") not in self._origins:
                await PlainTextResponse("Origin not allowed", status_code=403)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def create_app(
    room: Room, channel: WebChannel, hosts: list[str] | None = None, origins: list[str] | None = None
) -> FastAPI:
    app = FastAPI(title="Agent room", docs_url=None, redoc_url=None)
    app.add_middleware(OriginGuard, origins=origins or ["http://testserver"])
    # Prevents DNS rebinding attacks: only local names are served.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ["127.0.0.1", "localhost", "testserver"])

    def run(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except RoomError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (_STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/static/{name}")
    def static(name: str) -> Response:
        resource = _STATIC / name
        type = _TYPES.get(name[name.rfind(".") :])
        if "/" in name or "\\" in name or type is None or not resource.is_file():
            raise HTTPException(404)
        return Response(resource.read_bytes(), media_type=type)

    @app.get("/api/participants")
    def participants() -> dict:
        human = room.human
        return {
            "human": human.id if human else None,
            "participants": [{"id": p.id, "name": p.name, "type": p.type} for p in room.participants.values()],
        }

    @app.get("/api/messages")
    def messages(since: int = 0, limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
        return [m.as_dict() for m in room.messages(since, limit)]

    @app.get("/api/history")
    def history(since: int | None = None, limit: int = Query(20, ge=1, le=200), format: Format = "json"):
        return _output(room.history(since, limit), format)

    @app.post("/api/messages")
    def send(new: NewMessage) -> dict:
        author = new.author or (room.human.id if room.human else None)
        if author is None:
            raise HTTPException(400, "author is missing")
        result = run(room.send, author, new.text, new.reply_to)
        return {
            "message": result.message.as_dict(),
            "unknown": list(result.unknown),
            "notified": list(result.notified),
        }

    @app.get("/api/pending")
    async def pending(
        participant: str,
        since: int = 0,
        wait: float = Query(0, ge=0),
        activity: bool = False,
        watching: bool = False,
        format: Format = "json",
    ):
        """With `wait` > 0 it long-polls until there are messages or the timeout expires.

        `watching` is sent by `agora-watch`: while the wait lasts, the participant counts as
        listening and its waker does not need to step in.
        """
        result = await asyncio.to_thread(run, room.pending, participant, since, activity=activity)
        if watching:
            room.record_listen(participant, True)
        try:
            deadline = asyncio.get_running_loop().time() + min(wait, _MAX_WAIT)
            while not result and not channel.closed and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(_INTERVAL)
                result = await asyncio.to_thread(room.pending, participant, since)
        finally:
            if watching:
                room.record_listen(participant, False)
        return _output(result, format)

    @app.post("/api/read")
    def read(read: Read) -> dict:
        run(room.mark_read, read.participant, read.ids)
        return {"ok": True}

    @app.get("/api/state")
    def state() -> dict:
        return room.state()

    @app.post("/api/pause")
    def pause(pause: Pause) -> dict:
        room.set_paused(pause.paused)
        return room.state()

    @app.post("/api/wake/{participant}")
    def wake(participant: str) -> dict:
        run(room.wake_now, participant)
        return {"ok": True}

    @app.get("/api/events")
    def events() -> StreamingResponse:
        return StreamingResponse(
            channel.sse_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
