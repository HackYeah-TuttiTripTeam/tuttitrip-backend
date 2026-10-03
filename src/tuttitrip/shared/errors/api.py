"""Error handlers shared by every route."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_ECHOES = ("input", "ctx")


def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Answer 422 in FastAPI's usual shape without echoing request values.

    Pydantic puts the offending value in ``input`` and parameters (possibly
    derived from it) in ``ctx``; both can carry secrets or large pasted texts.

    Args:
        _request: The failed request (unused).
        exc: The validation error (any other exception is re-raised).

    Returns:
        The 422 response with ``detail`` items reduced to ``type``, ``loc``, ``msg``.
    """
    if not isinstance(exc, RequestValidationError):
        raise exc
    detail: list[dict[str, Any]] = [
        {k: v for k, v in error.items() if k not in _ECHOES} for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


def register_error_handlers(app: FastAPI) -> None:
    """Register the shared handlers on the application.

    Args:
        app: The FastAPI app.
    """
    app.add_exception_handler(RequestValidationError, validation_error_handler)
