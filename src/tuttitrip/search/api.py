"""Search endpoints (query embedding comes from the worker; none exposed yet)."""

from fastapi import APIRouter

router = APIRouter(prefix="/search", tags=["search"])
