"""Room HTTP API and basic web page."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Collection
from importlib.resources import files
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from agora.core import (
    PERMISSION_WAIT,
    Message,
    PermissionAnswered,
    PermissionNotFound,
    Permissions,
    Room,
    RoomError,
    format_line,
)
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
    blind: bool = False


class CloseRound(BaseModel):
    participant: str | None = None


class Read(BaseModel):
    participant: str
    ids: list[int]


class Pause(BaseModel):
    paused: bool


class NewPermission(BaseModel):
    participant: str
    summary: str = Field(min_length=1, max_length=1000)
    key: str | None = Field(None, max_length=200)


class DoneCall(BaseModel):
    participant: str
    key: str = Field(max_length=200)


class Decision(BaseModel):
    allow: bool


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


def _cookie(request: Request) -> str:
    """Cookies are shared by every port of a host: each room keeps its own."""
    return f"agora-{request.url.port or 80}"


def create_app(
    room: Room,
    channel: WebChannel,
    hosts: list[str] | None = None,
    origins: list[str] | None = None,
    token: str | None = None,
) -> FastAPI:
    """Without `token`, permission requests can only be answered in the agents' terminals."""
    app = FastAPI(title="Agent room", docs_url=None, redoc_url=None)
    app.add_middleware(OriginGuard, origins=origins or ["http://testserver"])
    # Prevents DNS rebinding attacks: only local names are served.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ["127.0.0.1", "localhost", "testserver"])

    permissions = Permissions(room.participants, room.channels)

    def run(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionNotFound:
            raise HTTPException(404, "unknown permission request") from None
        except PermissionAnswered as exc:
            raise HTTPException(409, str(exc)) from None
        except RoomError as exc:
            raise HTTPException(400, str(exc)) from None

    def valid(given: str | None) -> bool:
        return token is not None and given is not None and secrets.compare_digest(given, token)

    def can_decide(request: Request) -> bool:
        return valid(request.cookies.get(_cookie(request)))

    async def wait_for(result, fetch: Callable, seconds: float, request: Request | None = None):
        """Calls `fetch` every `_INTERVAL` until it returns something, time runs out, the server
        stops or, with `request`, the client goes away."""
        deadline = asyncio.get_running_loop().time() + seconds
        while not result and not channel.closed and asyncio.get_running_loop().time() < deadline:
            if request is not None and await request.is_disconnected():
                break
            await asyncio.sleep(_INTERVAL)
            result = await asyncio.to_thread(fetch)
        return result

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, token: str | None = None) -> Response:
        """The link with the token, printed by the room, leaves it in a cookie and out of the address bar."""
        if token is not None:
            response = RedirectResponse("/", status_code=303)
            if valid(token):
                response.set_cookie(_cookie(request), token, httponly=True, samesite="strict")
            return response
        return HTMLResponse((_STATIC / "index.html").read_text(encoding="utf-8"))

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
    def messages(since: int = 0, limit: int = Query(200, ge=1, le=1000), participant: str | None = None) -> list[dict]:
        return [m.as_dict() for m in run(room.messages, since, limit, participant)]

    @app.get("/api/history")
    def history(
        since: int | None = None,
        limit: int = Query(20, ge=1, le=200),
        format: Format = "json",
        participant: str | None = None,
    ):
        """Without `participant`, the user's view; with it, what that participant may see."""
        return _output(run(room.history, since, limit, participant), format)

    @app.post("/api/messages")
    def send(new: NewMessage) -> dict:
        author = new.author or (room.human.id if room.human else None)
        if author is None:
            raise HTTPException(400, "author is missing")
        result = run(room.send, author, new.text, new.reply_to, blind=new.blind)
        return {
            "message": result.message.as_dict(),
            "unknown": list(result.unknown),
            "notified": list(result.notified),
            "held": list(result.held),
        }

    @app.post("/api/round/close")
    def close_round(close: CloseRound | None = None) -> dict:
        by = (close.participant if close else None) or (room.human.id if room.human else None)
        if by is None:
            raise HTTPException(400, "participant is missing")
        run(room.close_round, by)
        return room.state()

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
            result = await wait_for(result, lambda: room.pending(participant, since), min(wait, _MAX_WAIT))
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

    @app.post("/api/permissions")
    async def ask(new: NewPermission, request: Request) -> dict:
        """For the agent's hook: waits on the same connection until the user answers in the room.

        If the hook stops waiting (the user answered in the terminal or the tool killed it), the
        connection closes and the request is marked as answered there.
        """
        id = run(permissions.request, new.participant, new.summary, new.key).id
        try:
            answered = await wait_for(None, lambda: _answered(permissions, id), PERMISSION_WAIT, request)
        finally:
            permissions.release(id)
        return {"decision": {"allowed": "allow", "denied": "deny"}.get(answered.status) if answered else None}

    @app.post("/api/permissions/done")
    def done(call: DoneCall) -> dict:
        """For the hooks that run after a tool call: a request still pending was answered in the terminal."""
        permissions.done(call.participant, call.key)
        return {"ok": True}

    @app.get("/api/permissions")
    def permission_list(request: Request) -> dict:
        return {"can_decide": can_decide(request), "permissions": [p.as_dict() for p in permissions.list()]}

    @app.post("/api/permissions/{id}")
    def decide(id: int, decision: Decision, request: Request) -> dict:
        if not can_decide(request):
            raise HTTPException(403, "open the link the room printed when it started to answer from this page")
        return run(permissions.decide, id, decision.allow).as_dict()

    @app.get("/api/events")
    def events() -> StreamingResponse:
        return StreamingResponse(
            channel.sse_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _answered(permissions: Permissions, id: int):
    item = permissions.get(id)
    return item if item.status != "pending" else None
