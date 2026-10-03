"""Build request paths from route names, so tests survive URL prefix changes."""

from functools import cache

from fastapi import FastAPI

from tuttitrip.main import create_app


@cache
def _app() -> FastAPI:
    return create_app()


def path(name: str, **params: object) -> str:
    """URL path of the route called ``name`` (the endpoint function's name)."""
    return _app().url_path_for(name, **params)
