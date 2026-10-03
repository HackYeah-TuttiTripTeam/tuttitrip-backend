"""Architecture rules enforced with pytest-archon."""

from pytest_archon import archrule


def test_domain_does_not_depend_on_outer_layers() -> None:
    """The domain layer stays pure: no agents, no pydantic-ai."""
    (
        archrule(
            "domain is independent",
            comment="Domain models must not depend on agents or the AI framework.",
        )
        .match("tuttitrip.domain*")
        .should_not_import("tuttitrip.agents*", "pydantic_ai*")
        .check("tuttitrip")
    )
