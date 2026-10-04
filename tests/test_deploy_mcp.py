"""MCP settings written by the deploy, and the connection guide that names them."""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # runs our own bash lib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tuttitrip.main import create_app
from tuttitrip.shared.config.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "deploy" / "lib.sh"
BASH = shutil.which("bash")
MAIN_URL = "https://tuttitrip-api.gburek.app/api/v1/mcp"
DEVELOP_URL = "https://tuttitrip-api-develop.gburek.app/api/v1/mcp"
METADATA = "/.well-known/oauth-protected-resource/api/v1/mcp"


def mcp_env(branch: str) -> dict[str, str]:
    """The MCP lines deploy.sh writes for a branch, as an env mapping."""
    assert BASH is not None
    script = f'. "{LIB}"; tt_mcp_env "$(tt_env "$1")"'
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, "-c", script, "mcp", branch], capture_output=True, text=True, check=True
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def test_main_and_develop_enable_the_server_with_their_own_url() -> None:
    assert mcp_env("main") == {
        "TUTTITRIP_MCP__ENABLED": "true",
        "TUTTITRIP_MCP__RESOURCE_URL": MAIN_URL,
    }
    assert mcp_env("develop") == {
        "TUTTITRIP_MCP__ENABLED": "true",
        "TUTTITRIP_MCP__RESOURCE_URL": DEVELOP_URL,
    }


@pytest.mark.parametrize("branch", ["feature/104-mcp-connect", "fix/x", "Weird__Name"])
def test_a_preview_branch_has_the_server_off(branch: str) -> None:
    assert mcp_env(branch) == {"TUTTITRIP_MCP__ENABLED": "false"}


def test_a_preview_answers_404_on_the_endpoint_and_the_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in mcp_env("feature/104-mcp-connect").items():
        monkeypatch.setenv(name, value)
    with TestClient(create_app(Settings())) as preview:
        assert preview.post("/api/v1/mcp", json={}).status_code == 404
        assert preview.get(METADATA).status_code == 404


def test_develop_env_gives_a_401_that_points_at_its_own_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in mcp_env("develop").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TUTTITRIP_AUTH0__DOMAIN", "tenant.example.com")
    host = {"Host": "tuttitrip-api-develop.gburek.app"}
    with TestClient(create_app(Settings())) as develop:
        denied = develop.post("/api/v1/mcp", json={}, headers=host)
        assert denied.status_code == 401
        assert (
            f'resource_metadata="{DEVELOP_URL.replace("/api/v1/mcp", "")}{METADATA}"'
            in (denied.headers["www-authenticate"])
        )
        metadata = develop.get(METADATA, headers=host)
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == DEVELOP_URL


def test_the_guide_names_both_urls_and_the_commands() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in (
        MAIN_URL,
        DEVELOP_URL,
        "claude mcp add --transport http tuttitrip",
        "Add custom connector",
        "Developer mode",
        "deploy/smoke-mcp.sh",
    ):
        assert needle in readme, needle
    assert (ROOT / "deploy" / "smoke-mcp.sh").is_file()
