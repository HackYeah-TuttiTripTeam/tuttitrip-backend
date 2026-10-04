"""The body limit answers 413 before the body is read, also for chunked uploads."""

import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from tuttitrip.main import MULTIPART_OVERHEAD_BYTES, create_app
from tuttitrip.shared.bodylimit.api import (
    BodyLimit,
    BodyLimitMiddleware,
    Message,
    Receive,
    Scope,
    Send,
)
from tuttitrip.shared.config.settings import PhotoSettings, Settings

LIMIT = 100
RULE = BodyLimit("POST", re.compile(r"/up"), LIMIT)


def _call(
    app: BodyLimitMiddleware, headers: list[tuple[bytes, bytes]], chunks: list[bytes]
) -> tuple[list[Message], int]:
    sent: list[Message] = []
    reads = 0
    queue = list(chunks)

    async def receive() -> Message:  # ruff: ignore[unused-async] ASGI callable
        nonlocal reads
        reads += 1
        if not queue:
            return {"type": "http.disconnect"}
        body = queue.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message: Message) -> None:  # ruff: ignore[unused-async] ASGI callable
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/up",
        "headers": headers,
    }
    asyncio.run(app(scope, receive, send))
    return sent, reads


async def _reading_app(scope: Scope, receive: Receive, send: Send) -> None:
    del scope
    while (await receive()).get("more_body"):
        pass
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_declared_oversize_is_413_without_reading_the_body() -> None:
    app = BodyLimitMiddleware(_reading_app, [RULE])
    sent, reads = _call(app, [(b"content-length", b"101")], [b"x" * 101])
    assert sent[0]["status"] == 413
    assert reads == 0


def test_chunked_oversize_is_413() -> None:
    app = BodyLimitMiddleware(_reading_app, [RULE])
    sent, _ = _call(app, [], [b"x" * 60, b"x" * 60, b"x" * 60])
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]


def test_body_within_the_limit_passes() -> None:
    app = BodyLimitMiddleware(_reading_app, [RULE])
    sent, _ = _call(app, [(b"content-length", b"100")], [b"x" * 100])
    assert sent[0]["status"] == 200


def test_other_paths_are_not_limited() -> None:
    app = BodyLimitMiddleware(_reading_app, [BodyLimit("POST", re.compile(r"/x"), 1)])
    sent, _ = _call(app, [(b"content-length", b"500")], [b"x" * 500])
    assert sent[0]["status"] == 200


@pytest.mark.parametrize("extra", [0, 1])
def test_photo_upload_limit_comes_from_settings(extra: int) -> None:
    photos = PhotoSettings(max_image_bytes=1000, max_thumbnail_bytes=500)
    app = create_app(Settings(_env_file=None, photos=photos))
    cap = 1500 + MULTIPART_OVERHEAD_BYTES
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/trips/00000000-0000-0000-0000-000000000000/photos",
            content=b"x" * (cap + extra),
            headers={"content-type": "multipart/form-data; boundary=b"},
        )
    # Within the cap the body reaches the multipart parser (400: not a real form);
    # above it the middleware answers 413 first.
    assert response.status_code == (400 if extra == 0 else 413)
