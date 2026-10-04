"""The upload size middleware answers 413 on an oversized Content-Length."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tuttitrip.shared.uploadlimit.middleware import UploadSizeLimit


def _client() -> TestClient:
    app = FastAPI()

    @app.post("/up")
    async def up() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(UploadSizeLimit, limits=[(r"/up", 100)])
    return TestClient(app)


def test_oversized_declared_body_is_refused_before_reading() -> None:
    client = _client()
    assert client.post("/up", content=b"x" * 101).status_code == 413
    assert client.post("/up", content=b"x" * 100).status_code == 200


def test_other_paths_and_methods_are_not_limited() -> None:
    client = _client()
    assert client.get("/up").status_code == 405
    assert client.post("/other", content=b"x" * 500).status_code == 404
