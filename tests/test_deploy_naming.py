"""Branch -> environment naming used by deploy/ (cleanup relies on it)."""

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # runs our own bash lib
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "deploy" / "lib.sh"
BASH = shutil.which("bash")


def naming(branch: str) -> list[str]:
    assert BASH is not None
    script = (
        f'. "{LIB}"; e=$(tt_env "$1"); '
        'printf "%s\\n" "$e" "$(tt_hostname "$e")" "$(tt_database "$e")"'
    )
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [BASH, "-c", script, "naming", branch],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("main", ["main", "tuttitrip-api.gburek.app", "tuttitrip_main"]),
        (
            "develop",
            ["develop", "tuttitrip-api-develop.gburek.app", "tuttitrip_develop"],
        ),
        (
            "feature/cos tam",
            [
                "feature-cos-tam",
                "tuttitrip-api-feature-cos-tam.gburek.app",
                "tuttitrip_br_feature_cos_tam",
            ],
        ),
        (
            "Fix/--Weird__Name--",
            [
                "fix-weird-name",
                "tuttitrip-api-fix-weird-name.gburek.app",
                "tuttitrip_br_fix_weird_name",
            ],
        ),
    ],
)
def test_branch_naming(branch: str, expected: list[str]) -> None:
    assert naming(branch) == expected


def test_hostname_label_is_at_most_63_chars() -> None:
    env, host, _ = naming("feature/" + "x" * 100 + "-tail")
    label = host.split(".", 1)[0]
    assert len(label) <= 63
    assert not label.endswith("-")
    assert env == label.removeprefix("tuttitrip-api-")
