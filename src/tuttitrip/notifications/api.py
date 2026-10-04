"""Notification endpoints (the list, counter and marking arrive in later issues)."""

from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])
