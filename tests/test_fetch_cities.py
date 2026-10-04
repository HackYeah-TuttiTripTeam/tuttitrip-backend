"""deploy/fetch-cities.sh: atomic download, last good copy kept on any problem."""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # runs our own bash script
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import override

import pytest

from tests.domains.places import sheet_fixture as fx

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "fetch-cities.sh"
BASH = shutil.which("bash")
GOOD = fx.DATA.read_bytes()
OLD = b"PK-the-last-good-copy"


@pytest.fixture
def served() -> Iterator[dict[str, tuple[int, bytes]]]:
    """A local HTTP server; the test fills ``routes`` (path -> status, body)."""
    routes: dict[str, tuple[int, bytes]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # http.server API
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Keep test output quiet."""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    routes["port"] = (server.server_port, b"")
    yield routes
    server.shutdown()
    server.server_close()


def fetch(
    served: dict[str, tuple[int, bytes]],
    target: Path,
    route: str,
    sheet_id: str = "abc123",
) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    url = f"http://127.0.0.1:{served['port'][0]}{route}"
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(target.parent),
        "TUTTITRIP_CITIES__SHEET_ID": sheet_id,
        "TT_CITIES_URL": url,
        "TT_CITIES_DIR": str(target),
        "TT_CITIES_MAX_TIME": "5",
    }
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )


@pytest.fixture
def target(tmp_path: Path) -> Path:
    directory = tmp_path / "cities"
    directory.mkdir()
    (directory / "miasta.xlsx").write_bytes(OLD)
    return directory


def only_the_old_copy(target: Path) -> bool:
    return [p.name for p in target.iterdir()] == ["miasta.xlsx"] and (
        target / "miasta.xlsx"
    ).read_bytes() == OLD


def test_a_good_download_replaces_the_copy_atomically(
    served: dict[str, tuple[int, bytes]], target: Path
) -> None:
    served["/ok"] = (200, GOOD)
    result = fetch(served, target, "/ok")
    assert result.returncode == 0, result.stderr
    assert (target / "miasta.xlsx").read_bytes() == GOOD
    assert [p.name for p in target.iterdir()] == ["miasta.xlsx"]  # no temp file left


def test_the_first_download_creates_the_directory(
    served: dict[str, tuple[int, bytes]], tmp_path: Path
) -> None:
    served["/ok"] = (200, GOOD)
    fresh = tmp_path / "new" / "cities"
    assert fetch(served, fresh, "/ok").returncode == 0
    assert (fresh / "miasta.xlsx").read_bytes() == GOOD


@pytest.mark.parametrize(
    ("status", "body", "why"),
    [
        (404, b"<html>Not found</html>", "download failed"),
        (200, b"<!doctype html><html>Sign in to continue</html>" * 40, "not an XLSX"),
        (200, b"PK", "only 2 bytes"),
        (200, b"PK\x03\x04" + b"x" * 2000, "required sheets"),
    ],
    ids=["404", "login-page", "tiny", "corrupt-zip"],
)
def test_a_bad_download_keeps_the_last_copy_and_warns(
    served: dict[str, tuple[int, bytes]],
    target: Path,
    status: int,
    body: bytes,
    why: str,
) -> None:
    served["/bad"] = (status, body)
    result = fetch(served, target, "/bad")
    assert result.returncode == 0  # the deploy never fails because of Google
    assert "WARNING: cities sheet" in result.stderr
    assert why in result.stderr
    assert only_the_old_copy(target)


def test_a_workbook_without_the_required_sheets_is_not_installed(
    served: dict[str, tuple[int, bytes]], target: Path, tmp_path: Path
) -> None:
    partial = fx.write_xlsx(tmp_path / "partial.xlsx", only=["miejsca", "miasta"])
    served["/partial"] = (200, partial.read_bytes())
    result = fetch(served, target, "/partial")
    assert result.returncode == 0
    assert "required sheets" in result.stderr
    assert only_the_old_copy(target)


def test_an_unreachable_server_keeps_the_last_copy(
    served: dict[str, tuple[int, bytes]], target: Path
) -> None:
    served["port"] = (1, b"")  # nothing listens there
    result = fetch(served, target, "/ok")
    assert result.returncode == 0
    assert "download failed" in result.stderr
    assert only_the_old_copy(target)


def test_an_empty_sheet_id_skips_the_download(
    served: dict[str, tuple[int, bytes]], target: Path
) -> None:
    served["/ok"] = (200, GOOD)
    result = fetch(served, target, "/ok", sheet_id="")
    assert result.returncode == 0
    assert "TUTTITRIP_CITIES__SHEET_ID is empty" in result.stderr
    assert only_the_old_copy(target)


def test_a_sheet_id_with_odd_characters_is_refused(
    served: dict[str, tuple[int, bytes]], target: Path
) -> None:
    result = fetch(served, target, "/ok", sheet_id="abc/../def?x=1")
    assert result.returncode == 0
    assert "unexpected characters" in result.stderr
    assert only_the_old_copy(target)
