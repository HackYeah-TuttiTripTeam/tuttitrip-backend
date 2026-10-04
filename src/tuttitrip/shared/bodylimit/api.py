"""ASGI middleware that caps the request body of chosen routes.

Starlette parses (and spools) a whole multipart body before an endpoint runs,
so a size check in the endpoint comes too late. This middleware answers 413
from the ``Content-Length`` header before reading anything, and counts the
bytes of chunked bodies while they stream in.
"""

import json
import re
from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

TOO_LARGE = "The request body is too large"


@dataclass(frozen=True, slots=True)
class BodyLimit:
    """A body cap for one method and path pattern."""

    method: str
    path: re.Pattern[str]
    max_bytes: int


async def _reject(send: Send) -> None:
    body = json.dumps({"detail": TOO_LARGE}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _Counter:
    """Wraps ``receive``: counts body bytes and cuts the stream past the limit."""

    def __init__(self, limit: int, receive: Receive) -> None:
        self.limit = limit
        self.inner = receive
        self.received = 0
        self.exceeded = False

    async def receive(self) -> Message:
        if self.exceeded:
            return {"type": "http.disconnect"}
        message = await self.inner()
        if message["type"] == "http.request":
            self.received += len(message.get("body", b""))
            if self.received > self.limit:
                self.exceeded = True
                return {"type": "http.disconnect"}
        return message


class BodyLimitMiddleware:
    """Answer 413 when a matching request's body exceeds its limit."""

    def __init__(self, app: ASGIApp, limits: Sequence[BodyLimit]) -> None:
        """Wrap ``app``.

        Args:
            app: The next ASGI app.
            limits: The caps, first match wins.
        """
        self.app = app
        self.limits = tuple(limits)

    def _limit(self, scope: Scope) -> int | None:
        for rule in self.limits:
            if scope["method"] == rule.method and rule.path.fullmatch(scope["path"]):
                return rule.max_bytes
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI call.

        Args:
            scope: ASGI scope.
            receive: ASGI receive channel.
            send: ASGI send channel.
        """
        limit = self._limit(scope) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await _reject(send)
            return

        counter = _Counter(limit, receive)

        async def guarded_send(message: Message) -> None:
            if not counter.exceeded:
                await send(message)

        try:
            await self.app(scope, counter.receive, guarded_send)
        except Exception:
            if not counter.exceeded:
                raise
        if counter.exceeded:
            await _reject(send)
