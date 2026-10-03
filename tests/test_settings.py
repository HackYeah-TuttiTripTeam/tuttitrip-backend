"""`.env.example` must document exactly the variables `Settings` reads."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from tuttitrip.shared.config.settings import (
    ENV_NESTED_DELIMITER,
    ENV_PREFIX,
    Settings,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def expected_keys(model: type[BaseModel], prefix: str) -> set[str]:
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        key = f"{prefix}{name.upper()}"
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            keys |= expected_keys(annotation, f"{key}{ENV_NESTED_DELIMITER}")
        else:
            keys.add(key)
    return keys


def documented_keys() -> list[str]:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return [
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_settings_use_documented_prefix_and_delimiter() -> None:
    assert Settings.model_config.get("env_prefix") == ENV_PREFIX == "TUTTITRIP_"
    assert Settings.model_config.get("env_nested_delimiter") == ENV_NESTED_DELIMITER
    assert ENV_NESTED_DELIMITER == "__"


def test_env_example_has_no_duplicates() -> None:
    keys = documented_keys()
    assert len(keys) == len(set(keys))


def test_env_example_matches_settings_fields() -> None:
    assert set(documented_keys()) == expected_keys(Settings, ENV_PREFIX)


def test_env_example_parses_to_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in documented_keys():
        monkeypatch.delenv(key, raising=False)
    from_file = Settings(_env_file=ENV_EXAMPLE)
    assert from_file == Settings(_env_file=None)
