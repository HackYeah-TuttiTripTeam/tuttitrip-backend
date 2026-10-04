"""Pure ASGI middleware: refuse a too large ``Content-Length`` up front."""

import json
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type App = Callable[[Scope, Receive, Send], Awaitable[None]]

STATUS_TOO_LARGE = 413


class UploadSizeLimit:
    """Answer 413 when ``Content-Length`` of a POST to a matching path is over a limit.

    The multipart parser runs before the endpoint, so the endpoint cannot refuse
    early; this checks the header first. A body without ``Content-Length``
    (chunked) passes and is bounded by the proxy and by the endpoint's own read.
    """

    def __init__(self, app: App, limits: list[tuple[str, int]]) -> None:
        """Remember the rules.

        Args:
            app: The wrapped ASGI app.
            limits: ``(path regex, maximum Content-Length in bytes)`` pairs.
        """
        self._app = app
        self._limits = [(re.compile(pattern), size) for pattern, size in limits]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the app, or answer 413 for an oversized declared body.

        Args:
            scope: ASGI scope.
            receive: ASGI receive channel.
            send: ASGI send channel.
        """
        if scope["type"] == "http" and scope["method"] == "POST":
            limit = next(
                (size for rx, size in self._limits if rx.fullmatch(scope["path"])),
                None,
            )
            declared = dict(scope["headers"]).get(b"content-length", b"")
            if limit is not None and declared.isdigit() and int(declared) > limit:
                body = json.dumps({"detail": "Request body too large"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": STATUS_TOO_LARGE,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self._app(scope, receive, send)
